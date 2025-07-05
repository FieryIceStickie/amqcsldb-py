import logging
from pathlib import Path
from typing import override

import httpx
from attrs import field, frozen
from attrs.validators import instance_of

from amqcsl.exceptions import LoginError
from amqcsl.objects._db_types import CSLGroup, CSLList

from .core import Bundle, RichReprRtn, SingleVendor, httpxClient

logger = logging.getLogger('amqcsl.client')


@frozen
class AuthBundle(Bundle[None]):
    username: str | None = field(validator=instance_of((str, type(None))))
    password: str | None = field(repr=False, validator=instance_of((str, type(None))))
    session_path: Path = field(validator=instance_of(Path))

    def get_session_cookie(self) -> str:
        """Get the session cookie from the file

        Returns:
            the session cookie

        Raises:
            FileNotFoundError: File doesn't exist, or path is a directory
        """
        if self.session_path.is_dir():
            raise FileNotFoundError('session_path must not be a directory')
        logger.info('Retrieving session cookie')
        try:
            with open(self.session_path, 'r') as file:
                return file.read().strip()
        except FileNotFoundError:
            return ''

    def login(self) -> SingleVendor[None]:
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
        res = yield httpx.Request('POST', '/api/login', json=body)
        if res.status_code == 403:
            raise LoginError('Invalid login credentials')
        logger.info(f'Writing session_id to {self.session_path}')
        session_id = res.cookies['session-id']
        logger.debug('session_id', extra={'session-id': session_id})
        with open(self.session_path, 'w') as file:
            file.write(session_id)

    @override
    def vendor(self, client: httpxClient) -> SingleVendor[None]:
        """Verify that the user login info is correct and user has admin

        Raises:
            LoginError: If login fails for any expected reason
            RuntimeError: If login fails for an unexpected reason
        """
        session_cookie = self.get_session_cookie()
        client.cookies.set('session-id', session_cookie)
        res: httpx.Response | None = None

        try:
            is_valid_cookie = bool(session_cookie)
            if is_valid_cookie:
                logger.info('Trying session cookie')
                logger.debug('SESSION-ID', extra={'session-id': session_cookie})
                res = yield client.build_request('GET', '/api/auth/me')
                is_valid_cookie = res.status_code != 401

            if not is_valid_cookie:
                logger.info('Invalid session cookie, attempting login')
                client.cookies.delete('session-id')
                yield from self.login()
                res = yield client.build_request('GET', '/api/auth/me')

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
            logger.exception(f'Error during login of user {self.username}: {e}')
            raise
        except Exception:
            logger.exception('Unexpected error during auth')
            raise

        logger.info('Auth successful')
        if 'ADMIN' not in res.json()['roles']:
            raise LoginError(f'User {res.json()["name"]} does not have admin privileges')

    @override
    def __rich_repr__(self) -> RichReprRtn:
        yield 'username', self.username
        yield 'session_path', self.session_path


@frozen
class LogoutBundle(Bundle[None]):
    session_path: Path = field(validator=instance_of(Path))

    @override
    def vendor(self, client: httpxClient) -> SingleVendor[None]:
        logger.info('Logging out the client')
        res = yield client.build_request('POST', '/api/logout')
        res.raise_for_status()

        logger.info('Logout successful')
        with open(self.session_path, 'w'):
            pass

    @override
    def __rich_repr__(self) -> RichReprRtn:
        yield 'session_path', self.session_path


type CSLLists = dict[str, CSLList]


@frozen
class ListBundle(Bundle[CSLLists]):
    @override
    def vendor(self, client: httpxClient) -> SingleVendor[CSLLists]:
        logger.info('Fetching lists')
        res = yield client.build_request('GET', '/api/lists')
        res.raise_for_status()
        rtn: CSLLists = {}
        for data in res.json():
            csl_list = CSLList.from_json(data)
            rtn[csl_list.name] = csl_list
        return rtn

    @override
    def __rich_repr__(self) -> RichReprRtn:
        return
        yield


type CSLGroups = dict[str, CSLGroup]


@frozen
class GroupBundle(Bundle[CSLGroups]):
    @override
    def vendor(self, client: httpxClient) -> SingleVendor[CSLGroups]:
        logger.info('Fetching groups')
        res = yield client.build_request('GET', '/api/groups')
        res.raise_for_status()
        rtn: CSLGroups = {}
        for data in res.json():
            group = CSLGroup.from_json(data)
            rtn[group.name] = group
        return rtn

    @override
    def __rich_repr__(self) -> RichReprRtn:
        return
        yield
