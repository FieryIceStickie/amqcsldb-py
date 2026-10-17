import asyncio
import logging
from collections.abc import AsyncGenerator, AsyncIterator, Iterable, Iterator, Sequence
from contextlib import aclosing, nullcontext
from contextvars import ContextVar
from functools import cached_property
from os import PathLike
from pathlib import Path
from types import TracebackType
from typing import Any, Self, TextIO

import niquests
from attrs import define, field
from attrs.validators import gt, le

from amqcsl.bundles._core import Items, StreamingBundle
from amqcsl.bundles._misc import (
    AddAudioBundle,
    AuthBundle,
    Bundle,
    CreateAlbumBundle,
    CreateGroupBundle,
    CreateListBundle,
    CSLGroups,
    CSLLists,
    GetArtistBundle,
    GetMetadataBundle,
    GetSongBundle,
    GroupBundle,
    GroupDeleteBundle,
    GroupEditBundle,
    ImportAudioBundle,
    ListBundle,
    ListDeleteBundle,
    ListEditBundle,
    LogoutBundle,
    SongAddMetadataBundle,
    SongDeleteBundle,
    SongDeleteMetadataBundle,
    SongEditBundle,
    TrackAddMetadataBundle,
    TrackDeleteMetadataBundle,
    TrackEditBundle,
)
from amqcsl.bundles._pages import (
    IterArtistsBundle,
    IterSongsBundle,
    IterTracksBundle,
)
from amqcsl.clients._http_utils import MultipartUpload, RequestSemaphore, reject_redirect, request_options
from amqcsl.exceptions import ClientDoesNotExistError
from amqcsl.objects._db_types import (
    AlbumTrack,
    CSLArtist,
    CSLArtistSample,
    CSLExtraMetadata,
    CSLGroup,
    CSLList,
    CSLMetadata,
    CSLSong,
    CSLSongArtistCredit,
    CSLSongSample,
    CSLTrack,
    CSLTrackRef,
    Metadata,
    NewSong,
    TrackPutArtistCredit,
)
from amqcsl.objects._obj_consts import TrackType
from amqcsl.queues._core import GroupAbortedError, GroupState, Queue, QueueGroup, save_recovery

from ._client_consts import (
    DB_URL,
    DEFAULT_SESSION_PATH,
)

logger = logging.getLogger('amqcsl.client')


@define
class QueueRunState:
    """Share scheduling decisions across workers in one queue run.

    Attributes:
        pending: Groups waiting to be assigned to a worker.
        stop_if_err: Whether request errors stop scheduling new groups.
        failures: Errors reported by individual bundles.
        stopped: Whether workers should stop taking groups.
    """

    pending: Iterator[QueueGroup]
    stop_if_err: bool
    failures: list[Exception] = field(factory=list)
    stopped: bool = False


@define
class AsyncDBClient:
    """Async client for accessing the db.
    If session cookie is valid, username and password may be omitted.
    """

    #: DB username
    username: str | None = None
    #: DB password
    password: str | None = None
    #: Filepath to look for/store session cookie in, defaults to amq_session.txt
    session_path: Path = field(default=Path(DEFAULT_SESSION_PATH), converter=Path)
    _client: niquests.AsyncSession | None = field(default=None, init=False, repr=False)

    #: Maximum batch size when querying db
    max_batch_size: int = field(default=100, validator=gt(0))
    #: Maximum number of queries when iterating
    max_query_size: int = field(default=1500, validator=gt(0))
    _max_request_count: int = field(default=15, alias='max_request_count', validator=[gt(0), le(50)])

    _lists: CSLLists = field(factory=dict)
    _groups: CSLGroups = field(factory=dict)

    def is_sync(self) -> bool:
        """Check for if client is synchronous

        Returns:
            False
        """
        return False

    @property
    def max_request_count(self) -> int:
        """Maximum concurrent requests, adjustable from 1 to 50 while the client is running."""
        return self._max_request_count

    @max_request_count.setter
    def max_request_count(self, value: int) -> None:
        """Update the limit; active requests finish before a lower limit takes effect."""
        self._max_request_count = value
        self._request_semaphore.resize(value)

    @cached_property
    def _request_semaphore(self) -> RequestSemaphore:
        return RequestSemaphore(self.max_request_count)

    @property
    def client(self) -> niquests.AsyncSession:
        """Underlying niquests.AsyncSession"""
        if self._client is None:
            raise ClientDoesNotExistError
        return self._client

    @cached_property
    def _queue_state(self) -> ContextVar[GroupState | None]:
        """Carry group failure state through nested request tasks, scoped to this client."""
        return ContextVar('queue_group', default=None)

    async def _send_request(self, req: niquests.PreparedRequest) -> niquests.Response:
        """Send within the concurrency limit using the shared transport policy."""
        async with self._request_semaphore:
            state = self._queue_state.get()
            if state is not None and state.failed:
                raise GroupAbortedError('Another bundle in this group failed before this request started')
            try:
                body = req.body
                with body.opened() if isinstance(body, MultipartUpload) else nullcontext():
                    response = await self.client.send(req, **request_options(req))
                reject_redirect(response)
                return response
            except Exception:
                if state is not None:
                    state.failed = True
                raise

    async def _send_requests(self, requests: Iterable[niquests.PreparedRequest]) -> list[niquests.Response]:
        """Drain every request task before propagating a multi-request bundle failure.

        Args:
            requests: Prepared requests emitted together by a bundle.

        Returns:
            Responses in request insertion order.
        """
        tasks = [asyncio.create_task(self._send_request(request)) for request in requests]
        try:
            replies = await asyncio.gather(*tasks, return_exceptions=True)
            errors = [reply for reply in replies if isinstance(reply, BaseException)]
            if errors:
                raise next((error for error in errors if not isinstance(error, GroupAbortedError)), errors[0])
            return [reply for reply in replies if isinstance(reply, niquests.Response)]
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def process[R](self, bundle: Bundle[R]) -> R:
        """Processes a bundle (Mainly for internal use)

        Args:
            bundle: Bundle

        Returns:
            Output of the bundle
        """
        logger.debug(f'Processing {type(bundle)}')
        client = self.client
        g = bundle.vendor(client)
        res: niquests.Response | Sequence[niquests.Response] | None = None
        try:
            while True:
                try:
                    req = g.send(res)  # type: ignore[reportArgumentType]
                except StopIteration as e:
                    return e.value
                match req:
                    case Items():
                        raise TypeError('Use a streaming iterator or .collect() for streaming bundles')
                    case niquests.PreparedRequest():
                        res = await self._send_request(req)
                    case reqs:
                        res = await self._send_requests(reqs)
        finally:
            g.close()

    async def _process_queued_bundle(
        self,
        queue: Queue[Any],
        bundle: Bundle[Any],
        group_state: GroupState,
        run: QueueRunState,
    ) -> None:
        """Execute a queued bundle and remove it from the queue on success.

        On failure, retain the bundle, record the error, and mark its group as
        failed so unsent requests are skipped. Stop scheduling new groups when
        required by the error policy. Already-aborted bundles remain queued.

        Args:
            queue: Queue containing the pending bundle.
            bundle: Leaf bundle to execute.
            group_state: Failure state shared by requests in this group.
            run: Scheduling and error state shared by the workers.
        """
        try:
            await self.process(bundle)
            queue.complete(bundle)
        except GroupAbortedError:
            pass
        except asyncio.CancelledError:
            group_state.failed = True
            run.stopped = True
            raise
        except Exception as error:  # noqa: BLE001 -- collect child errors without cancelling in-flight siblings
            group_state.failed = True
            run.failures.append(error)
            logger.error('Queued bundle %r failed: %r', bundle, error)
            if run.stop_if_err or not isinstance(error, niquests.exceptions.RequestException):
                run.stopped = True

    async def _process_queue_group(
        self,
        queue: Queue[Any],
        group: QueueGroup,
        run: QueueRunState,
    ) -> None:
        """Start a group's child tasks in insertion order with shared failure state.

        Args:
            queue: Queue to update as children complete.
            group: Snapshot of the group's pending bundles.
            run: Scheduling and error state shared by the workers.
        """
        state = GroupState()
        token = self._queue_state.set(state)
        children: list[asyncio.Task[None]] = []
        try:
            async with asyncio.TaskGroup() as tasks:
                for bundle in group.bundles:
                    children.append(tasks.create_task(self._process_queued_bundle(queue, bundle, state, run)))
            # TaskGroup treats individually cancelled children as successful exits
            if any(child.cancelled() for child in children):
                raise asyncio.CancelledError
        finally:
            self._queue_state.reset(token)

    async def _queue_worker(
        self,
        queue: Queue[Any],
        run: QueueRunState,
    ) -> None:
        """Take groups until work ends or a failure stops scheduling.

        Args:
            queue: Queue whose groups this worker processes.
            run: Iterator and scheduling state shared by the workers.
        """
        while not run.stopped:
            group = next(run.pending, None)
            if group is None:
                return
            await self._process_queue_group(queue, group, run)

    async def _process_queue_groups(
        self,
        queue: Queue[Any],
        groups: tuple[QueueGroup, ...],
        *,
        stop_if_err: bool,
    ) -> None:
        """Run the configured workers and drain them before propagating errors.

        Args:
            queue: Queue supplying the worker count and completion updates.
            groups: Ordered execution snapshot.
            stop_if_err: Whether request errors stop scheduling and propagate.

        Raises:
            ValueError: A nonempty queue supplies fewer than one worker.
            ExceptionGroup: Multiple bundle errors need to propagate.
        """
        run = QueueRunState(iter(groups), stop_if_err)
        workers: list[asyncio.Task[None]] = []
        try:
            count = queue.num_workers(self.max_request_count)
            if groups and count < 1:
                raise ValueError('A nonempty queue must provide at least one worker')
            workers = [asyncio.create_task(self._queue_worker(queue, run)) for _ in range(min(count, len(groups)))]
            await asyncio.gather(*workers)
            propagated = [
                error
                for error in run.failures
                if stop_if_err or not isinstance(error, niquests.exceptions.RequestException)
            ]
            if len(propagated) == 1:
                raise propagated[0]
            if propagated:
                raise ExceptionGroup('Queued bundles failed', propagated)
        finally:
            for task in workers:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*workers, return_exceptions=True)

    async def _finish_queue(
        self,
        queue: Queue[Any],
        *,
        changes_lists: bool,
        changes_groups: bool,
        save_to: TextIO | None,
        error: BaseException | None,
    ) -> None:
        """Refresh changed caches and save recovery output without hiding errors.

        Args:
            queue: Remaining work to save.
            changes_lists: Whether this run could change the list cache.
            changes_groups: Whether this run could change the group cache.
            save_to: Output stream, or None for the current stdout.
            error: Execution error already being propagated, if any.

        Raises:
            ExceptionGroup: Execution and cache refresh both fail.
        """
        # Async caches cannot refresh on property access, clear them before refreshing
        # so cancellation or a refresh error leaves no stale data
        if changes_lists:
            self._lists = {}
        if changes_groups:
            self._groups = {}
        try:
            if not isinstance(error, asyncio.CancelledError):
                if changes_lists:
                    await self.refresh_lists()
                if changes_groups:
                    await self.refresh_groups()
        except BaseException as cache_error:
            if isinstance(cache_error, asyncio.CancelledError):
                error = cache_error
                raise
            if error is None:
                error = cache_error
                raise
            if isinstance(error, Exception) and isinstance(cache_error, Exception):
                error = ExceptionGroup('Queue processing and cache refresh failed', [error, cache_error])
                raise error from None
            logger.exception('Could not refresh changed queue caches')
        finally:
            save_recovery(queue, save_to, error)

    async def process_queue(
        self,
        queue: Queue[Any],
        *,
        save_to: TextIO | None = None,
        stop_if_err: bool = True,
    ) -> None:
        """Process concurrent groups and save unfinished work.

        Successful children are removed immediately. A failed group skips unsent
        requests while requests already in flight finish. Cancellation drains all
        workers before saving the remaining queue and propagating.

        Args:
            queue: Independent queue to process and update.
            save_to: Recovery output stream, or None for the current stdout. Written
                at its current position and left open, including after success.
            stop_if_err: Stop taking new groups and propagate request errors when
                True. When False, log request errors and continue with other groups.
                Programming errors always propagate.

        Raises:
            RuntimeError: The queue is already processing.
            ExceptionGroup: Multiple execution, cache refresh, or save errors occur.
        """
        with queue._processing() as groups:  # pyright: ignore[reportPrivateUsage]
            changes_lists, changes_groups = queue.changes_lists(), queue.changes_groups()
            logger.info('Processing %s queued edits', len(queue))
            error: BaseException | None = None
            try:
                await self._process_queue_groups(queue, groups, stop_if_err=stop_if_err)
            except BaseException as caught:
                error = caught
                raise
            finally:
                await self._finish_queue(
                    queue,
                    changes_lists=changes_lists,
                    changes_groups=changes_groups,
                    save_to=save_to,
                    error=error,
                )

    # --- Initialization ---

    async def __aenter__(self) -> Self:
        logger.info('Creating client')
        self._client = niquests.AsyncSession(base_url=DB_URL, timeout=(10, 30))
        try:
            logger.info('Verifying permissions')
            bundle = AuthBundle(self.username, self.password, self.session_path)
            await self.process(bundle)
            await self.refresh_lists()
            await self.refresh_groups()
        except BaseException:
            await self._client.close()
            raise
        else:
            return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ):
        try:
            if exc_type is None:
                logger.info('Closing client')
            else:
                if isinstance(exc_val, niquests.exceptions.HTTPError) and exc_val.response is not None:
                    try:
                        error_data = exc_val.response.json()
                        logger.error('JSON given with error, check logs', extra={'data': error_data})
                    except ValueError:
                        logger.error('No JSON given with error')
                logger.error('Exception encountered, closing client')

        finally:
            if self._client:
                await self._client.close()

    async def logout(self):
        """Logout the client

        Raises:
            AMQCSLError: niquests client doesn't exist yet
        """
        bundle = LogoutBundle(self.session_path)
        await self.process(bundle)

    # --- Batch DB reading ---

    @property
    def lists(self) -> CSLLists:
        """Dictionary of user's lists, indexed by name"""
        return self._lists

    async def refresh_lists(self) -> None:
        """Refresh client.lists (usually done automatically)"""
        bundle = ListBundle()
        self._lists = await self.process(bundle)

    @property
    def groups(self) -> CSLGroups:
        """Dictionary of DB groups, indexed by name"""
        return self._groups

    async def refresh_groups(self) -> None:
        """Refresh client.groups (usually done automatically)"""
        bundle = GroupBundle()
        self._groups = await self.process(bundle)

    async def _process_stream[T](
        self,
        bundle: StreamingBundle[T],
    ) -> AsyncGenerator[T, None]:
        """Execute HTTP events concurrently and expose item events in stream order."""
        logger.debug(f'Processing {type(bundle)}')
        vendor = bundle.vendor(self.client)
        reply: niquests.Response | Sequence[niquests.Response] | None = None
        try:
            while True:
                try:
                    event = vendor.send(reply)
                except StopIteration:
                    return
                match event:
                    case Items(values=values):
                        for item in values:
                            yield item
                        reply = None
                    case niquests.PreparedRequest():
                        reply = await self._send_request(event)
                    case _:
                        async with asyncio.TaskGroup() as tg:
                            tasks = [tg.create_task(self._send_request(request)) for request in event]
                        reply = [task.result() for task in tasks]
        finally:
            vendor.close()

    async def iter_tracks(
        self,
        search_term: str = '',
        *,
        groups: Iterable[CSLGroup] = (),
        active_list: CSLList | None = None,
        missing_audio: bool = False,
        missing_info: bool = False,
        from_active_list: bool | None = None,
        batch_size: int = 50,
    ) -> AsyncIterator[CSLTrack]:
        """Iterate over tracks matching the search parameters

        Args:
            search_term: Search term
            groups: List of groups to restrict to, leave empty if no restriction
            active_list: List to restrict search by
            missing_audio: Restrict to songs without audio
            missing_info: Restrict to songs missing info
            from_active_list: Restrict to songs from active list, defaults to True if active_list is given and False otherwise
            batch_size: How many tracks to query at once (page size)

        Yields:
            CSLTrack
        """
        bundle = IterTracksBundle.from_client(
            self,
            search_term=search_term,
            groups=groups,
            active_list=active_list,
            missing_audio=missing_audio,
            missing_info=missing_info,
            from_active_list=from_active_list,
            batch_size=batch_size,
        )
        async with aclosing(self._process_stream(bundle)) as items:
            async for item in items:
                yield item

    async def iter_songs(
        self,
        search_term: str,
        *,
        batch_size: int = 50,
    ) -> AsyncIterator[CSLSongSample]:
        """Iterate over songs matching the search term

        Args:
            search_term: Term to search for
            batch_size: Number of songs per page

        Yields:
            CSLSongSample
        """
        bundle = IterSongsBundle.from_client(
            self,
            search_term=search_term,
            batch_size=batch_size,
        )
        async with aclosing(self._process_stream(bundle)) as items:
            async for item in items:
                yield item

    async def iter_artists(
        self,
        search_term: str,
        *,
        batch_size: int = 50,
    ) -> AsyncIterator[CSLArtistSample]:
        """Iterate over artists matching the search term

        Args:
            search_term: Term to search for
            batch_size: Number of artists per page

        Yields:
            CSLArtistSample
        """
        bundle = IterArtistsBundle.from_client(
            self,
            search_term=search_term,
            batch_size=batch_size,
        )
        async with aclosing(self._process_stream(bundle)) as items:
            async for item in items:
                yield item

    # --- Detailed DB reading ---

    async def get_song(self, song: CSLSongSample) -> CSLSong:
        """Fetch detailed song info from db

        Args:
            song: CSLSongSample, probably from iter_songs

        Returns:
            CSLSong
        """
        bundle = GetSongBundle(song)
        return await self.process(bundle)

    async def get_artist(self, artist: CSLArtistSample) -> CSLArtist:
        """Fetch detailed artist info from db

        Args:
            artist: CSLArtistSample, probably from iter_artists

        Returns:
            CSLArtist
        """
        bundle = GetArtistBundle(artist)
        return await self.process(bundle)

    async def get_metadata(self, track: CSLTrack) -> CSLMetadata | None:
        """Fetch metadata info from db

        Args:
            track: CSLTrack to get metadata from

        Returns:
            CSLMetadata, or None if it doesn't have any metadata
        """
        bundle = GetMetadataBundle(track)
        return await self.process(bundle)

    # --- List operations ---

    async def create_list(self, name: str, *csl_lists: CSLList) -> CSLList:
        """Make a list

        Args:
            name: name of the list
            *csl_lists: Lists to pull tracks from

        Returns:
            Newly created list

        Raises:
            niquests.HTTPError: The server rejected the request, for example because the list already exists
        """
        bundle = CreateListBundle(name, csl_lists)
        await self.process(bundle)
        await self.refresh_lists()
        return self.lists[name]

    async def list_edit(
        self,
        csl_list: CSLList,
        *,
        name: str | None = None,
        add: Iterable[CSLTrackRef] = (),
        remove: Iterable[CSLTrackRef] = (),
    ) -> None:
        """Edit a list

        Args:
            csl_list: List to edit
            name: New name
            add_tracks: Tracks to add
            remove_tracks: Tracks to remove
        """
        bundle = ListEditBundle(csl_list, name, add, remove)
        await self.process(bundle)

    async def list_delete(self, csl_list: CSLList) -> None:
        """Delete a list

        Args:
            csl_list: List to delete
        """
        bundle = ListDeleteBundle(csl_list)
        await self.process(bundle)
        await self.refresh_lists()

    # --- General Editing ---

    async def create_group(self, name: str) -> CSLGroup:
        """Create a group

        Args:
            name: Name of the group

        Returns:
            Newly created group
        """
        bundle = CreateGroupBundle(name)
        group = await self.process(bundle)
        await self.refresh_groups()
        return group

    async def group_edit(self, group: CSLGroup, name: str) -> None:
        """Edit a group

        Args:
            group: CSLGroup
            name: New name of the group
        """
        bundle = GroupEditBundle(group, name)
        await self.process(bundle)

    async def group_delete(self, group: CSLGroup) -> None:
        """Delete a group

        Args:
            group: CSLGroup
        """
        bundle = GroupDeleteBundle(group)
        await self.process(bundle)

    async def song_edit(
        self,
        song: CSLSong,
        name: str | None = None,
        disambiguation: str | None = None,
    ) -> None:
        """Edit a song

        Args:
            song: CSLSong
            name: New name
            disambiguation: New disambiguation
        """
        bundle = SongEditBundle(song, name, disambiguation)
        await self.process(bundle)

    async def song_delete(self, song: CSLSong) -> None:
        """Delete a song

        Args:
            song: CSLSong
        """
        bundle = SongDeleteBundle(song)
        await self.process(bundle)

    async def song_add_metadata(
        self,
        song: CSLSong,
        *metas: Metadata,
    ) -> None:
        """Add metadata to a song

        Args:
            song: CSLSong
            *metas: Metadata to add
        """
        bundle = SongAddMetadataBundle(song, metas)
        await self.process(bundle)

    async def song_delete_metadata(
        self,
        song: CSLSong,
        meta: CSLSongArtistCredit | CSLExtraMetadata,
    ) -> None:
        """Remove metadata from a song

        Args:
            song: CSLSong
            meta: Metadata to remove
        """
        bundle = SongDeleteMetadataBundle(song, meta)
        await self.process(bundle)

    async def track_add_metadata(
        self,
        track: CSLTrack,
        *metas: Metadata,
        override: bool | None = None,
        existing_meta: CSLMetadata | None = None,
    ) -> None:
        """Add metadata to a track

        Args:
            track: CSLTrack
            *metas: Metadata to add
            override: Change metadata to override or append
            existing_meta: Existing metadata on the track, pass in to avoid duplicating metadata

        Raises:
            ValueError: If non-metadata is passed into metas
        """
        bundle = TrackAddMetadataBundle(track, metas, override, existing_meta)
        await self.process(bundle)

    async def track_remove_metadata(
        self,
        track: CSLTrack,
        meta: CSLSongArtistCredit | CSLExtraMetadata,
    ) -> None:
        """Remove metadata from a track

        Args:
            track: CSLTrack
            meta: Metadata to remove
        """
        bundle = TrackDeleteMetadataBundle(track, meta)
        await self.process(bundle)

    async def track_edit(
        self,
        track: CSLTrack,
        *,
        artist_credits: Sequence[TrackPutArtistCredit] | None = None,
        groups: Sequence[CSLGroup] | None = None,
        name: str | None = None,
        original_artist: str | None = None,
        original_name: str | None = None,
        song: NewSong | CSLSongSample | None = None,
        type: TrackType | None = None,
    ) -> None:
        """Edit a track

        Args:
            track: CSLTrack
            artist_credits: List of new artist credits
            groups: List of new groups
            name: New track name
            original_artist: New track original artist
            original_name: New track original name
            song: New song
            type: New track type

        Raises:
            ValueError: New track type is not a valid track type
        """
        bundle = TrackEditBundle(track, artist_credits, groups, name, original_artist, original_name, song, type)
        await self.process(bundle)

    # --- Album Creation ---

    async def create_album(
        self,
        name: str,
        original_name: str,
        year: int,
        groups: Iterable[CSLGroup],
        tracks: Sequence[Sequence[AlbumTrack]],
    ) -> None:
        """Create an album

        Args:
            name: Album name
            original_name: Original album name
            year: Year album was created
            groups: Groups associated with album
            tracks: CSLTrack[][], where each CSLTrack[] is a disc
        """
        bundle = CreateAlbumBundle(name, original_name, year, groups, tracks)
        await self.process(bundle)

    async def add_audio(
        self,
        track: CSLTrack,
        audio_path: str | PathLike[str],
    ) -> None:
        """Add audio to a track

        Args:
            track: CSLTrack
            audio_path: Path to the audio file

        Raises:
            QueryError: Audio path is invalid
        """
        bundle = AddAudioBundle(track, audio_path)
        await self.process(bundle)

    async def import_audio(
        self,
        track: CSLTrack,
        track_to_import_from: CSLTrack,
    ) -> None:
        """Import audio from an existing track

        Args:
            track: CSLTrack
            track_to_import_from: Other track to import audio from
        """
        bundle = ImportAudioBundle(track, track_to_import_from)
        await self.process(bundle)
