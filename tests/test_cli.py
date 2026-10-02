from pathlib import Path

import pytest
from typer.testing import CliRunner

from amqcsl.cli import Templates, app


@pytest.mark.parametrize('session_path', ['', 'custom-session.txt'])
def test_init_creates_config_and_directories(tmp_path: Path, session_path: str) -> None:
    destination = tmp_path / 'project'
    result = CliRunner().invoke(app, ['init', str(destination)], input=f'username\npassword\n{session_path}\n')
    assert result.exit_code == 0, result.output
    resolved_session = session_path or 'amq_session.txt'
    assert (
        destination / '.env'
    ).read_text() == f'AMQ_USERNAME="username"\nAMQ_PASSWORD="password"\nSESSION_PATH="{resolved_session}"\n'
    assert (destination / '.gitignore').read_text().splitlines() == [resolved_session, '.env', 'logs']
    assert all((destination / name).is_dir() for name in ['logs', 'log', 'scripts'])


def test_init_existing_log_directory_leaves_config_unchanged(tmp_path: Path) -> None:
    (tmp_path / 'log').mkdir()
    config = tmp_path / '.env'
    config.write_text('EXISTING=true\n')
    result = CliRunner().invoke(app, ['init', str(tmp_path)])
    assert result.exit_code == 1
    assert 'already exists' in result.output
    assert config.read_text() == 'EXISTING=true\n'


@pytest.mark.parametrize('template', [*Templates])
def test_make_copies_each_template(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    template: Templates,
) -> None:
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(app, ['make', 'script.py', '--template', template.value])
    assert result.exit_code == 0, result.output
    script = tmp_path / 'script.py'
    assert script.exists()
    compile(script.read_text(), str(script), 'exec')


def test_make_refuses_existing_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    path = tmp_path / 'script.py'
    path.write_text('original')
    result = CliRunner().invoke(app, ['make', path.name])
    assert result.exit_code != 0
    assert isinstance(result.exception, FileExistsError)
    assert path.read_text() == 'original'
