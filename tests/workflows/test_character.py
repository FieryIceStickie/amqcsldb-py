import json
import re
from collections.abc import Sequence

import pytest
from attrs import define, field
from helpers import finish, json_fields, load, mock_response, query_params, request_body, request_url
from niquests import PreparedRequest as Request
from niquests_mock import MockRoute as Route
from niquests_mock import MockRouter as Router

from amqcsl import AsyncDBClient, DBClient
from amqcsl.objects import CSLTrack
from amqcsl.workflows import character as cm

compact_characters: cm.ArtistDict = {
    'Sayuri Date': 'Kanon Shibuya',
    'Liyuu': 'Keke Tang',
    'Naomi Payton': 'Sumire Heanna',
    'Nako Misaki': 'Chisato Arashi',
    'Nagisa Aoyama': 'Ren Hazuki',
    'Nozomi Suzuhara': 'Kinako Sakurakouji',
    'Aya Emori': 'Natsumi Onitsuka',
    'Wakana Ookuma': 'Shiki Wakana',
    'Akane Yabushima': 'Mei Yoneme',
    'Yuina': 'Margarete Wien',
    'Sakura Sakakura': 'Tomari Onitsuka',
}

characters: cm.CharacterDict = {
    'kanon': 'Kanon Shibuya',
    'keke': 'Keke Tang',
    'sumire': 'Sumire Heanna',
    'chisato': 'Chisato Arashi',
    'ren': 'Ren Hazuki',
    'kinako': 'Kinako Sakurakouji',
    'natsumi': 'Natsumi Onitsuka',
    'shiki': 'Shiki Wakana',
    'mei': 'Mei Yoneme',
    'margarete': 'Margarete Wien',
    'tomari': 'Tomari Onitsuka',
    'yuuna': 'Yuuna Hijirisawa',
    'mao': 'Mao Hiiragi',
}

# fmt: off
artists: cm.ArtistDict = {
    'Sayuri Date': 'kanon',
    'Liyuu': 'keke',
    'Nako Misaki': 'chisato',
    'Naomi Payton': 'sumire',
    'Nagisa Aoyama': 'ren',
    'Nozomi Suzuhara': 'kinako',
    'Aya Emori': 'natsumi',
    'Wakana Ookuma': 'shiki',
    'Akane Yabushima': 'mei',
    'Yuina': 'margarete',
    'Sakura Sakakura': 'tomari',
}
# fmt: on


expected_track_names = {
    'mock-id-track-aspire': {
        'Kanon Shibuya',
        'Keke Tang',
        'Sumire Heanna',
        'Chisato Arashi',
        'Ren Hazuki',
        'Kinako Sakurakouji',
        'Natsumi Onitsuka',
        'Shiki Wakana',
        'Mei Yoneme',
        'Margarete Wien',
        'Tomari Onitsuka',
    },
    'mock-id-track-overover': {'Kanon Shibuya'},
    'mock-id-track-skylinker': {'Mei Yoneme'},
    'mock-id-track-wildcard': {'Tomari Onitsuka'},
    'mock-id-track-justwoo': {'Sumire Heanna'},
    'mock-id-track-pastelcollage': {'Natsumi Onitsuka'},
    'mock-id-track-rhythm': {'Chisato Arashi'},
    'mock-id-track-lilia': {'Shiki Wakana'},
    'mock-id-track-fundamental': {'Keke Tang'},
    'mock-id-track-musubiba': {'Ren Hazuki'},
    'mock-id-track-luca': {'Margarete Wien'},
    'mock-id-track-tekutekubiyori': {'Kinako Sakurakouji'},
}


@define
class AspireFixture:
    route: Route
    group_route: Route
    num_tracks: int
    calls: dict[str, bytes] = field(factory=dict[str, bytes])


@pytest.fixture
def aspire_fixture(router: Router) -> AspireFixture:
    track_data = load('superstar/aspire')
    artist_data = load('superstar/liella')
    group = next(artist for artist in artist_data if artist['id'] == 'mock-id-artist-liella11')
    members = [artist for artist in artist_data if artist['type'] == 1]
    group_route = router.get('/api/artist/mock-id-artist-liella11').mock(
        return_value=mock_response(
            200,
            json={
                **group,
                'forwardRelations': [
                    {'id': f'member-{idx}', 'type': 1, 'artist': member} for idx, member in enumerate(members)
                ],
                'reverseRelations': [],
                'linkedAMQSongs': [],
                'linkedTracks': [],
            },
        )
    )
    _ = router.post(
        '/api/tracks',
        name='tracks',
        json=json_fields({'searchTerm': 'Aspire'}),
    ).mock(return_value=mock_response(200, json={'tracks': track_data, 'count': len(track_data)}))
    _ = router.get(
        '/api/artists',
        name='artists',
        params={'searchTerm': 'Liella!'},
    ).mock(return_value=mock_response(200, json={'artists': artist_data, 'count': len(artist_data)}))
    _ = router.get(
        url=re.compile(r'/api/track/([\w-]+)/metadata'),
        name='get_meta',
    ).mock(
        return_value=mock_response(
            404, json={'statusCode': 404, 'errors': {'generalErrors': ['Song does not have metadata']}}
        )
    )
    route = router.post(url=re.compile(r'/api/track/(?P<track_id>[\w-]+)/metadata'))
    rtn = AspireFixture(route, group_route, len(track_data))

    def side_effect(request: Request):
        track_id = request_url(request).split('/')[-2]
        rtn.calls[track_id] = request_body(request)
        return mock_response(200)

    route.mock(side_effect=side_effect)
    return rtn


@define
class ArtistHandler:
    unknown_artists: dict[str, list[str]] = field(factory=dict[str, list[str]])

    def __call__(
        self,
        track: CSLTrack,
        unknown_artists: Sequence[cm.Reason],
    ) -> cm.ExcludeDecision:
        self.unknown_artists[track.id] = [reason.artist.id for reason in unknown_artists]
        return cm.ExcludeDecision.ERROR


@pytest.fixture
def artist_handler() -> ArtistHandler:
    return ArtistHandler()


def assert_metadatas(req_content: bytes, expected_names: set[str]):
    content = json.loads(req_content)
    names: set[str] = set()
    for meta in content['extraMetadatas']:
        match meta:
            case {
                'isArtist': True,
                'type': 'Character',
                'value': name,
            }:
                names.add(name)
            case _:
                assert False, f'{meta = }'
    assert names == expected_names


pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize('compact', [False, True])
async def test_aspire(
    aspire_fixture: AspireFixture,
    client: DBClient | AsyncDBClient,
    artist_handler: ArtistHandler,
    compact: bool,
) -> None:
    artist_to_meta = await finish(
        cm.compact_make_artist_to_meta(client, compact_characters, ['Liella!'])
        if compact
        else cm.make_artist_to_meta(client, characters, artists, ['Liella!'])
    )
    match client:
        case DBClient():
            for track in client.iter_tracks('Aspire'):
                bundle = await finish(artist_to_meta.apply(track, artist_handler))
                if bundle is not None:
                    client.enqueue(bundle)
        case AsyncDBClient():
            async for track in client.iter_tracks('Aspire'):
                bundle = await finish(artist_to_meta.apply(track, artist_handler))
                if bundle is not None:
                    client.enqueue(bundle)
    assert not artist_handler.unknown_artists
    assert aspire_fixture.group_route.call_count == 1
    assert len(client.queue) == aspire_fixture.num_tracks - 2

    await finish(client.commit())

    for track_id, req_content in aspire_fixture.calls.items():
        assert track_id in expected_track_names
        assert_metadatas(req_content, expected_track_names[track_id])


async def test_artist_to_meta(
    router: Router,
    client: DBClient | AsyncDBClient,
) -> None:
    artist_data = load('superstar/liella')

    def artist_route(req: Request):
        search_term = query_params(req)['searchTerm']
        artists = [artist for artist in artist_data if search_term in artist['name']]
        return mock_response(200, json={'artists': artists, 'count': len(artists)})

    liella_route = router.get(
        '/api/artists',
        name='artists_liella',
        params={'searchTerm': 'Liella!'},
    ).mock(return_value=mock_response(200, json={'artists': artist_data, 'count': len(artist_data)}))
    route = router.get(
        path='/api/artists',
        name='artists',
        params={'searchTerm': re.compile(r'^(?!Liella!$)')},
    ).mock(side_effect=artist_route)

    artist_to_meta = await finish(cm.make_artist_to_meta(client, characters, artists))
    expected_artist_to_meta = await finish(cm.make_artist_to_meta(client, characters, artists, ['Liella!']))
    assert artist_to_meta.metadata == expected_artist_to_meta.metadata

    assert route.call_count == 11
    assert liella_route.call_count == 1
