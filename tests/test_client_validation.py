import asyncio
import logging
from pathlib import Path
from typing import Any

import niquests
import pytest
from helpers import finish, mock_response
from niquests import HTTPError
from niquests_mock import MockRouter as Router

from amqcsl import AsyncDBClient, DBClient
from amqcsl.exceptions import ClientDoesNotExistError, LoginError


@pytest.fixture(params=[DBClient, AsyncDBClient], ids=['sync', 'async'])
def client_class(request: pytest.FixtureRequest) -> type[DBClient] | type[AsyncDBClient]:
    return request.param


@pytest.mark.parametrize(
    'kwargs, error',
    [
        ({'max_batch_size': 0}, ValueError),
        ({'max_batch_size': -1}, ValueError),
        ({'max_query_size': 0}, ValueError),
        ({'max_query_size': -1}, ValueError),
    ],
)
def test_constructor_rejects_nonpositive_limits(
    client_class: type[DBClient] | type[AsyncDBClient],
    kwargs: dict[str, Any],
    error: type[Exception],
) -> None:
    with pytest.raises(error):
        client_class(**kwargs)


@pytest.mark.parametrize('value, error', [(0, ValueError), (-1, ValueError), (51, ValueError)])
def test_async_request_limit_validation(value: Any, error: type[Exception]) -> None:
    with pytest.raises(error):
        AsyncDBClient(max_request_count=value)


@pytest.mark.parametrize('value', [1, 50])
def test_async_request_limit_boundaries(value: int) -> None:
    assert AsyncDBClient(max_request_count=value).max_request_count == value


@pytest.mark.parametrize('value', [0, -1, 51])
def test_invalid_request_limit_assignment_preserves_previous_limit(value: int) -> None:
    client = AsyncDBClient(max_request_count=3)
    with pytest.raises(ValueError):
        client.max_request_count = value
    assert client.max_request_count == 3


@pytest.mark.parametrize('value', [1, 50])
def test_request_limit_assignment_accepts_boundaries(value: int) -> None:
    client = AsyncDBClient()
    client.max_request_count = value
    assert client.max_request_count == value


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
    closed_sessions: list[niquests.Session | niquests.AsyncSession],
) -> None:
    path = tmp_path / 'session.txt'
    if scenario == 'directory':
        path.mkdir()
    elif scenario == 'not_admin':
        path.write_text(mock_id)
        _ = router['auth_you'].mock(return_value=mock_response(200, json={'name': username, 'roles': ['USER']}))
    if scenario == 'forbidden':
        _ = router['login_you'].mock(return_value=mock_response(403))
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
    assert client.client in closed_sessions
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
    assert router['auth_none'].call_count == router['login_you'].call_count == 1
    assert router['auth_you'].call_count == router['logout_you'].call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('json_response', [False, True])
async def test_http_error_closes_client_with_or_without_json(
    client_class: type[DBClient] | type[AsyncDBClient],
    router: Router,
    tmp_path: Path,
    mock_id: str,
    json_response: bool,
    closed_sessions: list[niquests.Session | niquests.AsyncSession],
) -> None:
    path = tmp_path / 'session.txt'
    path.write_text(mock_id)
    response = mock_response(500, json={'error': 'failed'}) if json_response else mock_response(500, text='Not JSON')
    route = router.get(path='/test-error').mock(return_value=response)
    client = client_class(session_path=path)
    with pytest.raises(HTTPError) as error:
        match client:
            case DBClient():
                with client:
                    client.client.get('/test-error').raise_for_status()
            case AsyncDBClient():
                async with client:
                    (await client.client.get('/test-error')).raise_for_status()
    assert error.value.response is not None
    assert error.value.response.status_code == 500
    assert route.call_count == 1
    assert client.client in closed_sessions


@pytest.mark.asyncio
@pytest.mark.parametrize('http_error', [False, True])
async def test_exit_closes_client_when_logging_fails(
    client_class: type[DBClient] | type[AsyncDBClient],
    router: Router,
    tmp_path: Path,
    mock_id: str,
    monkeypatch: pytest.MonkeyPatch,
    http_error: bool,
    closed_sessions: list[niquests.Session | niquests.AsyncSession],
) -> None:
    path = tmp_path / 'session.txt'
    path.write_text(mock_id)
    route = router.get(path='/test-error').mock(return_value=mock_response(500, json={'error': 'failed'}))
    client = client_class(session_path=path)

    def fail_logging(*args: object, **kwargs: object) -> None:
        raise RuntimeError('Logging failed')

    def break_logging() -> None:
        logger = logging.getLogger('amqcsl.client')
        monkeypatch.setattr(logger, 'error' if http_error else 'info', fail_logging)

    with pytest.raises(RuntimeError, match='Logging failed'):
        match client:
            case DBClient():
                with client:
                    break_logging()
                    if http_error:
                        client.client.get('/test-error').raise_for_status()
            case AsyncDBClient():
                async with client:
                    break_logging()
                    if http_error:
                        (await client.client.get('/test-error')).raise_for_status()
    assert route.call_count == int(http_error)
    assert client.client in closed_sessions


@pytest.fixture
def closed_sessions(monkeypatch: pytest.MonkeyPatch) -> list[niquests.Session | niquests.AsyncSession]:
    """Record session closure while still executing real transport cleanup."""
    closed: list[niquests.Session | niquests.AsyncSession] = []
    sync_close = niquests.Session.close
    async_close = niquests.AsyncSession.close

    def close(session: niquests.Session) -> None:
        sync_close(session)
        closed.append(session)

    async def aclose(session: niquests.AsyncSession) -> None:
        await async_close(session)
        closed.append(session)

    monkeypatch.setattr(niquests.Session, 'close', close)
    monkeypatch.setattr(niquests.AsyncSession, 'close', aclose)
    return closed


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['auth_you', 'lists', 'groups'])
@pytest.mark.parametrize('cancel', [False, True])
async def test_async_initialization_failure_closes_session(
    router: Router,
    tmp_path: Path,
    mock_id: str,
    closed_sessions: list[niquests.Session | niquests.AsyncSession],
    stage: str,
    cancel: bool,
) -> None:
    path = tmp_path / 'session.txt'
    path.write_text(mock_id)
    client = AsyncDBClient(session_path=path)
    started = asyncio.Event()

    async def wait_for_cancellation(request: niquests.PreparedRequest) -> niquests.Response:
        started.set()
        await asyncio.Event().wait()
        pytest.fail('Initialization unexpectedly resumed')

    if cancel:
        router[stage].mock(side_effect=wait_for_cancellation)
        task = asyncio.create_task(client.__aenter__())
        await asyncio.wait_for(started.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        router[stage].respond(status_code=500)
        with pytest.raises(HTTPError) as error:
            async with client:
                pytest.fail('Initialization unexpectedly succeeded')
        assert error.value.response is not None
        assert error.value.response.status_code == 500
    assert closed_sessions == [client.client]
