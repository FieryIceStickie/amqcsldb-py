import json
from pathlib import Path

import pytest
from helpers import mock_response
from niquests_mock import MockRouter as Router
from typer.testing import CliRunner

from amqcsl.cli import app
from amqcsl.objects import CSLTrack
from amqcsl.queues import GroupedQueue, StandardQueue


@pytest.mark.parametrize('queue_class', [StandardQueue, GroupedQueue])
def test_display_loads_saved_queue_without_network(
    tmp_path: Path,
    track: CSLTrack,
    queue_class: type[StandardQueue] | type[GroupedQueue],
) -> None:
    queue = queue_class()
    queue.track_edit(track, name='Proposed name')
    path = tmp_path / 'queue.json'
    with path.open('w') as stream:
        queue.to_file(stream)
    original = path.read_bytes()
    result = CliRunner().invoke(app, ['queue', 'display', str(path)])
    assert result.exit_code == 0, result.output
    assert 'Proposed name' in result.output and track.id in result.output
    assert path.read_bytes() == original


@pytest.mark.parametrize('queue_class', [StandardQueue, GroupedQueue])
@pytest.mark.parametrize('failure', [False, True])
def test_run_processes_bundles_and_outputs_remaining_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    router: Router,
    track: CSLTrack,
    username: str,
    password: str,
    queue_class: type[StandardQueue] | type[GroupedQueue],
    failure: bool,
) -> None:
    monkeypatch.chdir(tmp_path)
    for name in ('AMQ_USERNAME', 'AMQ_PASSWORD', 'SESSION_PATH'):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / '.env').write_text(f'AMQ_USERNAME={username}\nAMQ_PASSWORD={password}\nSESSION_PATH=cli-session.txt\n')
    queue = queue_class()
    queue.track_edit(track, name='Proposed name')
    path = tmp_path / 'queue.json'
    with path.open('w') as stream:
        queue.to_file(stream)
    original = path.read_bytes()
    edit = router.put(path=f'/api/track/{track.id}').mock(return_value=mock_response(500 if failure else 200))
    result = CliRunner().invoke(app, ['queue', 'run', str(path)])
    assert (result.exit_code != 0) is failure, result.output
    assert edit.call_count == 1
    assert router['login_you'].called
    assert (tmp_path / 'cli-session.txt').exists()
    restored = queue_class.from_json(json.loads(result.stdout))
    assert len(restored) == int(failure)
    assert path.read_bytes() == original


@pytest.mark.parametrize('command', ['run', 'display'])
@pytest.mark.parametrize(
    'document',
    ['not json', '[]', '{"queue_type": "unknown"}', '{"queue_type": "grouped", "version": "9.0"}'],
)
def test_invalid_queue_is_rejected_before_execution(tmp_path: Path, command: str, document: str) -> None:
    path = tmp_path / 'queue.json'
    path.write_text(document)
    result = CliRunner().invoke(app, ['queue', command, str(path)])
    assert result.exit_code == 2
    assert 'Cannot load queue' in result.output
    assert path.read_text() == document


@pytest.mark.parametrize('command', ['run', 'display'])
def test_queue_command_requires_an_existing_file(tmp_path: Path, command: str) -> None:
    for path in (tmp_path, tmp_path / 'missing.json'):
        result = CliRunner().invoke(app, ['queue', command, str(path)])
        assert result.exit_code == 2
