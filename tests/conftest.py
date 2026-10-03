import re

# pyright: reportUnusedExpression=false
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import niquests_mock
import pytest
import pytest_asyncio
from helpers import load, mock_response
from niquests_mock import MockRouter as Router

import amqcsl
from amqcsl.clients._client_consts import DB_URL


@pytest.fixture
def mock_id() -> str:
    return 'mock-session-id'


type Cookie = dict[str, str]


@pytest.fixture
def cookies(mock_id: str) -> Cookie:
    return {'session-id': mock_id}


@pytest.fixture
def username() -> str:
    return 'YouWatanabe'


@pytest.fixture
def password() -> str:
    return 'Yousoro'


@pytest.fixture
def router(
    username: str,
    password: str,
    mock_id: str,
    cookies: Cookie,
) -> Iterator[Router]:
    with niquests_mock.mock(base_url=DB_URL, assert_all_called=False) as router:
        add_login(router, username, password, mock_id, cookies)
        add_lists_and_groups(router)
        yield router


def add_login(
    router: Router,
    username: str,
    password: str,
    mock_id: str,
    cookies: Cookie,
) -> None:
    router.get(
        path='/api/auth/me', name='auth_none', headers={'Cookie': re.compile(rf'^(?!session-id={re.escape(mock_id)}$)')}
    ).respond(status_code=401)
    router.get(
        path='/api/auth/me',
        name='auth_you',
        headers={'Cookie': f'session-id={cookies["session-id"]}'},
    ).mock(
        return_value=mock_response(
            200,
            json={'name': username, 'roles': ['ADMIN', 'USER']},
        )
    )
    router.post(
        '/api/login',
        name='login_you',
        json={
            'username': username,
            'password': password,
        },
    ).respond(status_code=200, headers={'Set-Cookie': f'session-id={mock_id}; Path=/'})
    router.post(
        '/api/logout',
        name='logout_you',
        headers={'Cookie': f'session-id={cookies["session-id"]}'},
    ).respond(status_code=200, headers={'Set-Cookie': 'session-id=; Path=/'})


def add_lists_and_groups(router: Router):
    router.get(path='/api/lists', name='lists').mock(return_value=mock_response(200, json=load('lists')))
    router.get(path='/api/groups', name='groups').mock(return_value=mock_response(200, json=load('groups')))


@pytest_asyncio.fixture(params=['sync', 'async'])
async def client(
    request: pytest.FixtureRequest,
    tmp_path: Path,
    router: Router,
    mock_id: str,
) -> AsyncIterator[amqcsl.DBClient | amqcsl.AsyncDBClient]:
    """Enter only the selected client, preserving its initialization behavior."""
    session_path = tmp_path / 'amq_session.txt'
    session_path.write_text(mock_id)
    if request.param == 'sync':
        with amqcsl.DBClient(session_path=session_path) as client:
            yield client
    else:
        async with amqcsl.AsyncDBClient(session_path=session_path) as client:
            yield client
