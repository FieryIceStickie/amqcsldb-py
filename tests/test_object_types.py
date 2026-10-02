from collections.abc import Callable
from datetime import datetime

import pytest
from helpers import load

from amqcsl.exceptions import QueryError
from amqcsl.objects import (
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
    TrackPutArtistCredit,
)
from amqcsl.objects._json_types import JSONType


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


type Parser = Callable[[JSONType], object]

PARSERS: list[Parser] = [
    CSLArtistSample.from_json,
    CSLArtist.from_json,
    CSLSongSample.from_json,
    CSLSong.from_json,
    CSLExtraMetadata.from_json,
    CSLSongArtistCredit.from_json,
    CSLSongRelation.from_json,
    CSLTrackArtistCredit.from_json,
    CSLTrackLink.from_json,
    CSLList.from_json,
    CSLGroup.from_json,
    CSLTrack.from_json,
    CSLMetadata.from_json,
]


@pytest.mark.parametrize('parse', PARSERS, ids=lambda parse: parse.__qualname__)
@pytest.mark.parametrize('data', [None, [], {}, {'id': 42}, 'invalid'])
def test_object_parsers_reject_invalid_shapes(parse: Parser, data: JSONType) -> None:
    with pytest.raises(QueryError, match='Invalid json'):
        parse(data)


@pytest.mark.parametrize(
    'field, value', [('id', 1), ('name', None), ('originalName', []), ('disambiguation', 42), ('type', 'Person')]
)
def test_artist_parser_rejects_wrong_field_types(field: str, value: JSONType) -> None:
    data: dict[str, JSONType] = {'id': 'id', 'name': 'Name', 'originalName': '', 'disambiguation': None, 'type': 1}
    data[field] = value
    with pytest.raises(QueryError):
        CSLArtistSample.from_json(data)


@pytest.mark.parametrize('field', ['id', 'name', 'originalName', 'disambiguation', 'type'])
def test_artist_parser_requires_fields(field: str) -> None:
    data: dict[str, JSONType] = {'id': 'id', 'name': 'Name', 'originalName': '', 'disambiguation': None, 'type': 1}
    del data[field]
    with pytest.raises(QueryError):
        CSLArtistSample.from_json(data)


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
        CSLMetadata.from_json(data)


def test_track_dates_artist_credit_order_and_simplification() -> None:
    track = CSLTrack.from_json(load('sunshine/tracks')[0])
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
        assert simplified.to_json(credit.position)['position'] == credit.position


@pytest.mark.parametrize('name', [None, '', 'Alias'])
def test_track_put_credit_preserves_explicit_empty_name(name: str | None) -> None:
    artist = CSLArtistSample('id', 'Artist', '', None, 1)
    credit = TrackPutArtistCredit(artist, ' & ', name)
    assert credit.name == ('Artist' if name is None else name)
    assert credit.to_json(2) == {'artistId': 'id', 'name': credit.name, 'joinPhrase': ' & ', 'position': 2}


def test_song_created_at() -> None:
    song = CSLSongSample.from_json(load('idolypride/songs')[0])
    assert song.created_at == datetime.fromisoformat(song.str_created_at)
