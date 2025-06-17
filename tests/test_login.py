# pyright: reportUnusedExpression=false
from pathlib import Path

import pytest
from respx import Router

import amqcsl


def test_login_with_session(tmp_path: Path, router: Router, mock_id: str):
    session_path = tmp_path / 'amq_session.txt'
    session_path.write_text(mock_id)
    with amqcsl.DBClient(session_path=session_path) as client:
        assert client.session_path == session_path
        assert session_path.read_text() == mock_id

    routes = router.routes
    assert routes['auth_you'].call_count == 1
    assert routes['auth_none'].call_count == 0


@pytest.mark.parametrize('fake_id', ['not-mock-session-id', ''])
def test_login_with_details(
    tmp_path: Path,
    router: Router,
    mock_id: str,
    username: str,
    password: str,
    fake_id: str,
):
    session_path = tmp_path / 'amq_session.txt'
    assert fake_id != mock_id
    if fake_id:
        session_path.write_text(fake_id)

    with amqcsl.DBClient(
        username=username,
        password=password,
        session_path=session_path,
    ) as client:
        assert client.session_path == session_path
        assert session_path.read_text() == mock_id

    routes = router.routes
    assert routes['auth_none'].call_count == bool(fake_id)
    assert routes['login_you'].call_count == 1
    assert routes['auth_you'].call_count == 1


def test_logout(tmp_path: Path, router: Router, mock_id: str):
    session_path = tmp_path / 'amq_session.txt'
    session_path.write_text(mock_id)
    with amqcsl.DBClient(session_path=session_path) as client:
        client.logout()
        assert not session_path.read_text()

    routes = router.routes
    assert routes['logout_you'].call_count == 1
