from __future__ import annotations

import asyncio
from collections.abc import Sequence

import rich
from rich.console import Group
from rich.panel import Panel
from rich.table import Column, Table
from rich.text import Text

from amqcsl.exceptions import QuitError
from amqcsl.objects._db_types import CSLTrack

from .types import INCOMPLETE_GROUP, ExcludeDecision, Reason

_EXCLUSION_QUESTION = '[Y] Exclude  [N] Error  [I] Ignore track  [Q] Quit › '


def _exclusion_panel(track: CSLTrack, artists: Sequence[Reason]) -> Panel:
    """Build a panel showing the track and unresolved artist metadata."""
    details = Table.grid(
        Column(style='dim', no_wrap=True),
        Column(ratio=1),
        padding=(0, 2),
        expand=True,
    )
    details.add_row('Track', Text(track.name if track.name is not None else track.original_name, style='bold'))
    details.add_row('ID', Text(track.id, style='dim'))
    details.add_row(
        'Artists',
        Text(', '.join(dict.fromkeys(credit.artist.name for credit in track.artist_credits)), style='cyan'),
    )
    reasons: list[Text] = []
    for failure in artists:
        reason = Text('⚠ ', style='yellow')
        reason.append(failure.artist.name, style='cyan')
        match failure.reason:
            case INCOMPLETE_GROUP(artists=members, known_artists=known):
                reason.append(' — incomplete group', style='yellow')
                if known:
                    reason.append('\n  Known members: ', style='dim')
                    reason.append(', '.join(dict.fromkeys(member.name for member in known)), style='green')
                if members:
                    reason.append('\n  Members without character metadata: ', style='dim')
                    reason.append(', '.join(dict.fromkeys(member.name for member in members)), style='cyan')
            case _:
                reason.append(' — unknown artist', style='yellow')
        reasons.append(reason)
    return Panel(Group(details, Text(''), *reasons), title='Unknown metadata found', title_align='left', width=100)


def _parse_exclusion(answer: str) -> ExcludeDecision | None:
    """Parse a decision, returning None for an unrecognized answer."""
    match answer.lower().strip():
        case 'y' | 'yes' | 'exclude':
            return ExcludeDecision.EXCLUDE
        case 'n' | 'no' | 'error':
            return ExcludeDecision.ERROR
        case 'i' | 'ignore':
            return ExcludeDecision.IGNORE
        case 'q' | 'quit':
            raise QuitError
        case _:
            rich.print('[yellow]Unknown answer. Choose Y, N, I, or Q.[/yellow]')
            return None


def prompt_should_exclude(track: CSLTrack, artists: Sequence[Reason]) -> ExcludeDecision:
    """Ask whether to exclude unresolved artists, raise an error, or ignore the track."""
    rich.print(_exclusion_panel(track, artists))
    while True:
        decision = _parse_exclusion(input(_EXCLUSION_QUESTION))
        if decision is not None:
            return decision


async def _read_exclusion() -> str:
    """Wait for terminal input to finish before propagating cancellation."""
    task = asyncio.create_task(asyncio.to_thread(input, _EXCLUSION_QUESTION))
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
        except BaseException:
            if cancelled:
                raise asyncio.CancelledError from None
            raise
    if cancelled:
        if not task.cancelled():
            task.exception()
        raise asyncio.CancelledError
    return task.result()


async def async_prompt_should_exclude(track: CSLTrack, artists: Sequence[Reason]) -> ExcludeDecision:
    """Ask for an exclusion decision without blocking async network requests."""
    rich.print(_exclusion_panel(track, artists))
    while True:
        decision = _parse_exclusion(await _read_exclusion())
        if decision is not None:
            return decision
