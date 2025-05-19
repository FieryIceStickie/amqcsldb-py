import logging
from collections.abc import Iterator, Sequence
from pathlib import Path
from types import TracebackType
from typing import Literal, Self

import httpx
from attrs import define, field

from ._client_consts import (
    DB_URL,
    DEFAULT_SESSION_PATH,
    MetadataDelete,
    MetadataPost,
    QueueObj,
    TrackEdit,
)
from .exceptions import AMQCSLError, ClientDoesNotExistError, ListCreateError, LoginError, QueryError
from .objects import (
    EMPTY_ID,
    REVERSE_TRACK_TYPE,
    ArtistCredit,
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
    ExtraMetadata,
    JSONType,
    Metadata,
    MetadataPostBody,
    Query,
    QueryBodyTrack,
    QueryParamsArtist,
    QueryParamsSong,
    TrackPutArtistCredit,
    TrackPutBody,
)

logger = logging.getLogger('amqcsl.client')


@define
class DBClient:
    """Client for accessing the db.
    If session cookie is valid, username and password may be omitted.

    Attributes:
        username: DB username
        password: DB password
        session_path: Filepath to look for/store session cookie in, defaults to amq_session.txt
        max_batch_size: Maximum batch size when querying db
        max_query_size: Maximum number of queries when iterating
        queue: Request queue
    """

    username: str | None = None
    password: str | None = None
    session_path: Path = field(default=Path(DEFAULT_SESSION_PATH), converter=Path)
    _client: httpx.Client | None = field(default=None, init=False, repr=False)
    _session_cookie: str = field(init=False, repr=False)

    max_batch_size: int = 100
    max_query_size: int = 1500

    _lists: dict[str, CSLList] | None = None
    _groups: dict[str, CSLGroup] | None = None

    queue: list[QueueObj] = field(factory=list)

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            raise ClientDoesNotExistError
        return self._client

    @property
    def lists(self) -> dict[str, CSLList]:
        if self._lists is None:
            logger.info('Fetching lists')
            res = self.client.get('/api/lists')
            res.raise_for_status()
            self._lists = {}
            for data in res.json():
                csl_list = CSLList.from_json(data)
                self._lists[csl_list.name] = csl_list
        return self._lists

    @property
    def groups(self) -> dict[str, CSLGroup]:
        if self._groups is None:
            logger.info('Fetching groups')
            res = self.client.get('/api/groups')
            res.raise_for_status()
            self._groups = {}
            for data in res.json():
                group = CSLGroup.from_json(data)
                self._groups[group.name] = group
        return self._groups

    # --- Queue Methods ---

    def enqueue(self, obj: QueueObj):
        self.queue.append(obj)

    def commit(self, *, stop_if_err: bool = True):
        """Commit changes in the queue

        Args:
            stop_if_err: Stop sending requests if one of them errors
        """
        logger.info(f'Commiting {len(self.queue)} changes')
        client = self.client
        for obj in self.queue:
            res = client.send(obj.req)
            try:
                res.raise_for_status()
            except httpx.HTTPError:
                logger.exception(f'Request {obj.req.method} {obj.req.url} caused an error')
                if stop_if_err:
                    raise

    # --- Initialization ---

    def __attrs_post_init__(self):
        if self.session_path.is_dir():
            raise FileNotFoundError('session_path must not be a directory')
        logger.info('Retrieving session cookie')
        try:
            with open(self.session_path, 'r') as file:
                self._session_cookie = file.read().strip()
        except FileNotFoundError:
            self._session_cookie = ''

    def _verify_perms(self):
        """Verify that the user login info is correct and user has admin

        Raises:
            LoginError: If login fails for any expected reason
            RuntimeError: If login fails for an unexpected reason
        """
        if (client := self._client) is None:
            raise LoginError('Auth attempted without client')

        res: httpx.Response | None = None

        try:
            is_valid_cookie = bool(self._session_cookie)

            if is_valid_cookie:
                logger.info('Trying session cookie')
                logger.debug('SESSION-ID', extra={'session-id': self._session_cookie})
                res = client.get('/api/auth/me')
                is_valid_cookie = res.status_code != 401

            if not is_valid_cookie:
                logger.info('Invalid session cookie, attempting login')
                self._login(client)
                res = client.get('/api/auth/me')

            if res is None:
                raise RuntimeError('Unexpected branch')
            res.raise_for_status()
        except httpx.RequestError as e:
            logger.exception(f'Bad request during auth: {e}')
            raise
        except httpx.HTTPStatusError as e:
            logger.exception(f'Bad response during auth: {e.response.status_code}')
            raise
        except LoginError as e:
            logger.exception(f'Error during login: {e}')
            raise
        except Exception:
            logger.exception('Unexpected error during auth')
            raise

        logger.info('Auth successful')
        if 'ADMIN' not in res.json()['roles']:
            raise LoginError(f'User {res.json()["name"]} does not have admin privileges')

    def _login(self, client: httpx.Client):
        """Attempt login, saves session_id to file if successful

        Args:
            client: HTTPx client

        Raises:
            LoginError: If the username and password are invalid
        """
        if not all((self.username, self.password)):
            raise LoginError('Username and password must not be empty')
        body = {
            'username': self.username,
            'password': self.password,
        }
        res = client.post('/api/login', json=body)
        if res.status_code == 403:
            raise LoginError('Invalid login credentials')
        logger.info(f'Writing session_id to {self.session_path}')
        session_id = res.cookies['session-id']
        logger.debug('session_id', extra={'session-id': session_id})
        with open(self.session_path, 'w') as file:
            file.write(session_id)

    def __enter__(self) -> Self:
        logger.info('Creating client')
        self._client = httpx.Client(base_url=DB_URL, cookies={'session-id': self._session_cookie})
        try:
            logger.info('Verifying permissions')
            self._verify_perms()
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
        if (client := self._client) is None:
            raise AMQCSLError('Logout attempted without client')

        logger.info('Logging out the client')
        res = client.post('/api/logout')
        res.raise_for_status()

        logger.info('Logout successful')
        with open(self.session_path, 'w'):
            pass

    # --- Batch DB reading ---

    def iter_tracks(
        self,
        search_term: str = '',
        *,
        groups: list[CSLGroup] | None = None,
        active_list: CSLList | None = None,
        missing_audio: bool = False,
        missing_info: bool = False,
        from_active_list: bool = True,
        batch_size: int = 50,
    ) -> Iterator[CSLTrack]:
        """Iterate over tracks matching search parameters

        Args:
            search_term: Search term
            groups: List of groups to restrict to, leave empty if no restriction
            active_list: List to restrict search by
            missing_audio: Restrict to songs without audio
            missing_info: Restrict to songs missing info
            from_active_list: Restrict to songs in active list
            batch_size: How many tracks to query at once (page size)

        Yields:
            CSLTrack
        """
        if groups is None:
            groups = []

        logger.info(
            f'Fetching songs matching search term "{search_term}"',
            extra={
                'groups': [(group.id, group.name) for group in groups],
                'missing_audio': missing_audio,
                'missing_info': missing_info,
                'from_active_list': from_active_list,
                'active_list': None if active_list is None else {'id': active_list.id, 'name': active_list.name},
            },
        )
        body: QueryBodyTrack = {
            'activeListId': None if active_list is None else active_list.id,
            'filter': '',
            'groupFilters': [group.id for group in groups],
            'orderBy': '',
            'quickFilters': [
                idx
                for idx, val in enumerate(
                    [missing_audio, missing_info, from_active_list],
                    start=1,
                )
                if val
            ],
            'searchTerm': search_term,
            'skip': 0,
            'take': batch_size,
        }
        for page in self._iter_pages('POST', '/api/tracks', body, 'tracks'):
            yield from map(CSLTrack.from_json, page)

    def iter_songs(self, search_term: str, *, batch_size: int = 50) -> Iterator[CSLSongSample]:
        """Iterate over songs matching search_term

        Args:
            search_term: Term to search for
            batch_size: Number of songs per page

        Yields:
            CSLSongSample
        """
        logger.info(f'Fetching songs matching search term "{search_term}"')
        params: QueryParamsSong = {
            'searchTerm': search_term,
            'skip': 0,
            'take': batch_size,
            'orderBy': '',
            'filter': '',
        }
        for page in self._iter_pages('GET', '/api/songs', params, 'songs'):
            yield from map(CSLSongSample.from_json, page)

    def iter_artists(self, search_term: str, *, batch_size: int = 50) -> Iterator[CSLArtistSample]:
        """Iterator over artists matching search_term

        Args:
            search_term: Term to search for
            batch_size: Number of artists per page

        Yields:
            CSLArtistSample
        """
        params: QueryParamsArtist = {
            'searchTerm': search_term,
            'skip': 0,
            'take': batch_size,
            'orderBy': '',
            'filter': '',
        }
        logger.info(f'Fetching artists matching search term "{search_term}"')
        for page in self._iter_pages('GET', '/api/artists', params, 'artists'):
            yield from map(CSLArtistSample.from_json, page)

    def _iter_pages(
        self,
        req_type: Literal['POST', 'GET'],
        path: str,
        query: Query,
        key: str,
    ) -> Iterator[list[JSONType]]:
        """Internal helper for iterating over pages

        Args:
            req_type: Type of the request
            path: Request path
            query: Query/Body for the request
            key: Name of the thing being searched for (song/artist/track)

        Yields:
            JSON objects from the pages

        Raises:
            QueryError: Batch size is invalid
        """
        if query['take'] <= 0:
            raise QueryError('Batch size must be positive')
        elif query['take'] > self.max_batch_size:
            raise QueryError(f'Batch size {query["take"]} is larger than the max batch size of {self.max_batch_size}')
        prev_count = None
        num_read = 0
        while True:
            if req_type == 'POST':
                res = self.client.post(path, json=query)
            else:
                res = self.client.get(path, params=query)  # type: ignore[reportArgumentType]
            res.raise_for_status()
            match res.json():
                case {
                    'count': int(count),
                    **data,
                } if isinstance(data.get(key, None), list):
                    if prev_count is None:
                        if count > self.max_query_size:
                            raise QueryError(
                                f'Query returns {count} results, which is larger than the max query size of {self.max_query_size}'
                            )
                    elif count != prev_count:
                        logger.error(f'Count mutated from {prev_count} to {count}')
                case _:
                    logger.error(f'Unexpected query response from {req_type} {path}', extra={'response': res.json()})
                    raise QueryError('Unexpected query response')
            yield data[key]
            num_read += len(data[key])
            logger.info('Page exhausted')
            if num_read >= count:
                break
            prev_count = count

            logger.info('Querying next page')
            query['skip'] += query['take']
        logger.info(f'Finished querying {key}')

    # --- Detailed DB reading ---

    def get_song(self, song: CSLSongSample) -> CSLSong:
        """Fetch detailed song info from db

        Args:
            song: CSLSongSample, probably from iter_songs

        Returns:
            CSLSong
        """
        if isinstance(song, CSLSong):
            logger.warning(f'client.get_song called with already filled CSLSong {song.name}')
            return song
        res = self.client.get(f'/api/song/{song.id}')
        res.raise_for_status()
        return CSLSong.from_json(res.json())

    def get_artist(self, artist: CSLArtistSample) -> CSLArtist:
        """Fetch detailed artist info from db

        Args:
            artist: CSLArtistSample, probably from iter_artists

        Returns:
            CSLArtist
        """
        if isinstance(artist, CSLArtist):
            logger.warning(f'client.get_artist called with already filled CSLArtist {artist.name}')
            return artist
        res = self.client.get(f'/api/artist/{artist.id}')
        res.raise_for_status()
        return CSLArtist.from_json(res.json())

    def get_metadata(self, track: CSLTrack) -> CSLMetadata | None:
        """Fetch metadata info from db

        Args:
            track: CSLTrack to get metadata from

        Returns:
            CSLMetadata, or None if it doesn't have any metadata
        """
        res = self.client.get(f'/api/track/{track.id}/metadata')
        if res.status_code == 404 and res.json()['errors']['generalErrors'][0] == 'Song does not have metadata':
            return None
        res.raise_for_status()
        return CSLMetadata.from_json(res.json())

    # --- List operations ---

    def list_add(self, csl_list: CSLList, *tracks: CSLTrack) -> None:
        """Add tracks to a list

        Args:
            csl_list: List to add to
            *tracks: Tracks to add
        """
        self.list_edit(csl_list, tracks, [])

    def list_remove(self, csl_list: CSLList, *tracks: CSLTrack) -> None:
        """Remove tracks from a list

        Args:
            csl_list: List to remove from
            *tracks: Tracks to remove
        """
        self.list_edit(csl_list, [], tracks)

    def list_edit(
        self,
        csl_list: CSLList,
        add_tracks: Sequence[CSLTrack],
        remove_tracks: Sequence[CSLTrack],
    ) -> None:
        """Edit a list

        Args:
            csl_list: List to edit
            add_tracks: Tracks to add
            remove_tracks: Tracks to remove
        """
        logger.info(f'Editing list {csl_list.name}')
        body = {
            'addSongIds': [track.id for track in add_tracks] if add_tracks else None,
            'id': EMPTY_ID,
            'name': None,
            'removeSongIds': [track.id for track in remove_tracks] if remove_tracks else None,
        }
        res = self.client.put(f'/api/list/{csl_list.id}', json=body)
        res.raise_for_status()

    def list_create(self, name: str, *csl_lists: CSLList) -> CSLList:
        """Make a list

        Args:
            name: name of the list
            *csl_lists: Lists to pull tracks from

        Returns:
            Newly created list

        Raises:
            ListCreateError: Error if the request gives an error, probably because the list already exists
        """
        logger.info(f'Making list {name}')
        body = {
            'importListIds': [csl_list.id for csl_list in csl_lists],
            'name': name,
        }
        res = self.client.post('/api/list', json=body)
        if res.status_code == 400:
            raise ListCreateError(res.json()['errors']['generalErrors'][0])
        res.raise_for_status()
        logger.info(f'List {name} created')
        self._lists = None
        return self.lists[name]

    # --- General Editing ---
    def track_metadata_add(
        self,
        track: CSLTrack,
        *metas: Metadata,
        override: bool | None = None,
        existing_meta: CSLMetadata | None = None,
        queue: bool = False,
    ):
        """Add metadata to a track

        Args:
            track: CSLTrack
            *metas: Metadata to add
            override: Change metadata to override or append
            existing_meta: Existing metadata on the track, pass in to avoid duplicating metadata
            queue: Whether to queue the request, defaults to False

        Raises:
            ValueError: If non-metadata is passed into *metas
        """
        logger.info(f'Queuing metadata edit on {track.name}')

        current_metas: set[Metadata]
        if existing_meta is None:
            current_metas = set()
        else:
            current_metas = {
                *map(ArtistCredit.simplify, existing_meta.artist_credits),
                *map(ExtraMetadata.simplify, existing_meta.extra_metas),
            }
        id_to_name: dict[str, str] = {}

        body: MetadataPostBody = {
            'artistCredits': [],
            'extraMetadatas': [],
            'id': EMPTY_ID,
            'override': override,
        }
        for meta in metas:
            match meta:
                case ArtistCredit():
                    if meta not in current_metas:
                        logger.debug(f'Adding artist credit {meta.type} {meta.artist.name}')
                        body['artistCredits'].append(meta.to_json())
                        current_metas.add(meta)
                        id_to_name[meta.artist.id] = meta.artist.name
                case ExtraMetadata():
                    if meta not in current_metas:
                        logger.debug(f'Adding extra metadata {meta.type}: {meta.value}')
                        body['extraMetadatas'].append(meta.to_json())
                        current_metas.add(meta)
                case _:
                    raise ValueError('metas must be ArtistCredit or ExtraMetadata')
        if (
            not body['artistCredits']
            and not body['extraMetadatas']
            and body['id'] == EMPTY_ID
            and body['override'] is None
        ):
            logger.info('No changes necessary, skipping request')
            return
        req = self.client.build_request('POST', f'/api/track/{track.id}/metadata', json=body)
        if queue:
            self.enqueue(MetadataPost(req, track, body, id_to_name))
        else:
            res = self.client.send(req)
            res.raise_for_status()

    def track_metadata_remove(
        self,
        track: CSLTrack,
        meta: CSLSongArtistCredit | CSLExtraMetadata,
        queue: bool = False,
    ):
        """Remove metadata from a track

        Args:
            track: CSLTrack
            meta: Metadata to remove
            queue: Whether to queue the request, defaults to False
        """
        logger.info(f'Removing metadata {meta} from {track.name}')
        req = self.client.build_request('DELETE', f'/api/track/{track.id}/metadata/{meta.id}')
        if queue:
            self.enqueue(MetadataDelete(req, track, meta))
        else:
            res = self.client.send(req)
            res.raise_for_status()

    def group_add(self, name: str) -> CSLGroup:
        """Add a group

        Args:
            name: Name of the group

        Returns:
            Newly created group
        """
        logger.info(f'Adding group {name}')
        res = self.client.post('/api/group', json={'name': name})
        res.raise_for_status()
        return CSLGroup.from_json(res.json())

    def track_edit(
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
    ):
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
        if type is not None and type not in REVERSE_TRACK_TYPE:
            raise ValueError('Track type must be one of the following: {', '.join(REVERSE_TRACK_TYPE)}')
        logger.info(f'Editing track {track.name}')
        body: TrackPutBody = {
            'artistCredits': None if artist_credits is None else [v.to_json(i) for i, v in enumerate(artist_credits)],
            'batchSongIds': None,
            'groupIds': None if groups is None else [group.id for group in groups],
            'id': EMPTY_ID,
            'name': name,
            'newSong': None,
            'originalArtist': original_artist,
            'originalName': original_name,
            'songId': None if song is None else song.id,
            'type': None if type is None else REVERSE_TRACK_TYPE[type],
        }
        req = self.client.build_request('PUT', f'/api/track/{track.id}', json=body)
        if queue:
            self.enqueue(TrackEdit(req, track, body))
        else:
            res = self.client.send(req)
            res.raise_for_status()
