import logging
from pathlib import Path

import httpx
from attrs import define, field

from amqcsl.exceptions import LoginError

from .constants import DB_URL, DEFAULT_SESSION_PATH

logger = logging.getLogger('client')


@define
class DBClient:
    """
    Client for accessing the db.
    If session cookie is valid, username and password may be omitted.

    Attributes:
        username: DB username.
        password: DB password.
        session_path: Filepath to look for/store session cookie in, defaults to amq_session.txt.
    """

    username: str | None = None
    password: str | None = None
    session_path: Path = field(default=Path(DEFAULT_SESSION_PATH), converter=Path)
    _client: httpx.Client | None = field(default=None, init=False, repr=False)
    _session_cookie: str = field(init=False, repr=False)

    def __attrs_post_init__(self):
        if self.session_path.is_dir():
            raise FileNotFoundError('session_path must not be a directory')

        logger.info('Retrieving session cookie')
        try:
            with open(self.session_path, 'r') as file:
                self._session_cookie = file.read()
        except FileNotFoundError:
            self._session_cookie = ''

    def verify_perms(self):
        if (client := self._client) is None:
            raise LoginError('Auth attempted without client')

        res: httpx.Response | None = None

        try:
            is_valid_cookie = bool(self._session_cookie)

            if is_valid_cookie:
                logger.info('Trying session cookie')
                res = client.get('/api/auth/me')
                is_valid_cookie = res.status_code != 401

            if not is_valid_cookie:
                self.login(client)
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

    def login(self, client: httpx.Client):
        if not all((self.username, self.password)):
            raise LoginError('Username and password must not be empty')
        logger.info('Invalid session cookie, attempting login')
        body = {
            'username': self.username,
            'password': self.password,
        }
        res = client.post('/api/login', json=body)
        if res.status_code == 403:
            raise LoginError('Invalid login credentials')
        logger.info(f'Writing session_id to {self.session_path}')
        session_id = res.cookies['session-id']
        logger.debug(f'{session_id = }')
        with open(self.session_path, 'w') as file:
            file.write(session_id)


    def __enter__(self):
        logger.info('Creating client')
        self._client = httpx.Client(base_url=DB_URL, cookies={'session-id': self._session_cookie})
        try:
            logger.info('Verifying permissions')
            self.verify_perms()
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
