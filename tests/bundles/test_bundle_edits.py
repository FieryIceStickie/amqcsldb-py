from pathlib import Path
from typing import Any

import pytest
from attrs import evolve

from amqcsl.bundles import (
    AddAudioBundle,
    EntryEdit,
    HasEdit,
    SongAddMetadataBundle,
    SongDeleteBundle,
    SongDeleteMetadataBundle,
    SongEditBundle,
    TrackAddMetadataBundle,
    TrackDeleteMetadataBundle,
    TrackEditBundle,
    has_overlap,
)
from amqcsl.bundles._misc import ImportAudioBundle
from amqcsl.objects import (
    ArtistCredit,
    CSLArtistSample,
    CSLExtraMetadata,
    CSLMetadata,
    CSLSong,
    CSLSongArtistCredit,
    CSLTrack,
    ExtraMetadata,
)


@pytest.fixture
def song(track: CSLTrack) -> CSLSong:
    return CSLSong(track.id, 'Song', None, '2026-01-01', [], [])


@pytest.mark.parametrize(
    ('field', 'value'),
    [
        ('name', ''),
        ('original_name', ''),
        ('original_artist', ''),
        ('artist_credits', []),
        ('groups', []),
        ('type', 'Vocal'),
    ],
)
def test_track_fields_overlap_even_for_empty_values(
    field: str,
    value: Any,
    track: CSLTrack,
) -> None:
    first = TrackEditBundle(track, **{field: value})
    second = TrackEditBundle(evolve(track, name='Different snapshot'), **{field: value})
    assert first.affected_entries() == {EntryEdit('track', track.id, (field,))}
    assert has_overlap(first, second) and has_overlap(second, first)
    assert not has_overlap(first, TrackEditBundle(evolve(track, id='other'), **{field: value}))
    assert not has_overlap(first, TrackEditBundle(track))


def test_disjoint_fields_and_target_categories_do_not_overlap(track: CSLTrack, song: CSLSong) -> None:
    first = TrackEditBundle(track, name='Name')
    assert not has_overlap(first, TrackEditBundle(track, original_name='Original'))
    assert not has_overlap(first, SongEditBundle(song, 'Name', None))


def test_song_edits_report_both_fields_actually_sent(song: CSLSong) -> None:
    first = SongEditBundle(song, 'Name', None)
    second = SongEditBundle(song, None, 'Disambiguation')
    assert first.affected_entries() == {
        EntryEdit('song', song.id, ('name',)),
        EntryEdit('song', song.id, ('disambiguation',)),
    }
    assert has_overlap(first, second)


@pytest.mark.parametrize('target', ['track', 'song'])
def test_extra_metadata_additions_and_removals_share_identity(
    target: str,
    track: CSLTrack,
    song: CSLSong,
) -> None:
    new = ExtraMetadata(False, 'Character', 'You')
    old = CSLExtraMetadata('entry', 1, 'Character', 'You')
    if target == 'track':
        addition = TrackAddMetadataBundle(track, [new])
        removal = TrackDeleteMetadataBundle(track, old)
        different = TrackAddMetadataBundle(track, [ExtraMetadata(False, 'Character', 'Chika')])
        artist_value = TrackAddMetadataBundle(track, [ExtraMetadata(True, 'Character', 'You')])
    else:
        addition = SongAddMetadataBundle(song, [new])
        removal = SongDeleteMetadataBundle(song, old)
        different = SongAddMetadataBundle(song, [ExtraMetadata(False, 'Character', 'Chika')])
        artist_value = SongAddMetadataBundle(song, [ExtraMetadata(True, 'Character', 'You')])
    assert has_overlap(addition, removal) and has_overlap(removal, addition)
    assert has_overlap(addition, addition) and has_overlap(removal, removal)
    assert not has_overlap(addition, different)
    assert not has_overlap(addition, artist_value)


@pytest.mark.parametrize('target', ['track', 'song'])
def test_artist_link_identity_ignores_credited_name_but_includes_role(
    target: str,
    track: CSLTrack,
    song: CSLSong,
) -> None:
    artist = CSLArtistSample('artist', 'Artist', '', None, 1)
    first_meta = ArtistCredit(artist, 'Vocal', 'First credit')
    second_meta = ArtistCredit(evolve(artist, name='Different snapshot'), 'Vocal', 'Second credit')
    old = CSLSongArtistCredit('entry', 'Vocal', artist)
    if target == 'track':
        first = TrackAddMetadataBundle(track, [first_meta])
        second = TrackAddMetadataBundle(track, [second_meta])
        removal = TrackDeleteMetadataBundle(track, old)
        different = TrackAddMetadataBundle(track, [ArtistCredit(artist, 'Composer')])
    else:
        first = SongAddMetadataBundle(song, [first_meta])
        second = SongAddMetadataBundle(song, [second_meta])
        removal = SongDeleteMetadataBundle(song, old)
        different = SongAddMetadataBundle(song, [ArtistCredit(artist, 'Composer')])
    assert has_overlap(first, second) and has_overlap(first, removal)
    assert not has_overlap(first, different)


def test_filtered_metadata_is_excluded_and_override_is_separate(track: CSLTrack) -> None:
    old = CSLExtraMetadata('entry', 2, 'Character', 'You')
    metadata = ExtraMetadata(True, 'Character', 'You')
    existing = CSLMetadata(False, [], [old], 1, [])
    filtered = TrackAddMetadataBundle(track, [metadata], existing_meta=existing)
    removal = TrackDeleteMetadataBundle(track, old)
    assert filtered.affected_entries() == set()
    assert not has_overlap(filtered, removal)
    enabled = TrackAddMetadataBundle(track, [], True)
    disabled = TrackAddMetadataBundle(track, [], False)
    assert has_overlap(enabled, disabled)
    assert not has_overlap(enabled, removal)


def test_upload_and_import_overlap_on_destination_audio(track: CSLTrack, tmp_path: Path) -> None:
    audio = tmp_path / 'audio.flac'
    audio.write_bytes(b'audio')
    upload = AddAudioBundle(track, audio)
    imported = ImportAudioBundle(track, evolve(track, id='source'))
    assert has_overlap(upload, imported)
    assert not has_overlap(upload, ImportAudioBundle(evolve(track, id='other'), track))
    assert not has_overlap(upload, TrackEditBundle(track, name='Name'))


def test_song_deletion_overlaps_all_edits_on_that_song(song: CSLSong) -> None:
    edits: list[HasEdit] = [
        SongEditBundle(song, 'Name', None),
        SongAddMetadataBundle(song, [ExtraMetadata(False, 'Language', 'Japanese')]),
        SongDeleteMetadataBundle(song, CSLExtraMetadata('entry', 1, 'Language', 'Japanese')),
        SongDeleteBundle(song),
    ]
    deletion = SongDeleteBundle(song)
    for edit in edits:
        assert has_overlap(deletion, edit) and has_overlap(edit, deletion)
    assert not has_overlap(deletion, SongDeleteBundle(evolve(song, id='other')))
