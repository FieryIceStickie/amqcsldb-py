import logging
from collections.abc import Iterable, Iterator, Sequence
from os import PathLike
from pathlib import Path
from types import TracebackType
from typing import Any, Literal, Self

import httpx
from attrs import define, field
from attrs.validators import gt, instance_of, optional

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
    PageSingleVendor,
    RawPage,
    TrackAddMetadataBundle,
    TrackDeleteMetadataBundle,
    TrackEditBundle,
)
from amqcsl.clients.bundles.misc import GroupDeleteBundle, GroupEditBundle, SongDeleteBundle, SongEditBundle
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
from amqcsl.objects._db_types import NewSong

from ._client_consts import (
    DB_URL,
    DEFAULT_SESSION_PATH,
)

logger = logging.getLogger('amqcsl.client')


@define
class DBClient:
    """Client for accessing the db.
    If session cookie is valid, username and password may be omitted.
    """

    #: DB username
    username: str | None = field(default=None, validator=optional(instance_of(str)))
    #: DB password
    password: str | None = field(default=None, validator=optional(instance_of(str)))
    #: Filepath to look for/store session cookie in, defaults to amq_session.txt
    session_path: Path = field(default=Path(DEFAULT_SESSION_PATH), converter=Path)
    _client: httpx.Client | None = field(default=None, init=False, repr=False)

    #: Maximum batch size when querying db
    max_batch_size: int = field(default=100, validator=[instance_of(int), gt(0)])
    #: Maximum number of queries when iterating
    max_query_size: int = field(default=1500, validator=[instance_of(int), gt(0)])

    _lists: CSLLists | None = None
    _groups: CSLGroups | None = None
    _queue: list[Bundle[Any]] = field(factory=list)

    def is_sync(self) -> bool:
        """Check for if client is synchronous

        Returns:
            True
        """
        return True

    @property
    def client(self) -> httpx.Client:
        """Underlying httpx.Client"""
        if self._client is None:
            raise ClientDoesNotExistError
        return self._client

    @property
    def queue(self) -> list[Bundle[Any]]:
        return self._queue

    def process[R](self, bundle: Bundle[R]) -> R:
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
                    res = client.send(req)
                case reqs:
                    res = [client.send(req) for req in reqs]

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
        logger.info(f'Commiting {len(self._queue)} changes')
        for bundle in self._queue:
            try:
                self.process(bundle)
            except httpx.HTTPError:
                if stop_if_err:
                    raise

    # --- Initialization ---

    def __enter__(self) -> Self:
        logger.info('Creating client')
        self._client = httpx.Client(base_url=DB_URL)
        try:
            logger.info('Verifying permissions')
            bundle = AuthBundle(self.username, self.password, self.session_path)
            self.process(bundle)
        except Exception:
            self._client.close()
            raise
        else:
            return self

    def __exit__(
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
            self._client.close()

    def logout(self):
        """Logout the client

        Raises:
            AMQCSLError: httpx client doesn't exist yet
        """
        bundle = LogoutBundle(self.session_path)
        self.process(bundle)

    # --- Batch DB reading ---

    @property
    def lists(self) -> CSLLists:
        """Dictionary of user's lists, indexed by name"""
        if self._lists is None:
            bundle = ListBundle()
            self._lists = self.process(bundle)
        return self._lists

    @property
    def groups(self) -> CSLGroups:
        """Dictionary of DB groups, indexed by name"""
        if self._groups is None:
            bundle = GroupBundle()
            self._groups = self.process(bundle)
        return self._groups

    def _process_pages[R](self, bundle: PageBundle[R, PageSingleVendor]) -> Iterator[R]:
        """Processes a page bundle by lazily yielding results

        Args:
            bundle: LazyBundle

        Yields:
            Output of the bundle
        """
        logger.debug(f'Processing {type(bundle)}')
        client = self.client
        g = bundle.vendor(client)
        raw_page: RawPage | None = None
        while True:
            try:
                req = g.send(raw_page)  # type: ignore[reportArgumentType]
            except StopIteration:
                break
            match req:
                case httpx.Request():
                    resps = [client.send(req)]
                case reqs:
                    resps = [client.send(req) for req in reqs]
            for res in resps:
                raw_page = bundle.process_response(res)
                yield from bundle.clean_raw_page(raw_page)

    def iter_tracks(
        self,
        search_term: str = '',
        *,
        groups: Iterable[CSLGroup] = (),
        active_list: CSLList | None = None,
        missing_audio: bool = False,
        missing_info: bool = False,
        from_active_list: bool | None = None,
        batch_size: int = 50,
    ) -> Iterator[CSLTrack]:
        """Iterate over tracks matching search parameters

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
        yield from self._process_pages(bundle)

    def iter_songs(self, search_term: str, *, batch_size: int = 50) -> Iterator[CSLSongSample]:
        """Iterate over songs matching search_term

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
        yield from self._process_pages(bundle)

    def iter_artists(self, search_term: str, *, batch_size: int = 50) -> Iterator[CSLArtistSample]:
        """Iterator over artists matching search_term

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
        yield from self._process_pages(bundle)

    # --- Detailed DB reading ---

    def get_song(self, song: CSLSongSample) -> CSLSong:
        """Fetch detailed song info from db

        Args:
            song: CSLSongSample, probably from iter_songs

        Returns:
            CSLSong
        """
        bundle = GetSongBundle(song)
        return self.process(bundle)

    def get_artist(self, artist: CSLArtistSample) -> CSLArtist:
        """Fetch detailed artist info from db

        Args:
            artist: CSLArtistSample, probably from iter_artists

        Returns:
            CSLArtist
        """
        bundle = GetArtistBundle(artist)
        return self.process(bundle)

    def get_metadata(self, track: CSLTrack) -> CSLMetadata | None:
        """Fetch metadata info from db

        Args:
            track: CSLTrack to get metadata from

        Returns:
            CSLMetadata, or None if it doesn't have any metadata
        """
        bundle = GetMetadataBundle(track)
        return self.process(bundle)

    # --- List operations ---

    def create_list(self, name: str, *csl_lists: CSLList) -> CSLList:
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
        self.process(bundle)
        self._lists = None
        return self.lists[name]

    def list_edit(
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
        self.process(bundle)

    # --- General Editing ---

    def create_group(self, name: str) -> CSLGroup:
        """Create a group

        Args:
            name: Name of the group

        Returns:
            Newly created group
        """
        bundle = CreateGroupBundle(name)
        return self.process(bundle)

    def group_edit(self, group: CSLGroup, name: str, *, queue: bool = False) -> None:
        """Edit a group

        Args:
            group: CSLGroup
            name: New name of the group
        """
        bundle = GroupEditBundle(group, name)
        if queue:
            self.enqueue(bundle)
        else:
            self.process(bundle)

    def group_delete(self, group: CSLGroup, *, queue: bool = False) -> None:
        """Delete a group

        Args:
            group: CSLGroup
        """
        bundle = GroupDeleteBundle(group)
        if queue:
            self.enqueue(bundle)
        else:
            self.process(bundle)

    def song_edit(
        self,
        song: CSLSong,
        name: str | None = None,
        disambiguation: str | None = None,
        *,
        queue: bool = False,
    ) -> None:
        """Edit a song

        Args:
            song: CSLSong
            name: New name
            disambiguation: New disambiguation
        """
        bundle = SongEditBundle(song, name, disambiguation)
        if queue:
            self.enqueue(bundle)
        else:
            self.process(bundle)

    def song_delete(self, song: CSLSong, *, queue: bool = False) -> None:
        """Delete a song

        Args:
            song: CSLSong
        """
        bundle = SongDeleteBundle(song)
        if queue:
            self.enqueue(bundle)
        else:
            self.process(bundle)

    def track_add_metadata(
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
            self.process(bundle)

    def track_remove_metadata(
        self,
        track: CSLTrack,
        meta: CSLSongArtistCredit | CSLExtraMetadata,
        *,
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
            self.process(bundle)

    def track_edit(
        self,
        track: CSLTrack,
        *,
        artist_credits: Sequence[TrackPutArtistCredit] | None = None,
        groups: Sequence[CSLGroup] | None = None,
        name: str | None = None,
        original_artist: str | None = None,
        original_name: str | None = None,
        song: NewSong | None = None,
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

        Returns:
            Newly edited track
        """
        bundle = TrackEditBundle(track, artist_credits, groups, name, original_artist, original_name, song, type)
        if queue:
            self.enqueue(bundle)
        else:
            self.process(bundle)

    # --- Album Creation ---

    def create_album(
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
            self.process(bundle)

    def add_audio(
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
            self.process(bundle)
