import json
from collections.abc import Sequence

import pytest
from attrs import define, field
from helpers import load
from httpx import Request, Response
from respx import Route, Router

from amqcsl import AsyncDBClient, DBClient
from amqcsl.objects import CSLArtistSample, CSLTrack
from amqcsl.workflows import character as cm

compact_characters: cm.ArtistDict = {
    ('Liella!', 'Love Live! Superstar!! (11 members)'): (
        'Kanon Shibuya, Keke Tang, Sumire Heanna, Chisato Arashi, Ren Hazuki, '  #
        'Kinako Sakurakouji, Natsumi Onitsuka, Shiki Wakana, Mei Yoneme, Margarete Wien, Tomari Onitsuka'
    ),
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
    ('Liella!', 'Love Live! Superstar!! (11 members)'): 'kanon keke sumire chisato ren kinako natsumi shiki mei margarete tomari',
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
    num_tracks: int
    calls: dict[str, bytes] = field(factory=dict)


@pytest.fixture
def aspire_fixture(router: Router) -> AspireFixture:
    track_data = load('superstar/aspire')
    artist_data = load('superstar/liella')
    _ = router.post(
        '/api/tracks',
        name='tracks',
        json__searchTerm='Aspire',
    ) % Response(200, json={'tracks': track_data, 'count': len(track_data)})
    _ = router.get(
        '/api/artists',
        name='artists',
        params={'searchTerm': 'Liella!'},
    ) % Response(200, json={'artists': artist_data, 'count': len(artist_data)})
    _ = router.get(
        url__regex=r'/api/track/([\w-]+)/metadata',
        name='get_meta',
    ) % Response(404, json={'statusCode': 404, 'errors': {'generalErrors': ['Song does not have metadata']}})
    route = router.post(url__regex=r'/api/track/(?P<track_id>[\w-]+)/metadata')
    rtn = AspireFixture(route, len(track_data))

    def side_effect(request: Request, track_id: str):
        rtn.calls[track_id] = request.content
        return Response(200)

    route.side_effect = side_effect
    return rtn


@define
class ArtistHandler:
    unknown_artists: dict[str, list[str]] = field(factory=dict)

    def __call__(
        self,
        track: CSLTrack,
        artist_to_meta: cm.ArtistToMeta,
        unknown_artists: Sequence[CSLArtistSample],
    ) -> bool:
        self.unknown_artists[track.id] = [artist.id for artist in unknown_artists]
        return False


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


def test_aspire_sync(
    aspire_fixture: AspireFixture,
    client: DBClient,
    artist_handler: ArtistHandler,
):
    artist_to_meta = cm.make_artist_to_meta(client, characters, artists, ['Liella!'])
    for track in client.iter_tracks('Aspire'):
        meta = client.get_metadata(track)
        cm.queue_character_metadata(client, track, artist_to_meta, meta, artist_handler)
    assert not artist_handler.unknown_artists
    assert len(client.queue) == aspire_fixture.num_tracks - 2

    client.commit()

    for track_id, req_content in aspire_fixture.calls.items():
        assert track_id in expected_track_names
        assert_metadatas(req_content, expected_track_names[track_id])


def test_aspire_sync_compact(
    aspire_fixture: AspireFixture,
    client: DBClient,
    artist_handler: ArtistHandler,
):
    artist_to_meta = cm.compact_make_artist_to_meta(client, compact_characters, ['Liella!'])
    for track in client.iter_tracks('Aspire'):
        meta = client.get_metadata(track)
        cm.queue_character_metadata(client, track, artist_to_meta, meta, artist_handler)
    assert not artist_handler.unknown_artists
    assert len(client.queue) == aspire_fixture.num_tracks - 2

    client.commit()

    for track_id, req_content in aspire_fixture.calls.items():
        assert track_id in expected_track_names
        assert_metadatas(req_content, expected_track_names[track_id])


@pytest.mark.asyncio
async def test_aspire_async(
    aspire_fixture: AspireFixture,
    aclient: AsyncDBClient,
    artist_handler: ArtistHandler,
):
    artist_to_meta = await cm.make_artist_to_meta(aclient, characters, artists, ['Liella!'])
    async for track in aclient.iter_tracks('Aspire'):
        meta = await aclient.get_metadata(track)
        cm.queue_character_metadata(aclient, track, artist_to_meta, meta, artist_handler)
    assert not artist_handler.unknown_artists
    assert len(aclient.queue) == aspire_fixture.num_tracks - 2

    await aclient.commit()

    for track_id, req_content in aspire_fixture.calls.items():
        assert track_id in expected_track_names
        assert_metadatas(req_content, expected_track_names[track_id])


@pytest.mark.asyncio
async def test_aspire_async_compact(
    aspire_fixture: AspireFixture,
    aclient: AsyncDBClient,
    artist_handler: ArtistHandler,
):
    artist_to_meta = await cm.compact_make_artist_to_meta(aclient, compact_characters, ['Liella!'])
    async for track in aclient.iter_tracks('Aspire'):
        meta = await aclient.get_metadata(track)
        cm.queue_character_metadata(aclient, track, artist_to_meta, meta, artist_handler)
    assert not artist_handler.unknown_artists
    assert len(aclient.queue) == aspire_fixture.num_tracks - 2

    await aclient.commit()

    for track_id, req_content in aspire_fixture.calls.items():
        assert track_id in expected_track_names
        assert_metadatas(req_content, expected_track_names[track_id])


def test_artist_to_meta_sync(router: Router, client: DBClient):
    artist_data = load('superstar/liella')

    def artist_route(req: Request):
        search_term = req.url.params['searchTerm']
        artists = [artist for artist in artist_data if search_term in artist['name']]
        return Response(200, json={'artists': artists, 'count': len(artists)})

    liella_route = router.get(
        '/api/artists',
        name='artists_liella',
        params={'searchTerm': 'Liella!'},
    ) % Response(200, json={'artists': artist_data, 'count': len(artist_data)})
    route = router.get(
        '/api/artists',
        name='artists',
    ).mock(side_effect=artist_route)

    artist_to_meta = cm.make_artist_to_meta(client, characters, artists)
    expected_artist_to_meta = cm.make_artist_to_meta(client, characters, artists, ['Liella!'])
    assert artist_to_meta == expected_artist_to_meta

    assert route.call_count == 11
    assert liella_route.call_count == 2


@pytest.mark.asyncio
async def test_artist_to_meta_async(router: Router, aclient: AsyncDBClient):
    artist_data = load('superstar/liella')

    def artist_route(req: Request):
        search_term = req.url.params['searchTerm']
        artists = [artist for artist in artist_data if search_term in artist['name']]
        return Response(200, json={'artists': artists, 'count': len(artists)})

    liella_route = router.get(
        '/api/artists',
        name='artists_liella',
        params={'searchTerm': 'Liella!'},
    ) % Response(200, json={'artists': artist_data, 'count': len(artist_data)})
    route = router.get(
        '/api/artists',
        name='artists',
    ).mock(side_effect=artist_route)

    artist_to_meta = await cm.make_artist_to_meta(aclient, characters, artists)
    expected_artist_to_meta = await cm.make_artist_to_meta(aclient, characters, artists, ['Liella!'])
    assert artist_to_meta == expected_artist_to_meta

    assert route.call_count == 11
    assert liella_route.call_count == 2
