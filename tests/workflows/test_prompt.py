import pytest
from helpers import load

from amqcsl.exceptions import QuitError
from amqcsl.objects import CSLTrack
from amqcsl.workflows import character as cm
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


@pytest.mark.parametrize(
    'answer, expected',
    [
        ('y', cm.ExcludeDecision.EXCLUDE),
        ('YES', cm.ExcludeDecision.EXCLUDE),
        ('exclude', cm.ExcludeDecision.EXCLUDE),
        (' n ', cm.ExcludeDecision.ERROR),
        ('No', cm.ExcludeDecision.ERROR),
        ('error', cm.ExcludeDecision.ERROR),
        ('i', cm.ExcludeDecision.IGNORE),
        (' IGNORE ', cm.ExcludeDecision.IGNORE),
    ],
)
def test_exclusion_prompt_decisions(
    monkeypatch: pytest.MonkeyPatch,
    answer: str,
    expected: cm.ExcludeDecision,
) -> None:
    def read(message: str) -> str:
        assert 'I(gnore track)' in message
        return answer

    monkeypatch.setattr('builtins.input', read)
    assert cm.prompt_should_exclude(CSLTrack.from_json(load('sunshine/tracks')[0]), []) is expected


@pytest.mark.parametrize('answer', ['q', 'QUIT'])
def test_exclusion_prompt_quit(monkeypatch: pytest.MonkeyPatch, answer: str) -> None:
    def read(_: str) -> str:
        return answer

    monkeypatch.setattr('builtins.input', read)
    with pytest.raises(QuitError):
        cm.prompt_should_exclude(CSLTrack.from_json(load('sunshine/tracks')[0]), [])


def test_exclusion_prompt_retries_invalid_answers(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    answers = iter(['invalid', '', 'ignore'])
    prompts: list[str] = []

    def read(message: str) -> str:
        prompts.append(message)
        return next(answers)

    monkeypatch.setattr('builtins.input', read)
    t = CSLTrack.from_json(load('sunshine/tracks')[0])
    assert cm.prompt_should_exclude(t, []) is cm.ExcludeDecision.IGNORE
    assert len(prompts) == 3
    assert t.id in capsys.readouterr().out
