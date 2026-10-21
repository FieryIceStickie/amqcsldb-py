"""Render pending target groups as compact identity panels and edit sections."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from typing import TYPE_CHECKING, cast

from attrs import evolve, frozen
from rich.console import Group
from rich.padding import Padding
from rich.panel import Panel
from rich.table import Column, Table
from rich.text import Text

from amqcsl.bundles._misc import (
    AddAudioBundle,
    ImportAudioBundle,
    SongAddMetadataBundle,
    SongDeleteBundle,
    SongDeleteMetadataBundle,
    SongEditBundle,
    TrackAddMetadataBundle,
    TrackDeleteMetadataBundle,
    TrackEditBundle,
)
from amqcsl.objects._db_types import (
    ArtistCredit,
    CSLArtistSample,
    CSLExtraMetadata,
    CSLGroup,
    CSLSongArtistCredit,
    CSLSongSample,
    CSLTrack,
    CSLTrackArtistCredit,
    ExtraMetadata,
    NewSong,
    TrackPutArtistCredit,
)

if TYPE_CHECKING:
    from ._core import GroupTarget, PendingGroup

_SECTION_ORDER = ('Groups', 'Artist credits', 'Song', 'Metadata', 'Audio')


@frozen
class DisplayRow:
    """Store one literal label/value row and its position within an edit section.

    Attributes:
        label: Plain label, including an action marker when applicable.
        value: Styled value with user-supplied strings treated literally.
        style: Label style.
        priority: Removals sort before additions, followed by explanatory rows.
    """

    label: str
    value: Text
    style: str = 'dim'
    priority: int = 2


def _value(value: str | None) -> str:
    """Represent missing and empty values explicitly in a displayed field."""
    return 'None' if value is None else '""' if value == '' else value


def _change(
    current: str | None,
    proposed: str | None,
) -> Text:
    """Build a field comparison with a dim current value and yellow proposal."""
    result = Text(_value(current), style='dim')
    result.append(' → ', style='dim')
    result.append(_value(proposed), style='yellow')
    return result


def _artist(
    artist: CSLArtistSample,
    *,
    style: str,
    credited_name: str | None = None,
) -> Text:
    """Format an artist's name, disambiguation, optional credit, and gray ID."""
    result = Text(artist.name, style=style)
    if artist.disambiguation:
        result.append(f' ({artist.disambiguation})')
    result.append(f' ({artist.id})', style='bright_black')
    if credited_name is not None and credited_name != artist.name:
        result.append(f' as "{credited_name}"', style=style)
    return result


def _credits(credits: Sequence[CSLTrackArtistCredit | TrackPutArtistCredit]) -> str:
    """Join ordered track credits using their credited names and join phrases."""
    return ''.join(f'{credit.name}{credit.join_phrase}' for credit in credits)


def _credit_rows(
    current: Sequence[CSLTrackArtistCredit],
    proposed: Sequence[TrackPutArtistCredit],
) -> list[DisplayRow]:
    """Describe removed and added credits, including aliases and join phrase changes.

    Compare the full credit so changing an alias or join phrase remains visible.
    Pure reorderings get a proposed-order row instead of fabricated additions.

    Args:
        current: Ordered artist credits on the target snapshot.
        proposed: Complete proposed credit list.

    Returns:
        Removed credits, added credits, and any order-only change.
    """
    old_keys = [(credit.artist.id, credit.name, credit.join_phrase) for credit in current]
    new_keys = [(credit.artist.id, credit.name, credit.join_phrase) for credit in proposed]
    rows: list[DisplayRow] = []
    for credits, remaining, marker, style, priority in (
        (current, Counter(new_keys), '−', 'red', 0),
        (proposed, Counter(old_keys), '+', 'green', 1),
    ):
        for credit in credits:
            key = credit.artist.id, credit.name, credit.join_phrase
            if remaining[key]:
                remaining[key] -= 1
                continue
            value = _artist(credit.artist, style=style, credited_name=credit.name)
            if credit.join_phrase:
                value.append(f'  join: {credit.join_phrase!r}', style='bright_black')
            rows.append(DisplayRow(marker, value, style, priority))
    if old_keys != new_keys and Counter(old_keys) == Counter(new_keys):
        rows.append(DisplayRow('Order', Text(_value(_credits(proposed)), style='yellow')))
    return rows


def _group_rows(
    current: Sequence[CSLGroup],
    proposed: Sequence[CSLGroup],
) -> list[DisplayRow]:
    """Show removed groups before added groups, comparing their database IDs."""
    rows: list[DisplayRow] = []
    for groups, other_ids, marker, style, priority in (
        (current, {group.id for group in proposed}, '−', 'red', 0),
        (proposed, {group.id for group in current}, '+', 'green', 1),
    ):
        for group in groups:
            if group.id not in other_ids:
                value = Text(group.name, style=style)
                value.append(f' ({group.id})', style='bright_black')
                rows.append(DisplayRow(marker, value, style, priority))
    return rows


def _song(song: CSLSongSample | NewSong | None) -> Text:
    """Format a linked or newly created song with disambiguation and any existing ID."""
    if song is None:
        return Text('None', style='dim')
    result = Text(song.name, style='green' if isinstance(song, NewSong) else 'cyan')
    if song.disambiguation:
        result.append(f' ({song.disambiguation})')
    if isinstance(song, CSLSongSample):
        result.append(f' ({song.id})', style='bright_black')
    return result


def _metadata_row(
    meta: ArtistCredit | CSLSongArtistCredit | ExtraMetadata | CSLExtraMetadata,
    *,
    added: bool,
) -> tuple[str, DisplayRow]:
    """Build a metadata action row for the extra or artist link section.

    Args:
        meta: Added metadata or an existing entry being removed.
        added: Whether this is an addition rather than a removal.

    Returns:
        Metadata section name and an action row labeled with its subgroup header.
    """
    style, priority = ('green', 1) if added else ('red', 0)
    match meta:
        case ArtistCredit():
            value = _artist(meta.artist, style=style, credited_name=meta.credit)
            return 'Metadata', DisplayRow(meta.type, value, style, priority)
        case CSLSongArtistCredit():
            value = _artist(meta.artist, style=style)
            return 'Metadata', DisplayRow(meta.type, value, style, priority)
        case ExtraMetadata():
            kind = 'Artist' if meta.is_artist else 'Song'
            label = f'{meta.type} ({kind})'
        case CSLExtraMetadata():
            label = f'{meta.key} ({meta.type})'
    return 'Metadata', DisplayRow(label, Text(meta.value, style=style), style, priority)


def _grid(rows: Sequence[DisplayRow]) -> Table:
    """Build a borderless two-column grid that preserves literal user strings."""
    grid = Table.grid(Column(no_wrap=True), Column(ratio=1, overflow='fold'), padding=(0, 2), expand=True)
    for row in rows:
        grid.add_row(Text(row.label, style=row.style), row.value)
    return grid


def _track_edit(
    bundle: TrackEditBundle,
    track: CSLTrack,
    fields: list[DisplayRow],
    sections: dict[str, list[DisplayRow]],
) -> None:
    """Collect a track edit's changed fields, group/credit diffs, and linked song.

    Args:
        bundle: Merged track update to describe.
        track: Current group snapshot used for all old field values.
        fields: Summary rows receiving explicitly supplied scalar fields.
        sections: Edit sections receiving replacement-list and song changes.
    """
    for label, old, new in (
        ('Name', track.name, bundle.name),
        ('Original', track.original_name, bundle.original_name),
        ('Original artist', track.original_simple_artist, bundle.original_artist),
        ('Type', track.type, bundle.type),
    ):
        if new is not None:
            fields.append(DisplayRow(label, _change(old, new)))
    if bundle.groups is not None:
        sections['Groups'].extend(_group_rows(track.groups, bundle.groups))
    if bundle.artist_credits is not None:
        sections['Artist credits'].extend(_credit_rows(track.artist_credits, bundle.artist_credits))
    if bundle.song is not None:
        sections['Song'].append(DisplayRow('Current', _song(track.song)))
        label = 'Create' if isinstance(bundle.song, NewSong) else 'Proposed'
        sections['Song'].append(DisplayRow(label, _song(bundle.song)))


def _identity(target: GroupTarget) -> tuple[str, list[DisplayRow]]:
    """Build permanent identity rows from the group's current target snapshot.

    Args:
        target: Track, song, or artist snapshot identifying the pending group.

    Returns:
        Panel title and its always-visible identity rows.
    """
    match target:
        case CSLTrack():
            title = 'Track'
            identity = [
                DisplayRow(
                    'Track',
                    Text(_value(target.name if target.name is not None else target.original_name), style='bold'),
                ),
                DisplayRow('ID', Text(target.id, style='bright_black')),
                DisplayRow('Artists', Text(_value(_credits(target.artist_credits) or None), style='cyan')),
            ]
        case CSLSongSample():
            title = 'Song'
            identity = [
                DisplayRow('Song', Text(target.name, style='bold')),
                DisplayRow('ID', Text(target.id, style='bright_black')),
                DisplayRow('Disambiguation', Text(_value(target.disambiguation), style='dim')),
            ]
        case CSLArtistSample():
            title = 'Artist'
            identity = [
                DisplayRow('Artist', Text(target.name, style='bold')),
                DisplayRow('ID', Text(target.id, style='bright_black')),
                DisplayRow('Disambiguation', Text(_value(target.disambiguation), style='dim')),
            ]
    return title, identity


def _edits(
    pending: PendingGroup[GroupTarget],
) -> tuple[list[DisplayRow], dict[str, list[DisplayRow]], bool]:
    """Collect all pending requests into field rows and named edit sections.

    Args:
        pending: Target group whose merged proposals should be displayed.

    Returns:
        Field rows, section rows, and whether the song is being deleted.
    """
    fields: list[DisplayRow] = []
    sections: dict[str, list[DisplayRow]] = {name: [] for name in _SECTION_ORDER}
    delete = False
    for bundle in pending.bundles:
        match bundle:
            case TrackEditBundle():
                _track_edit(bundle, cast(CSLTrack, pending.target), fields, sections)
            case SongEditBundle():
                song = cast(CSLSongSample, pending.target)
                if bundle.name is not None:
                    fields.append(DisplayRow('Name', _change(song.name, bundle.name)))
                if bundle.disambiguation is not None:
                    fields.append(DisplayRow('Disambiguation', _change(song.disambiguation, bundle.disambiguation)))
            case TrackAddMetadataBundle() | SongAddMetadataBundle():
                artists, extras = bundle.filtered_metas
                for meta in (*artists, *extras):
                    name, row = _metadata_row(meta, added=True)
                    sections[name].append(row)
                if isinstance(bundle, TrackAddMetadataBundle) and bundle._override is not None:  # pyright: ignore[reportPrivateUsage]
                    value = 'Enabled' if bundle._override else 'Disabled'  # pyright: ignore[reportPrivateUsage]
                    fields.append(DisplayRow('Metadata override', Text(value, style='yellow')))
            case TrackDeleteMetadataBundle() | SongDeleteMetadataBundle():
                name, row = _metadata_row(bundle.meta, added=False)
                sections[name].append(row)
            case AddAudioBundle():
                sections['Audio'].append(DisplayRow('Upload', Text(str(bundle.audio_path), style='green')))
            case ImportAudioBundle():
                source = bundle.track_to_import_from
                value = Text(_value(source.name if source.name is not None else source.original_name), style='cyan')
                artists = _credits(source.artist_credits)
                if artists:
                    value.append(f' — {artists}')
                value.append(f' ({source.id})', style='bright_black')
                sections['Audio'].append(DisplayRow('Import', value))
            case SongDeleteBundle():
                delete = True
            case _:
                raise TypeError(f'Unsupported display bundle: {type(bundle).__name__}')
    return fields, sections, delete


def display_group(
    pending: PendingGroup[GroupTarget],
    *,
    width: int = 100,
) -> Panel:
    """Render one pending target group as an identity panel and conditional edit sections.

    All bundles contribute to one panel. Removals precede additions within each
    section, and identity rows always describe the current target snapshot.

    Args:
        pending: Target snapshot and merged pending requests to display.
        width: Maximum panel width, further constrained by the console.

    Returns:
        A Rich panel using the character workflow's compact grid style.

    Raises:
        ValueError: The requested width is not positive.
    """
    if width <= 0:
        raise ValueError('Display width must be positive')
    title, identity = _identity(pending.target)
    fields, sections, delete = _edits(pending)
    contents: list[Table | Text | Group] = [_grid(identity)]
    if fields:
        contents.extend([Text(''), _grid(fields)])
    for name, rows in sections.items():
        if rows:
            rows = sorted(rows, key=lambda row: row.priority)
            if name == 'Metadata':
                subgroups: dict[str, list[DisplayRow]] = {}
                for row in rows:
                    subgroups.setdefault(row.label, []).append(evolve(row, label='−' if row.priority == 0 else '+'))
                metadata = Group(
                    *(
                        Group(Text(header, style='bold'), Padding(_grid(entries), (0, 0, 0, 2)))
                        for header, entries in subgroups.items()
                    )
                )
                contents.extend([Text(''), Group(Text(name, style='bold'), Padding(metadata, (0, 0, 0, 2)))])
            else:
                contents.extend([Text(''), Group(Text(name, style='bold'), Padding(_grid(rows), (0, 0, 0, 2)))])
    if delete:
        contents.extend([Text(''), Text('Delete song', style='bold red')])
    elif not fields and not any(sections.values()):
        contents.extend([Text(''), Text('No field changes', style='dim')])
    return Panel(Group(*contents), title=title, title_align='left', width=width)
