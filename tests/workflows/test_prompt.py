from io import StringIO
from typing import cast

import pytest
from attrs import evolve
from helpers import load
from rich.console import Console

from amqcsl.exceptions import QuitError
from amqcsl.objects import (
    CSLTrack,
    from_json,
)
from amqcsl.objects._json_types import JSONTrack
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
        assert '[I] Ignore track' in message
        return answer

    monkeypatch.setattr('builtins.input', read)
    assert cm.prompt_should_exclude(from_json(cast(JSONTrack, load('sunshine/tracks')[0]), CSLTrack), []) is expected


@pytest.mark.parametrize('answer', ['q', 'QUIT'])
def test_exclusion_prompt_quit(monkeypatch: pytest.MonkeyPatch, answer: str) -> None:
    def read(_: str) -> str:
        return answer

    monkeypatch.setattr('builtins.input', read)
    with pytest.raises(QuitError):
        cm.prompt_should_exclude(from_json(cast(JSONTrack, load('sunshine/tracks')[0]), CSLTrack), [])


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
    t = from_json(cast(JSONTrack, load('sunshine/tracks')[0]), CSLTrack)
    assert cm.prompt_should_exclude(t, []) is cm.ExcludeDecision.IGNORE
    assert len(prompts) == 3
    output = capsys.readouterr().out
    assert t.id in output
    assert output.count('Unknown answer. Choose Y, N, I, or Q.') == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('answer', ['ignore', 'quit'])
async def test_async_exclusion_prompt_retries_and_parses(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    answer: str,
) -> None:
    answers = iter(['invalid', '', answer])

    def read(_: str) -> str:
        return next(answers)

    monkeypatch.setattr('builtins.input', read)
    track = from_json(cast(JSONTrack, load('sunshine/tracks')[0]), CSLTrack)
    if answer == 'quit':
        with pytest.raises(QuitError):
            await cm.async_prompt_should_exclude(track, [])
    else:
        assert await cm.async_prompt_should_exclude(track, []) is cm.ExcludeDecision.IGNORE
    assert capsys.readouterr().out.count('Unknown answer. Choose Y, N, I, or Q.') == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('use_async', [False, True])
async def test_exclusion_display_is_compact_and_preserves_literal_names(
    monkeypatch: pytest.MonkeyPatch,
    use_async: bool,
) -> None:
    output = StringIO()
    console = Console(file=output, width=70, color_system=None)
    monkeypatch.setattr('rich.get_console', lambda: console)

    def read(_: str) -> str:
        return 'ignore'

    monkeypatch.setattr('builtins.input', read)
    track = from_json(cast(JSONTrack, load('sunshine/tracks')[0]), CSLTrack)
    credit = track.artist_credits[0]
    recognised = evolve(credit.artist, id='known', name='Known [bold]artist[/bold]')
    unknown = evolve(credit.artist, id='guest', name='Guest Singer')
    group = evolve(credit.artist, id='group', name='Special Unit', type_id=3)
    member = evolve(credit.artist, id='member', name='Another Singer')
    track = evolve(
        track,
        id='test-track-id',
        name=None,
        original_name='Test song',
        artist_credits=[evolve(credit, artist=artist) for artist in [recognised, recognised, unknown, group]],
    )
    reasons = [cm.Reason(unknown, cm.UNKNOWN_ARTIST), cm.Reason(group, cm.INCOMPLETE_GROUP([member], [recognised]))]
    if use_async:
        assert await cm.async_prompt_should_exclude(track, reasons) is cm.ExcludeDecision.IGNORE
    else:
        assert cm.prompt_should_exclude(track, reasons) is cm.ExcludeDecision.IGNORE
    rendered = output.getvalue()
    assert '╭─ Unknown metadata found' in rendered and '╰' in rendered
    assert 'test-track-id' in rendered and 'Test song' in rendered
    assert rendered.count('Known [bold]artist[/bold]') == 2
    assert 'Guest Singer — unknown artist' in rendered
    assert 'Special Unit — incomplete group' in rendered
    assert 'Known members: Known [bold]artist[/bold]' in rendered
    assert 'Members without character metadata: Another Singer' in rendered
    assert 'CSLTrack(' not in rendered and 'type_id' not in rendered and 'original_name' not in rendered
