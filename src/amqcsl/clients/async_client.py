import asyncio
import logging
from collections.abc import Callable, Coroutine, Iterable, Iterator, Sequence
from functools import cached_property
from itertools import chain
from os import PathLike
from pathlib import Path
from types import TracebackType
from typing import Any, Literal, Self

import httpx
from attrs import define, field
from attrs.validators import gt, instance_of, le, optional

from amqcsl.clients.bundles import (
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
    IterArtistsBundle,
    IterSongsBundle,
    IterTracksBundle,
    ListBundle,
    ListEditBundle,
    LogoutBundle,
    PageBundle,
    PageMultiVendor,
    TrackAddMetadataBundle,
    TrackDeleteMetadataBundle,
    TrackEditBundle,
)
from amqcsl.exceptions import ClientDoesNotExistError
from amqcsl.objects import (
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
    Metadata,
    TrackPutArtistCredit,
)

from ._client_consts import (
    DB_URL,
    DEFAULT_SESSION_PATH,
)

logger = logging.getLogger('amqcsl.client')

type ItemProcessor[T, R] = Callable[[AsyncDBClient, T], Coroutine[None, None, R]]


async def default_func[T](_: 'AsyncDBClient', item: T) -> T:
    return item


@define
class AsyncDBClient:
    """Async client for accessing the db.
    If session cookie is valid, username and password may be omitted.
    """

    #: DB username
    username: str | None = field(default=None, validator=optional(instance_of(str)))
    #: DB password
    password: str | None = field(default=None, validator=optional(instance_of(str)))
    #: Filepath to look for/store session cookie in, defaults to amq_session.txt
    session_path: Path = field(default=Path(DEFAULT_SESSION_PATH), converter=Path)
    _client: httpx.AsyncClient | None = field(default=None, init=False, repr=False)

    #: Maximum batch size when querying db
    max_batch_size: int = field(default=100, validator=[instance_of(int), gt(0)])
    #: Maximum number of queries when iterating
    max_query_size: int = field(default=1500, validator=[instance_of(int), gt(0)])
    #: Maximum number of concurrent requests
    max_request_count: int = field(default=15, validator=[instance_of(int), gt(0), le(50)])

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
    def client(self) -> httpx.AsyncClient:
        """Underlying httpx.Client"""
        if self._client is None:
            raise ClientDoesNotExistError
        return self._client

    @property
    def queue(self) -> list[Bundle[Any]]:
        return self._queue

    async def _send_request(self, req: httpx.Request) -> httpx.Response:
        async with self._request_semaphore:
            return await self.client.send(req)

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
        res: httpx.Response | Sequence[httpx.Response] | None = None
        while True:
            try:
                req = g.send(res)  # type: ignore[reportArgumentType]
            except StopIteration as e:
                return e.value
            match req:
                case httpx.Request():
                    res = await self._send_request(req)
                case reqs:
                    res = await asyncio.gather(*map(self._send_request, reqs))

    def enqueue(self, bundle: Bundle[None]):
        """Add an object to the queue

        Args:
            obj: An object wrapper around a request
        """
        self._queue.append(bundle)

    def commit(self, *, stop_if_err: bool = True):
        """Commit changes in the queue

        Args:
            stop_if_err: Stop sending requests if one of them errors
        """
        logger.info(f'Commiting {len(self.queue)} changes')
        results = asyncio.gather(*map(self.process, self.queue), return_exceptions=stop_if_err)
        for task, r in zip(self.queue, results):
            if isinstance(r, Exception):
                logger.error(f'{task} failed: {r!r}')

    # --- Initialization ---

    async def __aenter__(self) -> Self:
        logger.info('Creating client')
        self._client = httpx.AsyncClient(base_url=DB_URL)
        try:
            logger.info('Verifying permissions')
            bundle = AuthBundle(self.username, self.password, self.session_path)
            await self.process(bundle)
        except Exception:
            await self._client.aclose()
            raise
        else:
            await self.refresh_lists()
            await self.refresh_groups()
            return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ):
        if exc_type is None:
            logger.info('Closing client')
        else:
            if isinstance(exc_val, httpx.HTTPStatusError):
                try:
                    error_data = exc_val.response.json()
                    logger.error('JSON given with error, check logs', extra={'data': error_data})
                except Exception:
                    logger.error('No JSON given with error')
            logger.error('Exception encountered, closing client')

        if self._client:
            await self._client.aclose()

    async def logout(self):
        """Logout the client

        Raises:
            AMQCSLError: httpx client doesn't exist yet
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

    async def _process_pages[T, R](
        self,
        bundle: PageBundle[T, PageMultiVendor],
        func: ItemProcessor[T, R],
    ) -> Iterable[R]:
        logger.debug(f'Processing {type(bundle)}')
        g = bundle.vendor(self.client)
        [req] = next(g)
        res = await self._send_request(req)
        raw_page = bundle.process_response(res)
        page = bundle.clean_raw_page(raw_page)
        reqs = g.send([raw_page])
        async with asyncio.TaskGroup() as tg:
            tasks = [tg.create_task(self._process_page(page, func))]
            for req in reqs:
                tasks.append(tg.create_task(self._request_page_and_process(bundle, req, func)))
        return chain.from_iterable(task.result() for task in tasks)

    async def _request_page_and_process[T, R](
        self,
        bundle: PageBundle[T, PageMultiVendor],
        req: httpx.Request,
        func: ItemProcessor[T, R],
    ) -> Iterable[R]:
        res = await self._send_request(req)
        raw_page = bundle.process_response(res)
        return await self._process_page(bundle.clean_raw_page(raw_page), func)

    async def _process_page[T, R](
        self,
        page: Iterator[T],
        func: ItemProcessor[T, R],
    ) -> Iterable[R]:
        async with asyncio.TaskGroup() as tg:
            tasks = [tg.create_task(func(self, item)) for item in page]
        return [task.result() for task in tasks]

    async def iter_tracks[R](
        self,
        search_term: str = '',
        *,
        groups: Iterable[CSLGroup] = (),
        active_list: CSLList | None = None,
        missing_audio: bool = False,
        missing_info: bool = False,
        from_active_list: bool | None = None,
        batch_size: int = 50,
        func: ItemProcessor[CSLTrack, R] = default_func,
    ) -> Iterable[R]:
        """Gather tracks matching search term, optionally applying a continuation to each track

        Args:
            search_term: Search term
            groups: List of groups to restrict to, leave empty if no restriction
            active_list: List to restrict search by
            missing_audio: Restrict to songs without audio
            missing_info: Restrict to songs missing info
            from_active_list: Restrict to songs from active list, defaults to True if active_list is given and False otherwise
            batch_size: How many tracks to query at once (page size)
            func: Continuation

        Returns:
            Iterable of results from calling func on each track
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
        return await self._process_pages(bundle, func)

    async def iter_songs[R](
        self,
        search_term: str,
        *,
        batch_size: int = 50,
        func: ItemProcessor[CSLSongSample, R],
    ) -> Iterable[R]:
        """Gather songs matching search term, optionally applying a continuation to each song

        Args:
            search_term: Term to search for
            batch_size: Number of songs per page
            func: Continuation

        Returns:
            Iterable of results from calling func on each song
        """
        bundle = IterSongsBundle.from_client(
            self,
            search_term=search_term,
            batch_size=batch_size,
        )
        return await self._process_pages(bundle, func)

    async def iter_artists[R](
        self,
        search_term: str,
        *,
        batch_size: int = 50,
        func: ItemProcessor[CSLArtistSample, R] = default_func,
    ) -> Iterable[R]:
        """Gather artists matching search term, optionally applying a continuation to each artist

        Args:
            search_term: Term to search for
            batch_size: Number of artists per page
            func: Continuation

        Returns:
            Iterable of results from calling func on each artist
        """
        bundle = IterArtistsBundle.from_client(
            self,
            search_term=search_term,
            batch_size=batch_size,
        )
        return await self._process_pages(bundle, func)

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
        add: Iterable[CSLTrack] = (),
        remove: Iterable[CSLTrack] = (),
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
        song: CSLSong | None = None,
        type: Literal['Vocal', 'OffVocal', 'Instrumental', 'Dialogue', 'Other'] | None = None,
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
