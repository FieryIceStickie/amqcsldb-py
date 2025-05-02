import logging
from collections.abc import Iterator
from pathlib import Path

import httpx
from attrs import define, field

from amqcsl.exceptions import LoginError, QueryError
from amqcsl.objects import CSLList
from amqcsl.objects.objects import CSLArtistSample, CSLSongSample

from .client_utils import DB_URL, DEFAULT_SESSION_PATH, ArtistQueryParams, SongQueryParams

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

    def iter_tracks(self):
        pass

    def iter_songs(self, search_term: str, batch_size: int = 50) -> Iterator[CSLSongSample]:
        """Iterator over songs matching search_term"""
        if (client := self._client) is None:
            raise LoginError('Song query attempted without client')

        logger.info(f'Fetching songs matching search term "{search_term}"')
        params: SongQueryParams = {
            'searchTerm': search_term,
            'skip': 0,
            'take': batch_size,
            'orderBy': '',
            'filter': '',
        }

        prev_count = None
        num_read = 0
        while True:
            res = client.get('/api/songs', params=params)  # type: ignore [reportArgumentType]
            res.raise_for_status()
            match res.json():
                case {
                    'songs': [*songs],
                    'count': int(count),
                }:
                    if prev_count is None:
                        if count > self.max_query_size:
                            raise QueryError(
                                f'Query returns {count} results, which is larger than the max query size of {self.max_query_size}'
                            )
                    elif count != prev_count:
                        logger.error(f'Count mutated from {prev_count} to {count}')
                case _:
                    logger.error('Unexpected query response from /api/songs', extra={'response': res.json()})
                    raise QueryError('Unexpected query response')
            yield from map(CSLSongSample.from_json, songs)
            num_read += len(songs)
            logger.info('Page exhausted')

            if num_read >= count:
                break
            prev_count = count

            logger.info('Querying next page')
            params['skip'] += params['take']
        logger.info('Finished querying songs')

    def iter_artists(self, search_term: str, batch_size: int = 50) -> Iterator[CSLArtistSample]:
        """Iterator over artists matching search_term"""
        if (client := self._client) is None:
            raise LoginError('List query attempted without client')

        logger.info(f'Fetching artists matching search term "{search_term}"')
        params: ArtistQueryParams = {
            'searchTerm': search_term,
            'skip': 0,
            'take': batch_size,
            'orderBy': '',
            'filter': '',
        }

        prev_count = None
        num_read = 0
        while True:
            res = client.get('/api/artists', params=params)  # type: ignore [reportArgumentType]
            res.raise_for_status()
            match res.json():
                case {
                    'artists': [*artists],
                    'count': int(count),
                }:
                    if prev_count is None:
                        if count > self.max_query_size:
                            raise QueryError(
                                f'Query returns {count} results, which is larger than the max query size of {self.max_query_size}'
                            )
                    elif count != prev_count:
                        logger.error(f'Count mutated from {prev_count} to {count}')
                case _:
                    logger.error('Unexpected query response from /api/artist', extra={'response': res.json()})
                    raise QueryError('Unexpected query response')
            yield from map(CSLArtistSample.from_json, artists)
            num_read += len(artists)
            logger.info('Page exhausted')

            if num_read >= count:
                break
            prev_count = count

            logger.info('Querying next page')
            params['skip'] += params['take']
        logger.info('Finished querying artists')

    def iter_lists(self) -> Iterator[CSLList]:
        """Iterator over lists associated with account"""
        if (client := self._client) is None:
            raise LoginError('List query attempted without client')

        logger.info('Fetching lists')
        res = client.get('/api/lists')
        res.raise_for_status()
        for data in res.json():
            yield CSLList.from_json(data)

    # --- Detailed DB reading ---

    def get_song(self):
        pass

    def get_artist(self):
        pass

    def get_metadata(self):
        pass
