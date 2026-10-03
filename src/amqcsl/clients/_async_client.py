import asyncio
import logging
from collections.abc import AsyncGenerator, AsyncIterator, Iterable, Sequence
from contextlib import aclosing, nullcontext
from functools import cached_property
from os import PathLike
from pathlib import Path
from types import TracebackType
from typing import Any, Self

import niquests
from attrs import define, field
from attrs.validators import gt, le

from amqcsl.clients._http_utils import MultipartUpload, reject_redirect, request_options
from amqcsl.clients.bundles._core import Items, StreamingBundle
from amqcsl.clients.bundles._misc import (
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
from amqcsl.clients.bundles._pages import (
    IterArtistsBundle,
    IterSongsBundle,
    IterTracksBundle,
)
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

from ._client_consts import (
    DB_URL,
    DEFAULT_SESSION_PATH,
)

logger = logging.getLogger('amqcsl.client')


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
    #: Maximum number of concurrent requests
    max_request_count: int = field(default=15, validator=[gt(0), le(50)])

    _lists: CSLLists = field(factory=dict)
    _groups: CSLGroups = field(factory=dict)
    _queue: list[Bundle[Any]] = field(factory=list)

    def is_sync(self) -> bool:
        """Check for if client is synchronous

        Returns:
            False
        """
        return False

    @cached_property
    def _request_semaphore(self) -> asyncio.Semaphore:
        return asyncio.Semaphore(self.max_request_count)

    @property
    def client(self) -> niquests.AsyncSession:
        """Underlying niquests.AsyncSession"""
        if self._client is None:
            raise ClientDoesNotExistError
        return self._client

    @property
    def queue(self) -> list[Bundle[Any]]:
        return self._queue

    async def _send_request(self, req: niquests.PreparedRequest) -> niquests.Response:
        """Send within the concurrency limit using the shared transport policy."""
        async with self._request_semaphore:
            body = req.body
            with body.opened() if isinstance(body, MultipartUpload) else nullcontext():
                response = await self.client.send(req, **request_options(req))
            reject_redirect(response)
            return response

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
        while True:
            try:
                req = g.send(res)  # type: ignore[reportArgumentType]
            except StopIteration as e:
                return e.value
            match req:
                case Items():
                    g.close()
                    raise TypeError('Use a streaming iterator or .collect() for streaming bundles')
                case niquests.PreparedRequest():
                    res = await self._send_request(req)
                case reqs:
                    res = await asyncio.gather(*map(self._send_request, reqs))

    def enqueue(self, bundle: Bundle[None]):
        """Add an object to the queue

        Args:
            obj: An object wrapper around a request
        """
        self._queue.append(bundle)

    async def commit(self, *, stop_if_err: bool = True):
        """Commit changes in the queue

        Args:
            stop_if_err: Propagate HTTP errors after concurrent work finishes; otherwise log and ignore them.

        Queued bundles run concurrently. The queue is retained when an error is propagated.
        Errors other than HTTP errors always propagate.
        """
        logger.info(f'Commiting {len(self.queue)} changes')
        results = await asyncio.gather(*map(self.process, self.queue), return_exceptions=True)
        for task, result in zip(self.queue, results):
            match result:
                case niquests.exceptions.RequestException():
                    logger.error(f'{task} failed: {result!r}')
                    if stop_if_err:
                        raise result
                case BaseException():
                    raise result
                case _:
                    pass
        self.queue.clear()

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
            ListCreateError: Error if the request gives an error, probably because the list already exists
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
        queue: bool = False,
    ) -> None:
        """Add metadata to a song

        Args:
            song: CSLSong
            *metas: Metadata to add
            queue: Whether to queue the request, defaults to False
        """
        bundle = SongAddMetadataBundle(song, metas)
        if queue:
            self.enqueue(bundle)
        else:
            await self.process(bundle)

    async def song_delete_metadata(
        self,
        song: CSLSong,
        meta: CSLSongArtistCredit | CSLExtraMetadata,
        queue: bool = False,
    ) -> None:
        """Remove metadata from a song

        Args:
            song: CSLSong
            meta: Metadata to remove
            queue: Whether to queue the request, defaults to False
        """
        bundle = SongDeleteMetadataBundle(song, meta)
        if queue:
            self.enqueue(bundle)
        else:
            await self.process(bundle)

    async def track_add_metadata(
        self,
        track: CSLTrack,
        *metas: Metadata,
        override: bool | None = None,
        existing_meta: CSLMetadata | None = None,
        queue: bool = False,
    ) -> None:
        """Add metadata to a track

        Args:
            track: CSLTrack
            *metas: Metadata to add
            override: Change metadata to override or append
            existing_meta: Existing metadata on the track, pass in to avoid duplicating metadata
            queue: Whether to queue the request, defaults to False

        Raises:
            ValueError: If non-metadata is passed into metas
        """
        bundle = TrackAddMetadataBundle(track, metas, override, existing_meta)
        if queue:
            self.enqueue(bundle)
        else:
            await self.process(bundle)

    async def track_remove_metadata(
        self,
        track: CSLTrack,
        meta: CSLSongArtistCredit | CSLExtraMetadata,
        queue: bool = False,
    ) -> None:
        """Remove metadata from a track

        Args:
            track: CSLTrack
            meta: Metadata to remove
            queue: Whether to queue the request, defaults to False
        """
        bundle = TrackDeleteMetadataBundle(track, meta)
        if queue:
            self.enqueue(bundle)
        else:
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
        queue: bool = False,
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
            queue: Whether to queue the request, defaults to False

        Raises:
            ValueError: New track type is not a valid track type
        """
        bundle = TrackEditBundle(track, artist_credits, groups, name, original_artist, original_name, song, type)
        if queue:
            self.enqueue(bundle)
        else:
            await self.process(bundle)

    # --- Album Creation ---

    async def create_album(
        self,
        name: str,
        original_name: str,
        year: int,
        groups: Iterable[CSLGroup],
        tracks: Sequence[Sequence[AlbumTrack]],
        *,
        queue: bool = False,
    ) -> None:
        """Create an album

        Args:
            name: Album name
            original_name: Original album name
            year: Year album was created
            groups: Groups associated with album
            tracks: CSLTrack[][], where each CSLTrack[] is a disc
            queue: Whether to queue the request, defaults to False
        """
        bundle = CreateAlbumBundle(name, original_name, year, groups, tracks)
        if queue:
            self.enqueue(bundle)
        else:
            await self.process(bundle)

    async def add_audio(
        self,
        track: CSLTrack,
        audio_path: str | PathLike[str],
        *,
        queue: bool = False,
    ) -> None:
        """Add audio to a track

        Args:
            track: CSLTrack
            audio_path: Path to the audio file
            queue: Whether to queue the request, defaults to False

        Raises:
            QueryError: Audio path is invalid
        """
        bundle = AddAudioBundle(track, audio_path)
        if queue:
            self.enqueue(bundle)
        else:
            await self.process(bundle)

    async def import_audio(
        self,
        track: CSLTrack,
        track_to_import_from: CSLTrack,
        *,
        queue: bool = False,
    ) -> None:
        """Import audio from an existing track

        Args:
            track: CSLTrack
            track_to_import_from: Other track to import audio from
            queue: Whether to queue the request, defaults to False
        """
        bundle = ImportAudioBundle(track, track_to_import_from)
        if queue:
            self.enqueue(bundle)
        else:
            await self.process(bundle)
