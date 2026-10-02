from pathlib import Path
from typing import Any

import pytest
from helpers import finish
from httpx import Response
from respx import Router

from amqcsl import AsyncDBClient, DBClient
from amqcsl.exceptions import ClientDoesNotExistError, LoginError


@pytest.fixture(params=[DBClient, AsyncDBClient], ids=['sync', 'async'])
def client_class(request: pytest.FixtureRequest) -> type[DBClient] | type[AsyncDBClient]:
    return request.param


@pytest.mark.parametrize(
    'kwargs, error',
    [
        ({'username': 1}, TypeError),
        ({'password': 1}, TypeError),
        ({'max_batch_size': 0}, ValueError),
        ({'max_batch_size': -1}, ValueError),
        ({'max_batch_size': '10'}, TypeError),
        ({'max_batch_size': 1.5}, TypeError),
        ({'max_query_size': 0}, ValueError),
        ({'max_query_size': -1}, ValueError),
        ({'max_query_size': '10'}, TypeError),
        ({'max_query_size': 1.5}, TypeError),
    ],
)
def test_constructor_rejects_invalid_limits_and_credentials(
    client_class: type[DBClient] | type[AsyncDBClient],
    kwargs: dict[str, Any],
    error: type[Exception],
) -> None:
    with pytest.raises(error):
        client_class(**kwargs)


@pytest.mark.parametrize(
    'value, error', [(0, ValueError), (-1, ValueError), (51, ValueError), ('5', TypeError), (1.5, TypeError)]
)
def test_async_request_limit_validation(value: Any, error: type[Exception]) -> None:
    with pytest.raises(error):
        AsyncDBClient(max_request_count=value)


@pytest.mark.parametrize('value', [1, 50])
def test_async_request_limit_boundaries(value: int) -> None:
    assert AsyncDBClient(max_request_count=value).max_request_count == value


def test_session_path_accepts_string_or_path(
    client_class: type[DBClient] | type[AsyncDBClient],
    tmp_path: Path,
) -> None:
    path = tmp_path / 'session.txt'
    assert client_class(session_path=str(path)).session_path == path
    assert client_class(session_path=path).session_path == path


def test_underlying_client_unavailable_before_entry(
    client_class: type[DBClient] | type[AsyncDBClient],
) -> None:
    client = client_class()
    with pytest.raises(ClientDoesNotExistError):
        _ = client.client


@pytest.mark.asyncio
@pytest.mark.parametrize('scenario', ['missing_credentials', 'forbidden', 'not_admin', 'directory'])
async def test_auth_failure_closes_client(
    client_class: type[DBClient] | type[AsyncDBClient],
    router: Router,
    tmp_path: Path,
    mock_id: str,
    username: str,
    password: str,
    scenario: str,
) -> None:
    path = tmp_path / 'session.txt'
    if scenario == 'directory':
        path.mkdir()
    elif scenario == 'not_admin':
        path.write_text(mock_id)
        _ = router.routes['auth_you'] % Response(200, json={'name': username, 'roles': ['USER']})
    if scenario == 'forbidden':
        _ = router.routes['login_you'] % Response(403)
    client = client_class(
        username=None if scenario == 'missing_credentials' else username,
        password=password,
        session_path=path,
    )
    error = FileNotFoundError if scenario == 'directory' else LoginError
    with pytest.raises(error):
        if isinstance(client, DBClient):
            with client:
                pytest.fail('Authentication unexpectedly succeeded')
        else:
            async with client:
                pytest.fail('Authentication unexpectedly succeeded')
    assert client.client.is_closed
    if scenario != 'directory':
        assert path.read_text() == mock_id if scenario == 'not_admin' else not path.exists()


@pytest.mark.asyncio
async def test_async_login_fallback_and_logout(
    router: Router,
    tmp_path: Path,
    mock_id: str,
    username: str,
    password: str,
) -> None:
    path = tmp_path / 'session.txt'
    path.write_text('expired-cookie')
    async with AsyncDBClient(username=username, password=password, session_path=path) as client:
        assert path.read_text() == mock_id
        await finish(client.logout())
        assert path.read_text() == ''
    assert router.routes['auth_none'].call_count == router.routes['login_you'].call_count == 1
    assert router.routes['auth_you'].call_count == router.routes['logout_you'].call_count == 1
