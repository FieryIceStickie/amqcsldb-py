import logging
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Literal

import httpx
from attrs import define, field

from amqcsl.exceptions import AMQCSLError, ClientDoesNotExistError, ListCreateError, LoginError, QueryError
from amqcsl.objects import (
    EMPTY_ID,
    CSLArtist,
    CSLArtistSample,
    CSLGroup,
    CSLList,
    CSLMetadata,
    CSLSong,
    CSLSongSample,
    CSLTrack,
    CSLTrackSample,
    JSONType,
)

from .client_utils import DB_URL, DEFAULT_SESSION_PATH, ArtistQueryParams, Query, SongQueryParams, TrackQueryBody

logger = logging.getLogger('client')


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
    """

    username: str | None = None
    password: str | None = None
    session_path: Path = field(default=Path(DEFAULT_SESSION_PATH), converter=Path)
    _client: httpx.Client | None = field(default=None, init=False, repr=False)
    _session_cookie: str = field(init=False, repr=False)

    max_batch_size: int = 100
    max_query_size: int = 1500

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            raise ClientDoesNotExistError
        return self._client

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
        """Attempt login, saves session_id to file if successful"""
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

    def __enter__(self):
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

    def __exit__(self, exc_type, exc_val, exc_tb):  # type: ignore[reportMissingParameterType]
        if exc_type is None:
            logger.info('Closing client')
        else:
            logger.info('Exception encountered, closing client')
        if self._client:
            self._client.close()

    def logout(self):
        """Logout the client"""
        if (client := self._client) is None:
            raise LoginError('Logout attempted without client')

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
        body: TrackQueryBody = {
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
        """Iterator over songs matching search_term"""
        logger.info(f'Fetching songs matching search term "{search_term}"')
        params: SongQueryParams = {
            'searchTerm': search_term,
            'skip': 0,
            'take': batch_size,
            'orderBy': '',
            'filter': '',
        }
        for page in self._iter_pages('GET', '/api/songs', params, 'songs'):
            yield from map(CSLSongSample.from_json, page)

    def iter_artists(self, search_term: str, *, batch_size: int = 50) -> Iterator[CSLArtistSample]:
        """Iterator over artists matching search_term"""
        params: ArtistQueryParams = {
            'searchTerm': search_term,
            'skip': 0,
            'take': batch_size,
            'orderBy': '',
            'filter': '',
        }
        logger.info(f'Fetching artists matching search term "{search_term}"')
        for page in self._iter_pages('GET', '/api/artists', params, 'artists'):
            yield from map(CSLArtistSample.from_json, page)

    def iter_lists(self) -> Iterator[CSLList]:
        """Iterator over lists associated with account"""
        logger.info('Fetching lists')
        res = self.client.get('/api/lists')
        res.raise_for_status()
        for data in res.json():
            yield CSLList.from_json(data)

    def _iter_pages(
        self,
        req_type: Literal['POST', 'GET'],
        path: str,
        query: Query,
        key: str,
    ) -> Iterator[list[JSONType]]:
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
        """Fetch detailed song info from db"""
        if isinstance(song, CSLSong):
            logger.warning(f'client.get_song called with already filled CSLSong {song.name}')
            return song
        res = self.client.get(f'/api/song/{song.id}')
        res.raise_for_status()
        return CSLSong.from_json(res.json())

    def get_artist(self, artist: CSLArtistSample) -> CSLArtist:
        """Fetch detailed artist info from db"""
        if isinstance(artist, CSLArtist):
            logger.warning(f'client.get_artist called with already filled CSLArtist {artist.name}')
            return artist
        res = self.client.get(f'/api/artist/{artist.id}')
        res.raise_for_status()
        return CSLArtist.from_json(res.json())

    def get_metadata(self, track: CSLTrackSample) -> CSLMetadata | None:
        """Fetch metadata info from db"""
        res = self.client.get(f'/api/track/{track.id}/metadata')
        if res.status_code == 404 and res.json()['errors']['generalErrors'][0] == 'Song does not have metadata':
            return None
        res.raise_for_status()
        return CSLMetadata.from_json(res.json())

    # --- List operations ---

    def add_to_list(self, csl_list: CSLList, *tracks: CSLTrackSample) -> None:
        """Add tracks to a list"""
        self.edit_list(csl_list, tracks, [])

    def remove_from_list(self, csl_list: CSLList, *tracks: CSLTrackSample) -> None:
        """Remove tracks from a list"""
        self.edit_list(csl_list, [], tracks)

    def edit_list(
        self,
        csl_list: CSLList,
        add_tracks: Sequence[CSLTrackSample],
        remove_tracks: Sequence[CSLTrackSample],
    ) -> None:
        """Edit a list"""
        logger.info(f'Editing list {csl_list.name}')
        body = {
            'addSongIds': [track.id for track in add_tracks] if add_tracks else None,
            'id': EMPTY_ID,
            'name': None,
            'removeSongIds': [track.id for track in remove_tracks] if remove_tracks else None,
        }
        res = self.client.put(f'/api/list/{csl_list.id}', json=body)
        res.raise_for_status()

    def create_list(self, name: str, *csl_lists: CSLList) -> None:
        """Make a list"""
        logger.info(f'Making list {name}')
        body = {
            'importListIds': [csl_list.id for csl_list in csl_lists],
            'name': name,
        }
        res = self.client.post('/api/list', json=body)
        if res.status_code == 400:
            raise ListCreateError(res.json()['errors']['generalErrors'][0])
        res.raise_for_status()
