from collections.abc import Callable
from copy import deepcopy
from datetime import datetime
from typing import Any, assert_type, cast

import pytest
from helpers import load

from amqcsl.exceptions import QueryError
from amqcsl.objects import (
    AlbumTrack,
    ArtistCredit,
    CSLArtist,
    CSLArtistSample,
    CSLExtraMetadata,
    CSLGroup,
    CSLList,
    CSLMetadata,
    CSLSong,
    CSLSongArtistCredit,
    CSLSongRelation,
    CSLSongSample,
    CSLTrack,
    CSLTrackArtistCredit,
    CSLTrackLink,
    ExtraMetadata,
    NewSong,
    TrackPutArtistCredit,
    from_json,
    to_json,
)
from amqcsl.objects._conversion import DatabaseObject
from amqcsl.objects._json_types import (
    JSONAlbumTrack,
    JSONArtist,
    JSONArtistSample,
    JSONExtraMetadata,
    JSONGroup,
    JSONList,
    JSONMetadata,
    JSONSong,
    JSONSongArtistCredit,
    JSONSongRelation,
    JSONSongSample,
    JSONTrack,
    JSONTrackArtistCredit,
    JSONTrackLink,
    JSONTrackPutArtistCredit,
    JSONType,
    MetadataPostArtistCredit,
    MetadataPostExtraMetadata,
    TrackNewSong,
)


def test_artist_to_sample_strips_relations():
    full = CSLArtist('id', 'Name', 'Original', 'Disambiguation', 3, [], [], [], [])
    sample = full.to_sample()
    assert type(sample) is CSLArtistSample
    assert sample == CSLArtistSample('id', 'Name', 'Original', 'Disambiguation', 3)
    assert hash(sample) == hash(sample.to_sample())
    assert full.type == 'Group'


def test_numeric_types_keep_their_existing_values():
    sample = CSLArtistSample('id', 'Name', '', None, 1)
    assert sample.type == 'Person'
    assert CSLExtraMetadata('id', 2, 'Character', 'Name').type == 'Artist'
    assert CSLSongRelation('id', 1, sample).type == 'GroupMember'


PARSERS: list[type[Any]] = [
    CSLArtistSample,
    CSLArtist,
    CSLSongSample,
    CSLSong,
    CSLExtraMetadata,
    CSLSongArtistCredit,
    CSLSongRelation,
    CSLTrackArtistCredit,
    CSLTrackLink,
    CSLList,
    CSLGroup,
    CSLTrack,
    CSLMetadata,
]


@pytest.mark.parametrize('target', PARSERS, ids=lambda target: target.__name__)
@pytest.mark.parametrize('data', [None, [], {}, {'id': 42}, 'invalid'])
def test_object_parsers_reject_invalid_shapes(target: type[Any], data: Any) -> None:
    with pytest.raises(QueryError, match='Invalid json'):
        from_json(data, target)


@pytest.mark.parametrize(
    'field, value', [('id', 1), ('name', None), ('originalName', []), ('disambiguation', 42), ('type', 'Person')]
)
def test_artist_parser_rejects_wrong_field_types(field: str, value: JSONType) -> None:
    data: dict[str, JSONType] = {'id': 'id', 'name': 'Name', 'originalName': '', 'disambiguation': None, 'type': 1}
    data[field] = value
    with pytest.raises(QueryError):
        from_json(cast(JSONArtistSample, data), CSLArtistSample)


@pytest.mark.parametrize('field', ['id', 'name', 'originalName', 'disambiguation', 'type'])
def test_artist_parser_requires_fields(field: str) -> None:
    data: dict[str, JSONType] = {'id': 'id', 'name': 'Name', 'originalName': '', 'disambiguation': None, 'type': 1}
    del data[field]
    with pytest.raises(QueryError):
        from_json(cast(JSONArtistSample, data), CSLArtistSample)


@pytest.mark.parametrize(
    'field, value',
    [('fields', [1]), ('extraMetas', [{'invalid': True}]), ('artistCredits', [{'invalid': True}]), ('override', 'yes')],
)
def test_metadata_parser_rejects_malformed_nested_fields(field: str, value: JSONType) -> None:
    data: dict[str, JSONType] = {
        'override': False,
        'artistCredits': [],
        'extraMetas': [],
        'totalCount': 0,
        'fields': [],
    }
    data[field] = value
    with pytest.raises(QueryError):
        from_json(cast(JSONMetadata, data), CSLMetadata)


def test_track_dates_artist_credit_order_and_simplification() -> None:
    track = from_json(cast(JSONTrack, load('sunshine/tracks')[0]), CSLTrack)
    assert track.created_at == datetime.fromisoformat(track.str_created_at)
    assert track.updated_at == datetime.fromisoformat(track.str_updated_at)
    assert track.simp.id == track.id and track.simp.name == track.name
    assert track.simp.original_simple_artist == track.original_simple_artist
    assert track.str_artist_credits == ''.join(credit.name + credit.join_phrase for credit in track.artist_credits)
    for credit in track.artist_credits:
        simplified = TrackPutArtistCredit.simplify(credit)
        assert simplified.artist is credit.artist
        assert simplified.name == credit.name
        assert simplified.join_phrase == credit.join_phrase
        assert to_json(simplified, position=credit.position)['position'] == credit.position


@pytest.mark.parametrize('name', [None, '', 'Alias'])
def test_track_put_credit_preserves_explicit_empty_name(name: str | None) -> None:
    artist = CSLArtistSample('id', 'Artist', '', None, 1)
    credit = TrackPutArtistCredit(artist, ' & ', name)
    assert credit.name == ('Artist' if name is None else name)
    assert to_json(credit, position=2) == {'artistId': 'id', 'name': credit.name, 'joinPhrase': ' & ', 'position': 2}


def test_song_created_at() -> None:
    song = from_json(cast(JSONSongSample, load('idolypride/songs')[0]), CSLSongSample)
    assert song.created_at == datetime.fromisoformat(song.str_created_at)


_ROUND_TRIPS: list[tuple[type[DatabaseObject], Any]] = [
    (CSLGroup, {'id': 'group', 'name': 'Group'}),
    (CSLList, {'id': 'list', 'name': 'List', 'count': 2}),
    (
        CSLArtistSample,
        {'id': 'artist', 'name': 'Artist', 'originalName': '', 'disambiguation': None, 'type': 1},
    ),
    (
        CSLSongSample,
        {'id': 'song', 'name': 'Song', 'disambiguation': None, 'createdAt': '2026-10-04T00:00:00'},
    ),
    (CSLExtraMetadata, {'id': 'meta', 'type': 2, 'key': 'Character', 'value': 'You'}),
    (
        CSLSongArtistCredit,
        {
            'id': 'credit',
            'type': 'Vocalist',
            'artist': {'id': 'artist', 'name': 'Artist', 'originalName': '', 'disambiguation': None, 'type': 1},
        },
    ),
    (
        CSLSongRelation,
        {
            'id': 'relation',
            'type': 1,
            'artist': {'id': 'artist', 'name': 'Artist', 'originalName': '', 'disambiguation': None, 'type': 1},
        },
    ),
    (
        CSLTrackArtistCredit,
        {
            'artist': {'id': 'artist', 'name': 'Artist', 'originalName': '', 'disambiguation': None, 'type': 1},
            'name': 'Alias',
            'joinPhrase': ' & ',
            'position': 3,
        },
    ),
    (CSLTrackLink, {'id': 'track', 'name': None, 'artists': []}),
    (
        CSLArtist,
        {
            'id': 'artist',
            'name': 'Artist',
            'originalName': '',
            'disambiguation': None,
            'type': 1,
            'forwardRelations': [],
            'reverseRelations': [],
            'linkedAMQSongs': [{'id': 'track', 'name': None, 'artists': []}],
            'linkedTracks': [],
        },
    ),
    (
        CSLSong,
        {
            'id': 'song',
            'name': 'Song',
            'disambiguation': None,
            'createdAt': '2026-10-04T00:00:00',
            'artistCredits': [],
            'extraMetas': [],
        },
    ),
    (
        CSLTrack,
        {
            'id': 'track',
            'name': None,
            'originalName': 'Original',
            'originalSimpleArtist': 'Artist',
            'originalAlbum': None,
            'album': '',
            'trackNumber': 1,
            'trackTotal': 1,
            'discNumber': 1,
            'discTotal': 1,
            'year': None,
            'song': None,
            'artistCredits': [],
            'groups': [{'id': 'group', 'name': 'Group'}],
            'audioId': None,
            'audioName': None,
            'disabled': False,
            'type': 1,
            'createdAt': '2026-10-04T00:00:00',
            'updatedAt': '2026-10-04T00:00:00',
            'inList': True,
        },
    ),
    (
        CSLMetadata,
        {
            'override': False,
            'artistCredits': [],
            'extraMetas': [{'id': 'meta', 'type': 2, 'key': 'Character', 'value': 'You'}],
            'totalCount': 1,
            'fields': ['Character'],
        },
    ),
]


@pytest.mark.parametrize(
    'target, data',
    _ROUND_TRIPS,
    ids=lambda value: value.__name__ if isinstance(value, type) else None,
)
def test_database_json_round_trip(target: type[DatabaseObject], data: Any) -> None:
    obj = from_json(data, target)
    assert type(obj) is target
    payload = to_json(obj)
    assert payload == data
    assert from_json(cast(Any, payload), target) == obj
    assert not hasattr(target, 'from_json')


def test_serialization_includes_full_artist_and_song_fields() -> None:
    artist = from_json(cast(JSONArtist, load('sunshine/artists/shukasaitou')), CSLArtist)
    song_data = cast(JSONSong, load('idolypride/songs/blueskysummer'))
    song_data['extraMetas'] = [{'id': 'meta', 'type': 2, 'key': 'Character', 'value': 'You'}]
    song = from_json(song_data, CSLSong)
    artist_json = assert_type(to_json(artist), JSONArtist)
    song_json = assert_type(to_json(song), JSONSong)
    assert 'linkedAMQSongs' in artist_json
    assert artist_json['linkedAMQSongs']
    assert 'linked_amq_songs' not in artist_json
    assert 'linkedAmqSongs' not in artist_json
    assert song_json['artistCredits']
    assert song_json['extraMetas']
    assert assert_type(from_json(artist_json, CSLArtist), CSLArtist) == artist
    assert assert_type(from_json(song_json, CSLSong), CSLSong) == song


def test_conversion_ignores_extra_fields_without_mutating_input() -> None:
    data: Any = {
        'id': 'artist',
        'name': 'Name',
        'originalName': '',
        'disambiguation': None,
        'type': 1,
        'unexpected': {'value': True},
    }
    before = deepcopy(data)
    artist = from_json(cast(JSONArtistSample, data), CSLArtistSample)
    assert data == before
    payload = assert_type(to_json(artist), JSONArtistSample)
    assert 'unexpected' not in payload
    assert assert_type(from_json(payload, CSLArtistSample), CSLArtistSample) == artist


@pytest.mark.parametrize('field, value', [('artistCredits', ''), ('groups', {}), ('disabled', 1), ('inList', 'yes')])
def test_track_conversion_rejects_coercible_invalid_fields(field: str, value: JSONType) -> None:
    data: Any = load('sunshine/tracks')[0]
    data[field] = value
    with pytest.raises(QueryError) as error:
        from_json(cast(JSONTrack, data), CSLTrack)
    assert error.value.__cause__ is not None


def test_track_conversion_sorts_credits_without_mutating_input() -> None:
    data: JSONTrack = cast(JSONTrack, load('idolypride/tracks')[4])
    assert len(data['artistCredits']) > 1
    data['artistCredits'].sort(key=lambda credit: credit['position'], reverse=True)
    before = deepcopy(data)
    track = from_json(data, CSLTrack)
    assert data == before
    positions = [credit.position for credit in track.artist_credits]
    assert positions == sorted(positions)
    serialized = assert_type(to_json(track), JSONTrack)
    assert [credit['position'] for credit in serialized['artistCredits']] == positions
    assert assert_type(from_json(serialized, CSLTrack), CSLTrack) == track


def test_leaf_conversion_return_types() -> None:
    group = CSLGroup('group', 'Group')
    csl_list = CSLList('list', 'List', 2)
    song = CSLSongSample('song', 'Song', None, '2026-10-04T00:00:00')
    artist = CSLArtistSample('artist', 'Artist', '', None, 1)
    metadata = CSLExtraMetadata('meta', 2, 'Character', 'You')
    credit = CSLSongArtistCredit('credit', 'Vocalist', artist)
    relation = CSLSongRelation('relation', 1, artist)
    track_credit = CSLTrackArtistCredit(artist, 'Alias', '', 0)
    link = CSLTrackLink('track', None, [track_credit])
    full_metadata = CSLMetadata(False, [credit], [metadata], 2, ['Character'])
    assert_type(to_json(group), JSONGroup)
    assert_type(to_json(csl_list), JSONList)
    assert_type(to_json(song), JSONSongSample)
    assert_type(to_json(metadata), JSONExtraMetadata)
    assert_type(to_json(credit), JSONSongArtistCredit)
    assert_type(to_json(relation), JSONSongRelation)
    assert_type(to_json(track_credit), JSONTrackArtistCredit)
    assert_type(to_json(link), JSONTrackLink)
    assert_type(to_json(full_metadata), JSONMetadata)
    assert_type(from_json(to_json(group), CSLGroup), CSLGroup)
    assert_type(from_json(to_json(csl_list), CSLList), CSLList)
    assert_type(from_json(to_json(song), CSLSongSample), CSLSongSample)
    assert_type(from_json(to_json(metadata), CSLExtraMetadata), CSLExtraMetadata)
    assert_type(from_json(to_json(credit), CSLSongArtistCredit), CSLSongArtistCredit)
    assert_type(from_json(to_json(relation), CSLSongRelation), CSLSongRelation)
    assert_type(from_json(to_json(track_credit), CSLTrackArtistCredit), CSLTrackArtistCredit)
    assert_type(from_json(to_json(link), CSLTrackLink), CSLTrackLink)
    assert_type(from_json(to_json(full_metadata), CSLMetadata), CSLMetadata)


def test_edit_serialization_preserves_request_payloads() -> None:
    artist = CSLArtistSample('artist', 'Artist', '', None, 1)
    credit = ArtistCredit(artist, 'Vocalist')
    metadata = ExtraMetadata(True, 'Character', 'You')
    song = NewSong('Song')
    track_credit = TrackPutArtistCredit(artist, ' & ')
    track = AlbumTrack('Song', 'Original song', 'Artist')
    assert assert_type(to_json(credit), MetadataPostArtistCredit) == {
        'artistId': 'artist',
        'credit': None,
        'type': 'Vocalist',
    }
    assert assert_type(to_json(metadata), MetadataPostExtraMetadata) == {
        'isArtist': True,
        'type': 'Character',
        'value': 'You',
    }
    assert assert_type(to_json(song), TrackNewSong) == {'name': 'Song', 'disambiguation': None}
    assert assert_type(to_json(track_credit, position=3), JSONTrackPutArtistCredit) == {
        'artistId': 'artist',
        'joinPhrase': ' & ',
        'name': 'Artist',
        'position': 3,
    }
    assert assert_type(
        to_json(track, disc_number=2, track_number=3, track_total=10),
        JSONAlbumTrack,
    ) == {
        'discNumber': 2,
        'name': 'Song',
        'originalArtist': 'Artist',
        'originalName': 'Original song',
        'trackNumber': 3,
        'trackTotal': 10,
    }
    assert track_credit.name == 'Artist'


@pytest.mark.parametrize('credit_name', [None, '', 'Alias'])
def test_edit_credit_serialization_uses_effective_name(credit_name: str | None) -> None:
    artist = CSLArtist('artist', 'Artist', '', None, 1, [], [], [], [])
    credit = TrackPutArtistCredit(artist, ' & ', credit_name)
    assert to_json(credit, position=0) == {
        'artistId': 'artist',
        'joinPhrase': ' & ',
        'name': 'Artist' if credit_name is None else credit_name,
        'position': 0,
    }


@pytest.mark.parametrize(
    'obj, kwargs, message',
    [
        (TrackPutArtistCredit(CSLArtistSample('artist', 'Artist', '', None, 1)), {}, 'missing.*position'),
        (AlbumTrack('Song', '', ''), {'disc_number': 1, 'track_number': 1}, 'missing.*track_total'),
        (NewSong('Song'), {'position': 0}, 'unexpected.*position'),
        (CSLGroup('group', 'Group'), {'disc_number': 1}, 'unexpected.*disc_number'),
        (
            AlbumTrack('Song', '', ''),
            {'disc_number': 1, 'track_number': 1, 'track_total': 1, 'position': 0},
            'unexpected.*position',
        ),
    ],
)
def test_edit_serialization_checks_context_keywords(obj: Any, kwargs: dict[str, int], message: str) -> None:
    serialize = cast(Callable[..., object], to_json)
    with pytest.raises(TypeError, match=message):
        serialize(obj, **kwargs)


@pytest.mark.parametrize('target', [ArtistCredit, ExtraMetadata, NewSong, TrackPutArtistCredit, AlbumTrack])
def test_edit_objects_are_serialization_only(target: type[Any]) -> None:
    parse = cast(Callable[..., object], from_json)
    with pytest.raises(TypeError, match='Unsupported database object'):
        parse({}, target)
    assert not hasattr(target, 'to_json')
    assert not hasattr(target, 'from_json')
