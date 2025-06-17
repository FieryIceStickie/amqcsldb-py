# pyright: reportUnusedExpression=false
from collections.abc import Iterator
from pathlib import Path

import pytest
import respx
from helpers import load
from httpx import Response
from respx import Router, SetCookie

import amqcsl
from amqcsl._client_consts import DB_URL


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


# Don't remove router, necessary for fixture dependency
@pytest.fixture
def client(tmp_path: Path, router: Router, mock_id: str):
    session_path = tmp_path / 'amq_session.txt'
    session_path.write_text(mock_id)
    with amqcsl.DBClient(session_path=session_path) as client:
        yield client


@pytest.fixture
def router(
    username: str,
    password: str,
    mock_id: str,
    cookies: Cookie,
) -> Iterator[Router]:
    with respx.mock(base_url=DB_URL, assert_all_called=False) as router:
        add_login(router, username, password, mock_id, cookies)
        add_lists_and_groups(router, cookies)
        yield router


def add_login(
    router: Router,
    username: str,
    password: str,
    mock_id: str,
    cookies: Cookie,
) -> None:
    router.get(
        '/api/auth/me',
        name='auth_you',
        cookies=cookies,
    ) % Response(
        200,
        json={'name': username, 'roles': ['ADMIN', 'USER']},
    )
    router.post(
        '/api/login',
        name='login_you',
        json={
            'username': username,
            'password': password,
        },
    ) % Response(
        200,
        headers=[SetCookie('session-id', mock_id)],
    )
    router.post(
        '/api/logout',
        name='logout_you',
        cookies=cookies,
    ) % Response(
        200,
        headers=[SetCookie('session-id', '')],
    )

    router.get('/api/auth/me', name='auth_none') % 401


def add_lists_and_groups(router: Router, cookies: Cookie):
    router.get('/api/lists', name='lists', cookies=cookies) % Response(200, json=load('lists'))
    router.get('/api/groups', name='groups', cookies=cookies) % Response(200, json=load('groups'))
