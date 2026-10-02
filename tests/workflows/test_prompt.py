import pytest

from amqcsl.exceptions import QuitError
from amqcsl.workflows._workflow_utils import prompt


@pytest.mark.parametrize('answer, expected', [('y', True), ('YES', True), (' n ', False), ('No', False)])
def test_prompt_answers(monkeypatch: pytest.MonkeyPatch, answer: str, expected: bool) -> None:
    def read(_: str) -> str:
        return answer

    monkeypatch.setattr('builtins.input', read)
    assert prompt() is expected


@pytest.mark.parametrize('answer', ['q', 'QUIT'])
def test_prompt_quit(monkeypatch: pytest.MonkeyPatch, answer: str) -> None:
    def read(_: str) -> str:
        return answer

    monkeypatch.setattr('builtins.input', read)
    with pytest.raises(QuitError):
        prompt()


@pytest.mark.parametrize('default', [False, True])
def test_prompt_empty_default(monkeypatch: pytest.MonkeyPatch, default: bool) -> None:
    def read(_: str) -> str:
        return ''

    monkeypatch.setattr('builtins.input', read)
    assert prompt(continue_on_empty=True, default=default) is default


@pytest.mark.parametrize('pretty', [False, True])
def test_prompt_retries_invalid_and_empty_answers(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    pretty: bool,
) -> None:
    answers = iter(['invalid', '', 'yes'])

    def read(_: str) -> str:
        return next(answers)

    monkeypatch.setattr('builtins.input', read)
    assert prompt('Example', pretty=pretty)
    assert 'Example' in capsys.readouterr().out
