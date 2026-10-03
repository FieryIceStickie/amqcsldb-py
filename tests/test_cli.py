import ast
import asyncio
import logging
from pathlib import Path

import pytest
from typer.testing import CliRunner

from amqcsl.cli import Templates, app
from amqcsl.exceptions import QuitError


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


@pytest.mark.parametrize('template', [Templates.character, Templates.character_compact])
@pytest.mark.parametrize('error_kind', ['direct', 'grouped', 'mixed'])
def test_character_template_logs_quit_and_preserves_other_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    template: Templates,
    error_kind: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(app, ['make', 'script.py', '--template', template.value])
    assert result.exit_code == 0, result.output
    script = ast.parse((tmp_path / 'script.py').read_text())
    entrypoint = script.body[-1]
    assert isinstance(entrypoint, ast.If)
    handler = entrypoint.body[-1]
    assert isinstance(handler, ast.TryStar)
    code = compile(ast.Module(body=[handler], type_ignores=[]), 'script.py', 'exec')
    logger = logging.getLogger('template-test')

    async def main(_: logging.Logger) -> None:
        if error_kind == 'direct':
            raise QuitError
        errors: list[Exception] = [QuitError()]
        if error_kind == 'mixed':
            errors.append(ValueError('Other error'))
        raise ExceptionGroup('Processing failed', errors)

    namespace = {'asyncio': asyncio, 'main': main, 'logger': logger, 'QuitError': QuitError}
    with caplog.at_level(logging.INFO):
        if error_kind == 'mixed':
            with pytest.raises(ExceptionGroup) as caught:
                exec(code, namespace)  # noqa: S102 -- run only the generated quit handler with a fake workflow
            assert len(caught.value.exceptions) == 1
            assert isinstance(caught.value.exceptions[0], ValueError)
        else:
            exec(code, namespace)  # noqa: S102 -- run only the generated quit handler with a fake workflow
    assert 'Quit requested; exiting.' in caplog.text
