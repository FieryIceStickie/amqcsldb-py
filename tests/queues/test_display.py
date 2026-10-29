from io import StringIO
from pathlib import Path

import pytest
from attrs import evolve
from rich.console import Console

from amqcsl.objects import (
    ArtistCredit,
    CSLArtistSample,
    CSLExtraMetadata,
    CSLGroup,
    CSLSong,
    CSLSongArtistCredit,
    CSLSongSample,
    CSLTrack,
    ExtraMetadata,
    NewSong,
    TrackPutArtistCredit,
)
from amqcsl.queues import GroupedQueue


def render(queue: GroupedQueue, width: int = 100) -> str:
    """Render a queue without ANSI escapes at the requested console width."""
    output = StringIO()
    Console(file=output, width=width, color_system=None).print(queue)
    return output.getvalue()


def test_one_panel_collects_edit_sections_and_preserves_current_identity(track: CSLTrack) -> None:
    old_artist = CSLArtistSample('old-artist', 'Old [bold]artist', '', 'old version', 1)
    guest = evolve(old_artist, id='guest', name='Guest', disambiguation=None)
    new_artist = evolve(old_artist, id='new-artist', name='New artist', disambiguation='new version')
    credit = track.artist_credits[0]
    track = evolve(
        track,
        name='Old [bold]name',
        original_name='Original',
        artist_credits=[
            evolve(credit, artist=old_artist, name=old_artist.name, join_phrase=' feat. '),
            evolve(credit, artist=guest, name=guest.name, join_phrase=''),
        ],
        groups=[CSLGroup('old-group', 'Old group')],
    )
    queue = GroupedQueue()
    queue.track_edit(
        track,
        name='New [red]name',
        original_name='',
        original_artist='New original artist',
        type='OffVocal',
        artist_credits=[TrackPutArtistCredit(new_artist)],
        groups=[CSLGroup('new-group', 'New group')],
        song=NewSong('New song', 'Acoustic'),
    )
    queue.track_add_metadata(
        track, ExtraMetadata(True, 'Character', 'You [green]Watanabe'), ArtistCredit(new_artist, 'Vocal')
    )
    queue.track_remove_metadata(track, CSLExtraMetadata('extra', 2, 'Character', 'Old character'))
    queue.track_remove_metadata(track, CSLSongArtistCredit('credit', 'Vocal', old_artist))
    source = evolve(track, id='source-track', name='Source track')
    queue.import_audio(track, source)
    before = queue.to_json()
    text = render(queue)
    assert text.count('╭─ Track') == 1
    assert 'Old [bold]artist feat. Guest' in text
    assert track.id in text and 'Old [bold]name' in text and 'New [red]name' in text
    assert 'Original' in text and 'Original → ""' in text
    assert 'Vocal → OffVocal' in text
    assert 'Old [bold]artist (old version) (old-artist)' in text
    assert 'New artist (new version) (new-artist)' in text
    assert 'Create' in text and 'New song (Acoustic)' in text
    assert 'You [green]Watanabe' in text and 'Source track' in text and 'source-track' in text
    section_order = ['Groups', 'Artist credits', 'Song', 'Metadata', 'Audio']
    positions = [text.index(section) for section in section_order]
    assert positions == sorted(positions)
    extra = text[text.index('Character (Artist)') : text.index('Vocal', text.index('Metadata'))]
    links = text[text.index('Vocal', text.index('Metadata')) : text.index('Audio')]
    assert extra.index('Old character') < extra.index('You [green]Watanabe')
    assert links.index('old-artist') < links.index('new-artist')
    assert queue.to_json() == before


def test_identity_only_and_empty_queue_are_readable(track: CSLTrack) -> None:
    queue = GroupedQueue()
    assert 'No pending edits' in render(queue)
    queue.track_edit(evolve(track, name=None, original_name='Fallback name'))
    text = render(queue)
    assert 'Fallback name' in text and track.id in text and 'No field changes' in text
    assert all(section not in text for section in ('Artist credits', 'Metadata', 'Audio'))


def test_edits_use_the_same_snapshot_as_the_permanent_identity(track: CSLTrack) -> None:
    track = evolve(track, name='Group snapshot')
    queue = GroupedQueue()
    queue.track_add_metadata(track, ExtraMetadata(False, 'Language', 'Japanese'))
    queue.track_edit(evolve(track, name='Later snapshot'), name='Proposed')
    text = render(queue)
    assert 'Group snapshot → Proposed' in text
    assert 'Later snapshot' not in text


@pytest.mark.parametrize('width', [45, 70, 100])
def test_panels_fit_console_and_keep_literal_names(track: CSLTrack, width: int) -> None:
    track = evolve(track, name='[bold]Literal[/bold]')
    queue = GroupedQueue()
    queue.track_edit(track, name='[red]Proposal[/red]')
    text = render(queue, width)
    assert '[bold]Literal[/bold]' in text
    assert '[red]Proposal[/red]' in text
    assert all(len(line) <= width for line in text.splitlines())


def test_artist_credit_order_and_join_phrase_only_edits_are_visible(track: CSLTrack) -> None:
    first = evolve(track.artist_credits[0], join_phrase=' / ')
    second = evolve(first, artist=evolve(first.artist, id='second', name='Second'), name='Second', join_phrase='')
    track = evolve(track, artist_credits=[first, second])
    queue = GroupedQueue()
    queue.track_edit(
        track, artist_credits=[TrackPutArtistCredit.simplify(second), TrackPutArtistCredit.simplify(first)]
    )
    assert 'Order' in render(queue)
    other = GroupedQueue()
    other.track_edit(
        track,
        artist_credits=[
            evolve(TrackPutArtistCredit.simplify(first), join_phrase=' & '),
            TrackPutArtistCredit.simplify(second),
        ],
    )
    assert "join: ' / '" in render(other) and "join: ' & '" in render(other)


def test_empty_artist_and_group_replacements_show_removals(track: CSLTrack) -> None:
    queue = GroupedQueue()
    queue.track_edit(track, groups=[], artist_credits=[])
    text = render(queue)
    assert 'Groups' in text and 'Artist credits' in text
    assert '−' in text and '+' not in text
    for credit in track.artist_credits:
        assert credit.artist.id in text


@pytest.mark.parametrize('new_song', [False, True])
def test_song_link_and_creation_are_distinguished(track: CSLTrack, new_song: bool) -> None:
    queue = GroupedQueue()
    song = (
        NewSong('Proposed song', 'Version')
        if new_song
        else CSLSongSample('proposed-song-id', 'Proposed song', 'Version', '2026-01-01')
    )
    queue.track_edit(track, song=song)
    text = render(queue)
    assert 'Proposed song (Version)' in text
    assert ('Create' if new_song else 'Proposed') in text
    assert ('proposed-song-id' in text) is not new_song


def test_song_panel_shows_edits_and_deletion_without_bundle_reprs() -> None:
    song = CSLSong('song-id', 'Old song', None, '2026-01-01', [], [])
    queue = GroupedQueue()
    queue.song_edit(song, 'New song', 'Version')
    queue.song_add_metadata(song, ExtraMetadata(False, 'Language', 'Japanese'))
    queue.song_delete_metadata(song, CSLExtraMetadata('meta', 1, 'Language', 'English'))
    text = render(queue)
    assert text.count('╭─ Song') == 1 and 'song-id' in text
    assert 'Old song → New song' in text and 'None → Version' in text
    assert 'English' in text and 'Japanese' in text
    assert 'SongEditBundle' not in text
    queue.allow_overlapping_edits = True
    queue.song_delete(song)
    text = render(queue)
    assert 'Delete song' in text and 'New song' not in text and 'Metadata' not in text


def test_upload_and_override_display_the_actual_merged_proposals(
    track: CSLTrack,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Relative path so long temp directories don't wrap the filename
    monkeypatch.chdir(tmp_path)
    audio = Path('audio.flac')
    audio.write_bytes(b'audio')
    queue = GroupedQueue(allow_overlapping_edits=True)
    queue.add_audio(track, audio)
    queue.track_add_metadata(track, override=True)
    queue.track_add_metadata(track, override=False)
    text = render(queue)
    assert 'Upload' in text and 'audio.flac' in text and '…' not in text
    assert 'Metadata override' in text and 'Disabled' in text and 'Enabled' not in text


def test_panel_order_and_display_width(track: CSLTrack) -> None:
    queue = GroupedQueue()
    queue.track_edit(evolve(track, id='first-id'), name='First proposal')
    queue.track_edit(evolve(track, id='second-id'), name='Second proposal')
    output = StringIO()
    console = Console(file=output, width=100, color_system=None)
    console.print(queue.display({'width': 65}))
    text = output.getvalue()
    assert text.index('first-id') < text.index('second-id')
    assert all(len(line) <= 65 for line in text.splitlines())
    with pytest.raises(ValueError, match='positive'):
        queue.display({'width': 0})


def test_diff_colors_and_artist_ids_are_styled_separately(track: CSLTrack) -> None:
    new_artist = CSLArtistSample('new-id', 'New artist', '', None, 1)
    queue = GroupedQueue()
    queue.track_edit(track, name='Proposed name', artist_credits=[TrackPutArtistCredit(new_artist)])
    segments = [*Console(width=100, color_system='truecolor').render(queue.display())]
    colored: dict[str, list[str]] = {}
    for segment in segments:
        if segment.style is not None and segment.style.color is not None:
            colored.setdefault(segment.style.color.name, []).append(segment.text)
    assert 'New artist' in ''.join(colored['green'])
    assert 'new-id' in ''.join(colored['bright_black'])
    assert 'Proposed name' in ''.join(colored['yellow'])
    assert '−' in ''.join(colored['red'])


def test_metadata_headers_distinguish_targets_and_credit_aliases_use_plain_quotes(track: CSLTrack) -> None:
    artist = track.artist_credits[0].artist
    queue = GroupedQueue()
    queue.track_add_metadata(
        track,
        ExtraMetadata(True, 'Language', 'Japanese'),
        ExtraMetadata(False, 'Language', 'English'),
        ArtistCredit(artist, 'Composer', 'Alias'),
    )
    text = render(queue)
    assert text.count('Metadata') == 1
    assert 'Language (Artist)' in text and 'Language (Song)' in text
    assert 'Composer' in text and 'as "Alias"' in text
    assert '“' not in text and '”' not in text
