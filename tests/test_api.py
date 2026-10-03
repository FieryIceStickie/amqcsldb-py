import asyncio
import json
import mimetypes
from collections.abc import AsyncIterable
from contextlib import AsyncExitStack
from email.parser import BytesParser
from email.policy import default
from pathlib import Path
from typing import Any

import niquests
import pytest
from attrs import evolve
from helpers import collect, finish, first, json_fields, load, mock_response, request_body
from niquests import HTTPError, Response
from niquests import PreparedRequest as Request
from niquests_mock import MockRouter as Router

from amqcsl import AsyncDBClient, DBClient
from amqcsl.clients._http_utils import AsyncMultipartUpload, MultipartUpload, build_request
from amqcsl.objects import (
    AlbumTrack,
    CSLArtist,
    CSLExtraMetadata,
    CSLMetadata,
    CSLSong,
    CSLTrack,
    CSLTrackLink,
    CSLTrackRef,
    ExtraMetadata,
)
from amqcsl.objects._db_types import ArtistCredit, CSLArtistSample
from amqcsl.objects._obj_consts import EMPTY_ID

pytestmark = pytest.mark.asyncio


async def test_list(
    router: Router,
    client: DBClient | AsyncDBClient,
) -> None:
    data = load('lists')
    for list_json in data:
        csl_list = client.lists[list_json['name']]
        assert list_json['id'] == csl_list.id
        assert list_json['name'] == csl_list.name
        assert list_json['count'] == csl_list.count

    assert router['lists'].call_count == 1


async def test_group(
    router: Router,
    client: DBClient | AsyncDBClient,
) -> None:
    data = load('groups')
    for group_json in data:
        group = client.groups[group_json['name']]
        assert group_json['id'] == group.id
        assert group_json['name'] == group.name

    assert router['groups'].call_count == 1


async def test_track_by_list(
    router: Router,
    client: DBClient | AsyncDBClient,
) -> None:
    expected = load('idolypride/tracks')
    route = router.post(
        '/api/tracks',
        name='tracks',
        json=json_fields({'activeListId': 'mock-id-list-meihayasaka', 'quickFilters__0': 3}),
    ).mock(return_value=mock_response(200, json={'tracks': expected, 'count': len(expected)}))
    mei_list = client.lists['MeiHayasaka']
    tracks = {track.id for track in await collect(client.iter_tracks(active_list=mei_list))}
    assert tracks == {track['id'] for track in expected}
    assert route.call_count == 1


async def test_track_by_group(
    router: Router,
    client: DBClient | AsyncDBClient,
) -> None:
    expected = load('idolypride/tracks')
    route = router.post(
        '/api/tracks',
        name='tracks',
        json=json_fields({'groupFilters__0': 'mock-id-group-idolypride'}),
    ).mock(return_value=mock_response(200, json={'tracks': expected, 'count': len(expected)}))
    idoly_pride_group = client.groups['IDOLY PRIDE']
    tracks = {track.id for track in await collect(client.iter_tracks(groups=[idoly_pride_group]))}
    assert tracks == {track['id'] for track in expected}
    assert route.call_count == 1


async def test_track_with_pages(
    router: Router,
    client: DBClient | AsyncDBClient,
) -> None:
    expected = load('idolypride/tracks')
    assert len(expected) == 8
    first_page = router.post(
        '/api/tracks',
        name='tracks_first',
        json=json_fields({'groupFilters__0': 'mock-id-group-idolypride', 'skip': 0, 'take': 4}),
    ).mock(return_value=mock_response(200, json={'tracks': expected[:4], 'count': len(expected)}))
    second_page = router.post(
        '/api/tracks',
        name='tracks_second',
        json=json_fields({'groupFilters__0': 'mock-id-group-idolypride', 'skip': 4, 'take': 4}),
    ).mock(return_value=mock_response(200, json={'tracks': expected[4:], 'count': len(expected)}))
    idoly_pride_group = client.groups['IDOLY PRIDE']
    tracks = {track.id for track in await collect(client.iter_tracks(groups=[idoly_pride_group], batch_size=4))}
    assert tracks == {track['id'] for track in expected}
    assert first_page.call_count == 1
    assert second_page.call_count == 1


async def test_track_search(
    router: Router,
    client: DBClient | AsyncDBClient,
) -> None:
    expected = load('idolypride/tracks')
    target_track_id = 'mock-id-track-blueskysummer'
    expected_track = next(track for track in expected if track['id'] == target_track_id)
    route = router.post(
        '/api/tracks',
        name='tracks',
        json=json_fields({'searchTerm': 'Blue sky'}),
    ).mock(return_value=mock_response(200, json={'tracks': [expected_track], 'count': 1}))
    tracks = {track.id for track in await collect(client.iter_tracks('Blue sky'))}
    assert tracks == {target_track_id}
    assert route.call_count == 1


async def test_artist_search(
    router: Router,
    client: DBClient | AsyncDBClient,
) -> None:
    expected = [
        track
        for track in load('idolypride/artists')  # :)
        if 'idoly pride' in (track['disambiguation'] or '').lower()
    ]
    route = router.get(
        '/api/artists',
        name='artists',
        params={'searchTerm': 'IDOLY PRIDE'},
    ).mock(
        return_value=mock_response(
            200, json={'arists' if isinstance(client, DBClient) else 'artists': expected, 'count': len(expected)}
        )
    )
    assert {obj.id for obj in await collect(client.iter_artists('IDOLY PRIDE'))} == {obj['id'] for obj in expected}
    assert route.call_count == 1


async def test_song_search(
    router: Router,
    client: DBClient | AsyncDBClient,
) -> None:
    expected = load('idolypride/songs')
    route = router.get(
        '/api/songs',
        name='songs',
        params={'searchTerm': 'IDOLY PRIDE'},
    ).mock(return_value=mock_response(200, json={'songs': expected, 'count': len(expected)}))
    assert {obj.id for obj in await collect(client.iter_songs('IDOLY PRIDE'))} == {obj['id'] for obj in expected}
    assert route.call_count == 1


async def test_get_song(
    router: Router,
    client: DBClient | AsyncDBClient,
) -> None:
    target_id = 'mock-id-song-blueskysummer'
    expected_song_sample = next(
        song
        for song in load('idolypride/songs')  # :)
        if song['id'] == target_id
    )
    expected_song = load('idolypride/songs/blueskysummer')
    iter_route = router.get(
        '/api/songs',
        name='iter_song',
        params={'searchTerm': 'Blue sky summer'},
    ).mock(return_value=mock_response(200, json={'songs': [expected_song_sample], 'count': 1}))
    song_route = router.get(
        path=f'/api/song/{expected_song["id"]}',
        name='get_song',
    ).mock(return_value=mock_response(200, json=expected_song))

    song_sample = await first(client.iter_songs('Blue sky summer'))
    song = await finish(client.get_song(song_sample))
    assert song == CSLSong.from_json(expected_song)
    assert iter_route.call_count == 1
    assert song_route.call_count == 1


async def test_get_artist(
    router: Router,
    client: DBClient | AsyncDBClient,
) -> None:
    target_id = 'mock-id-artist-shukasaitou'
    expected_artist_sample = next(
        artist
        for artist in load('sunshine/artists')  # :)
        if artist['id'] == target_id
    )
    expected_artist = load('sunshine/artists/shukasaitou')
    iter_route = router.get(
        '/api/artists',
        name='iter_artists',
        params={'searchTerm': 'Shuka Saitou'},
    ).mock(return_value=mock_response(200, json={'artists': [expected_artist_sample], 'count': 1}))
    artist_route = router.get(
        path=f'/api/artist/{expected_artist["id"]}',
        name='get_artist',
    ).mock(return_value=mock_response(200, json=expected_artist))

    artist_sample = await first(client.iter_artists('Shuka Saitou'))
    artist = await finish(client.get_artist(artist_sample))
    assert artist == CSLArtist.from_json(expected_artist)
    assert iter_route.call_count == 1
    assert artist_route.call_count == 1


async def test_get_metadata(
    router: Router,
    client: DBClient | AsyncDBClient,
) -> None:
    target_id = 'mock-id-track-sukiforyou-you'
    expected_track = next(
        track
        for track in load('sunshine/tracks')  # :)
        if track['id'] == target_id
    )
    expected_meta = load('sunshine/metadata/sukiforyou')
    track_route = router.post(
        '/api/tracks',
        name='iter_tracks',
        json=json_fields({'searchTerm': 'SUKI for you'}),
    ).mock(return_value=mock_response(200, json={'tracks': [expected_track], 'count': 1}))
    meta_route = router.get(
        path=f'/api/track/{target_id}/metadata',
        name='get_meta',
    ).mock(return_value=mock_response(200, json=expected_meta))

    track = await first(client.iter_tracks('SUKI for you'))
    meta = await finish(client.get_metadata(track))
    assert meta == CSLMetadata.from_json(expected_meta)
    assert track_route.call_count == 1
    assert meta_route.call_count == 1


async def test_get_no_metadata(
    router: Router,
    client: DBClient | AsyncDBClient,
) -> None:
    target_id = 'mock-id-track-sukiforyou-you'
    expected_track = next(
        track
        for track in load('sunshine/tracks')  # :)
        if track['id'] == target_id
    )
    track_route = router.post(
        '/api/tracks',
        name='iter_tracks',
        json=json_fields({'searchTerm': 'SUKI for you'}),
    ).mock(return_value=mock_response(200, json={'tracks': [expected_track], 'count': 1}))
    meta_route = router.get(
        path=f'/api/track/{target_id}/metadata',
        name='get_meta',
    ).mock(return_value=mock_response(404, json=load('errors/no_meta')))

    track = await first(client.iter_tracks('SUKI for you'))
    meta = await finish(client.get_metadata(track))
    assert meta is None
    assert track_route.call_count == 1
    assert meta_route.call_count == 1


async def test_create_list(
    router: Router,
    client: DBClient | AsyncDBClient,
    cookies: dict[str, str],
) -> None:
    lists = load('lists')
    mock_list = {'id': 'mock-id-list-youmei', 'name': 'youmei', 'count': 16}
    if isinstance(client, DBClient):
        lists_route = router['lists']
        responses = iter(
            [
                mock_response(200, json=lists),
                mock_response(200, json=lists + [mock_list]),
            ]
        )
        lists_route.mock(side_effect=lambda request: next(responses))
    else:
        lists_route = router['lists'].mock(return_value=mock_response(200, json=lists + [mock_list]))

    mei_list = client.lists['MeiHayasaka']
    you_list = client.lists['yousoro']
    assert lists_route.call_count == 1

    add_route = router.post(
        '/api/list',
        name='add_list',
        json={'importListIds': [mei_list.id, you_list.id], 'name': 'youmei'},
    ).mock(return_value=mock_response(200, json={'ok': True}))

    youmei_list = await finish(client.create_list('youmei', mei_list, you_list))
    assert youmei_list.id == mock_list['id']
    assert lists_route.call_count == 2
    assert add_route.call_count == 1


async def test_list_edit(
    router: Router,
    client: DBClient | AsyncDBClient,
) -> None:
    tracks = load('idolypride/tracks')
    assert tracks
    _ = router.post(
        '/api/tracks',
        name='remove_track',
        json=json_fields({'activeListId': 'mock-id-list-meihayasaka', 'quickFilters__0': 3}),
    ).mock(return_value=mock_response(200, json={'tracks': tracks, 'count': len(tracks)}))
    remove_track_json = tracks[0]

    target_id = 'mock-id-track-sukiforyou-you'
    add_track_json = next(
        track
        for track in load('sunshine/tracks')  # :)
        if track['id'] == target_id
    )
    _ = router.post(
        '/api/tracks',
        name='add_track',
        json=json_fields({'searchTerm': 'SUKI for you'}),
    ).mock(return_value=mock_response(200, json={'tracks': [add_track_json], 'count': 1}))

    mei_list = client.lists['MeiHayasaka']
    route = router.put(
        f'/api/list/{mei_list.id}',
        name='list_edit',
        json=json_fields(
            {'addSongIds': [add_track_json['id']], 'name': 'meichan', 'removeSongIds': [remove_track_json['id']]}
        ),
    ).mock(return_value=mock_response(200))

    add_track = await first(client.iter_tracks('SUKI for you'))
    remove_track = await first(client.iter_tracks(active_list=mei_list))
    await finish(client.list_edit(mei_list, name='meichan', add=[add_track], remove=[remove_track]))
    assert route.call_count == 1


async def test_list_delete(
    router: Router,
    client: DBClient | AsyncDBClient,
) -> None:
    mei_list = client.lists['MeiHayasaka']

    def update_list_route(req: Request) -> Response:
        _ = router['lists'].mock(
            return_value=mock_response(
                200,
                json=[clist for clist in load('lists') if clist['name'] != 'MeiHayasaka'],
            )
        )
        return mock_response(200)

    route = router.delete(
        f'/api/list/{mei_list.id}',
        name='list_delete',
    ).mock(side_effect=update_list_route)

    await finish(client.list_delete(mei_list))
    assert route.call_count == 1
    assert 'MeiHayasaka' not in client.lists


async def test_add_group(
    router: Router,
    client: DBClient | AsyncDBClient,
) -> None:
    route = router.post(
        '/api/group',
        name='add_group',
        json={'name': 'Genshin Impact'},
    ).mock(return_value=mock_response(200, json={'id': 'mock-id-group-genshinimpact', 'name': 'Genshin Impact'}))
    group = await finish(client.create_group('Genshin Impact'))
    assert group.id == 'mock-id-group-genshinimpact'
    assert group.name == 'Genshin Impact'
    assert route.call_count == 1


async def test_track_add_metadata(
    router: Router,
    client: DBClient | AsyncDBClient,
) -> None:
    target_id = 'mock-id-track-sukiforyou-you'
    track_json = next(
        track
        for track in load('sunshine/tracks')  # :)
        if track['id'] == target_id
    )
    meta_json = load('sunshine/metadata/sukiforyou')
    meta_json['extraMetas'][0]['value'] = 'Chika Takami'
    _ = router.post(
        '/api/tracks',
        name='iter_tracks',
        json=json_fields({'searchTerm': 'SUKI for you'}),
    ).mock(return_value=mock_response(200, json={'tracks': [track_json], 'count': 1}))
    _ = router.get(
        path=f'/api/track/{target_id}/metadata',
        name='get_meta',
    ).mock(return_value=mock_response(200, json=meta_json))
    route = router.post(
        f'/api/track/{track_json["id"]}/metadata',
        name='post_meta',
        json=json_fields(
            {'override': False, 'extraMetadatas': [{'isArtist': True, 'type': 'Character', 'value': 'You Watanabe'}]}
        ),
    ).mock(return_value=mock_response(200))
    track = await first(client.iter_tracks('SUKI for you'))
    meta = await finish(client.get_metadata(track))
    await finish(
        client.track_add_metadata(
            track,
            ExtraMetadata(True, 'Character', 'Chika Takami'),
            ExtraMetadata(True, 'Character', 'You Watanabe'),
            existing_meta=meta,
            override=False,
        )
    )
    assert route.call_count == 1


async def test_track_add_metadata_artist_credit(
    router: Router,
    client: DBClient | AsyncDBClient,
) -> None:
    target_id = 'mock-id-track-sukiforyou-you'
    track_json = next(
        track
        for track in load('sunshine/tracks')  # :)
        if track['id'] == target_id
    )
    meta_json = load('sunshine/metadata/sukiforyou')
    artist_json = {
        'id': 'mock-id-artist-aki-hata',
        'name': 'Aki Hata',
        'originalName': 'Aki Hata',
        'disambiguation': None,
        'type': 1,
    }
    artist = CSLArtistSample.from_json(artist_json)  # type: ignore[reportArgumentType]
    meta_json['artistCredits'].append(
        {
            'id': 'mock-id-metadata-aki',
            'type': 'Lyricist',
            'artist': artist_json,
        }
    )
    _ = router.post(
        '/api/tracks',
        name='iter_tracks',
        json=json_fields({'searchTerm': 'SUKI for you'}),
    ).mock(return_value=mock_response(200, json={'tracks': [track_json], 'count': 1}))
    _ = router.get(
        path=f'/api/track/{target_id}/metadata',
        name='get_meta',
    ).mock(return_value=mock_response(200, json=meta_json))
    route = router.post(
        f'/api/track/{track_json["id"]}/metadata',
        name='post_meta',
        json=json_fields(
            {
                'override': False,
                'artistCredits': [{'artistId': 'mock-id-artist-aki-hata', 'type': 'Composer', 'credit': None}],
            }
        ),
    ).mock(return_value=mock_response(200))
    track = await first(client.iter_tracks('SUKI for you'))
    meta = await finish(client.get_metadata(track))
    await finish(
        client.track_add_metadata(
            track,
            ArtistCredit(artist, 'Lyricist'),
            ArtistCredit(artist, 'Composer'),
            existing_meta=meta,
            override=False,
        )
    )
    assert route.call_count == 1


async def test_track_remove_metadata(
    router: Router,
    client: DBClient | AsyncDBClient,
) -> None:
    target_id = 'mock-id-track-sukiforyou-you'
    track_json = next(
        track
        for track in load('sunshine/tracks')  # :)
        if track['id'] == target_id
    )
    meta_json = load('sunshine/metadata/sukiforyou')
    _ = router.post(
        '/api/tracks',
        name='iter_tracks',
        json=json_fields({'searchTerm': 'SUKI for you'}),
    ).mock(return_value=mock_response(200, json={'tracks': [track_json], 'count': 1}))
    _ = router.get(
        path=f'/api/track/{target_id}/metadata',
        name='get_meta',
    ).mock(return_value=mock_response(200, json=meta_json))
    route = router.delete(
        f'/api/track/{track_json["id"]}/metadata/{meta_json["extraMetas"][0]["id"]}',
        name='post_meta',
    ).mock(return_value=mock_response(200))
    track = await first(client.iter_tracks('SUKI for you'))
    meta = await finish(client.get_metadata(track))
    assert meta is not None
    assert len(meta.extra_metas) == 1
    await finish(client.track_remove_metadata(track, meta.extra_metas[0]))
    assert route.call_count == 1


async def test_track_metadata_queue(
    router: Router,
    client: DBClient | AsyncDBClient,
) -> None:
    target_id = 'mock-id-track-sukiforyou-you'
    track_json = next(
        track
        for track in load('sunshine/tracks')  # :)
        if track['id'] == target_id
    )
    meta_json = load('sunshine/metadata/sukiforyou')
    _ = router.post(
        '/api/tracks',
        name='iter_tracks',
        json=json_fields({'searchTerm': 'SUKI for you'}),
    ).mock(return_value=mock_response(200, json={'tracks': [track_json], 'count': 1}))
    _ = router.get(
        path=f'/api/track/{target_id}/metadata',
        name='get_meta',
    ).mock(return_value=mock_response(200, json=meta_json))
    route = router.delete(
        f'/api/track/{track_json["id"]}/metadata/{meta_json["extraMetas"][0]["id"]}',
        name='post_meta',
    ).mock(return_value=mock_response(200))
    track = await first(client.iter_tracks('SUKI for you'))
    meta = await finish(client.get_metadata(track))
    assert meta is not None
    assert len(meta.extra_metas) == 1
    await finish(client.track_remove_metadata(track, meta.extra_metas[0], queue=True))
    assert route.call_count == 0
    await finish(client.commit())
    assert route.call_count == 1


async def test_track_edit(
    router: Router,
    client: DBClient | AsyncDBClient,
) -> None:
    target_id = 'mock-id-track-sukiforyou-you'
    track_json = next(
        track
        for track in load('sunshine/tracks')  # :)
        if track['id'] == target_id
    )
    _ = router.post(
        '/api/tracks',
        name='iter_tracks',
        json=json_fields({'searchTerm': 'SUKI for you'}),
    ).mock(return_value=mock_response(200, json={'tracks': [track_json], 'count': 1}))
    route = router.put(
        f'/api/track/{track_json["id"]}',
        name='post_meta',
        json=json_fields({'name': 'SUKI for you, DREAM for you! (You Watanabe Solo ver.)'}),
    ).mock(return_value=mock_response(200))
    track = await first(client.iter_tracks('SUKI for you'))
    await finish(client.track_edit(track, name='SUKI for you, DREAM for you! (You Watanabe Solo ver.)'))
    assert route.call_count == 1


async def test_add_album(
    router: Router,
    client: DBClient | AsyncDBClient,
) -> None:
    album_name = 'Love Live! Sunshine!! Duo & Trio Collection CD Vol. 2 Winter Vacation'
    original_album_name = 'Duo & Trio Collection CD Vol. 2 Winter Vacation'
    year = 2024
    album_track = AlbumTrack('Misty Frosty Love', 'Misty frosty love', 'Shuka Saitou, Rikako Aida')
    track = album_track.to_json(1, 1, 1)
    group = client.groups['Love Live! Sunshine!!']
    route = router.post(
        '/api/album',
        name='album',
        json=json_fields(
            {
                'album': album_name,
                'discTotal': 1,
                'groupIds': [group.id],
                'originalAlbum': original_album_name,
                'year': year,
                'tracks': [track],
            }
        ),
    ).mock(return_value=mock_response(200))
    await finish(client.create_album(album_name, original_album_name, year, [group], [[album_track]]))
    assert route.call_count == 1


async def test_add_audio(
    router: Router,
    client: DBClient | AsyncDBClient,
    tmp_path: Path,
) -> None:
    audio_name = 'mock_audio.flac'
    audio_path = tmp_path / audio_name
    audio_path.write_bytes(b'abcde')
    target_id = 'mock-id-track-sukiforyou-you'
    track_json = next(
        track
        for track in load('sunshine/tracks')  # :)
        if track['id'] == target_id
    )
    _ = router.post(
        '/api/tracks',
        name='iter_tracks',
        json=json_fields({'searchTerm': 'SUKI for you'}),
    ).mock(return_value=mock_response(200, json={'tracks': [track_json], 'count': 1}))
    presign_route = router.post(
        f'/api/track/{track_json["id"]}/presigned-upload',
        name='presign',
        json={},
    ).mock(
        return_value=mock_response(
            200,
            json={
                'sessionId': 'mock-sessionid',
                'key': 'mock-key',
                'url': 'https://mock-url/',
            },
        )
    )
    upload_route = router.post(
        'https://mock-url/',
        name='upload',
        params={'sessionId': 'mock-sessionid', 'key': 'mock-key'},
    ).mock(return_value=mock_response(200))

    track = await first(client.iter_tracks('SUKI for you'))
    await finish(client.add_audio(track, audio_path))
    assert presign_route.call_count == 1
    assert upload_route.call_count == 1


@pytest.mark.parametrize('client', ['async'], indirect=True)
async def test_async_commit_raises_when_requested(
    client: DBClient | AsyncDBClient,
    router: Router,
) -> None:
    assert isinstance(client, AsyncDBClient)
    track = CSLTrack.from_json(load('sunshine/tracks')[0])
    meta = CSLExtraMetadata('meta-id', 1, 'Language', 'Japanese')
    _ = router.delete(f'/api/track/{track.id}/metadata/{meta.id}').mock(return_value=mock_response(500))
    await client.track_remove_metadata(track, meta, queue=True)
    with pytest.raises(HTTPError):
        await client.commit(stop_if_err=True)


@pytest.mark.parametrize('client', ['async'], indirect=True)
async def test_async_commit_can_continue_past_http_errors(
    client: DBClient | AsyncDBClient,
    router: Router,
) -> None:
    assert isinstance(client, AsyncDBClient)
    track = CSLTrack.from_json(load('sunshine/tracks')[0])
    first, second = (
        CSLExtraMetadata('first', 1, 'Language', 'Japanese'),
        CSLExtraMetadata('second', 1, 'Language', 'English'),
    )
    _ = router.delete(f'/api/track/{track.id}/metadata/{first.id}').mock(return_value=mock_response(500))
    success = router.delete(f'/api/track/{track.id}/metadata/{second.id}').mock(return_value=mock_response(200))
    await client.track_remove_metadata(track, first, queue=True)
    await client.track_remove_metadata(track, second, queue=True)
    await client.commit(stop_if_err=False)
    assert success.called and not client.queue


@pytest.mark.parametrize('operation', ['search', 'list_edit', 'list_remove', 'album'])
@pytest.mark.parametrize('container', ['list', 'tuple', 'generator'])
async def test_iterable_inputs_preserve_members(
    client: DBClient | AsyncDBClient,
    router: Router,
    operation: str,
    container: str,
) -> None:
    group = client.groups['IDOLY PRIDE']
    track = CSLTrack.from_json(load('sunshine/tracks')[0])
    members: list[Any] = [track] if operation in ('list_edit', 'list_remove') else [group]
    values = members if container == 'list' else tuple(members) if container == 'tuple' else iter(members)
    if operation == 'search':
        route = router.post('/api/tracks').mock(return_value=mock_response(200, json={'tracks': [], 'count': 0}))
        await collect(client.iter_tracks(groups=values))
        field, expected = 'groupFilters', group.id
    elif operation in ('list_edit', 'list_remove'):
        csl_list = client.lists['MeiHayasaka']
        route = router.put(f'/api/list/{csl_list.id}').mock(return_value=mock_response(200))
        if operation == 'list_edit':
            await finish(client.list_edit(csl_list, add=values))
        else:
            await finish(client.list_edit(csl_list, remove=values))
        field, expected = ('addSongIds' if operation == 'list_edit' else 'removeSongIds'), track.id
    else:
        route = router.post('/api/album').mock(return_value=mock_response(200))
        await finish(
            client.create_album('Album', 'Original', 2025, values, [[AlbumTrack('Track', 'Original', 'Artist')]])
        )
        field, expected = 'groupIds', group.id
    assert json.loads(request_body(route.calls[-1].request))[field] == [expected]


@pytest.mark.parametrize('stop_if_err', [False, True])
@pytest.mark.parametrize('client', ['async'], indirect=True)
async def test_async_commit_propagates_non_http_errors(
    client: DBClient | AsyncDBClient,
    router: Router,
    tmp_path: Path,
    stop_if_err: bool,
) -> None:
    assert isinstance(client, AsyncDBClient)
    from amqcsl.exceptions import QueryError

    track = CSLTrack.from_json(load('sunshine/tracks')[0])
    path = tmp_path / 'audio.txt'
    path.write_bytes(b'not audio')
    _ = router.post(f'/api/track/{track.id}/presigned-upload').mock(
        return_value=mock_response(200, json={'sessionId': 's', 'key': 'k', 'url': 'https://upload.test'})
    )
    await client.add_audio(track, path, queue=True)
    with pytest.raises(QueryError, match='not an audio file'):
        await client.commit(stop_if_err=stop_if_err)


@pytest.mark.parametrize('client', ['async'], indirect=True)
async def test_async_commit_finishes_siblings_before_raising(
    client: DBClient | AsyncDBClient,
    router: Router,
) -> None:
    assert isinstance(client, AsyncDBClient)
    import asyncio

    from niquests import PreparedRequest as Request

    track = CSLTrack.from_json(load('sunshine/tracks')[0])
    first = CSLExtraMetadata('first', 1, 'Language', 'Japanese')
    second = CSLExtraMetadata('second', 1, 'Language', 'English')
    completed = asyncio.Event()

    async def finish_second(_: Request) -> Response:
        await asyncio.sleep(0)
        completed.set()
        return mock_response(200)

    _ = router.delete(f'/api/track/{track.id}/metadata/{first.id}').mock(return_value=mock_response(500))
    router.delete(f'/api/track/{track.id}/metadata/{second.id}').mock(side_effect=finish_second)
    await client.track_remove_metadata(track, first, queue=True)
    await client.track_remove_metadata(track, second, queue=True)
    with pytest.raises(HTTPError):
        await client.commit()
    assert completed.is_set()


async def test_bundle_metadata_generators_and_list_imports_preserve_values(
    client: DBClient | AsyncDBClient,
    router: Router,
) -> None:
    from amqcsl.clients.bundles import CreateListBundle, SongAddMetadataBundle, TrackAddMetadataBundle
    from amqcsl.objects import ExtraMetadata

    song = CSLSong.from_json(load('idolypride/songs/blueskysummer'))
    track = CSLTrack.from_json(load('sunshine/tracks')[0])
    meta = ExtraMetadata(True, 'Character', 'Character name')
    imported = client.lists['MeiHayasaka']
    create_list = router.post('/api/list', json={'name': 'Copy', 'importListIds': [imported.id]}).mock(
        return_value=mock_response(200)
    )
    song_meta = router.post(f'/api/song/{song.id}').mock(return_value=mock_response(200))
    track_meta = router.post(f'/api/track/{track.id}/metadata').mock(return_value=mock_response(200))
    await finish(client.process(CreateListBundle('Copy', (item for item in [imported]))))
    await finish(client.process(SongAddMetadataBundle(song, (item for item in [meta]))))
    await finish(client.process(TrackAddMetadataBundle(track, (item for item in [meta]))))
    assert create_list.call_count == song_meta.call_count == track_meta.call_count == 1
    assert json.loads(request_body(song_meta.calls[-1].request))['extraMetadatas'] == [meta.to_json()]
    assert json.loads(request_body(track_meta.calls[-1].request))['extraMetadatas'] == [meta.to_json()]


@pytest.mark.parametrize('queue', [False, True])
@pytest.mark.parametrize('status', [200, 500])
async def test_import_audio(
    client: DBClient | AsyncDBClient,
    router: Router,
    queue: bool,
    status: int,
) -> None:
    target = CSLTrack.from_json(load('sunshine/tracks')[0])
    source = evolve(target, id='source', audio_name='source.flac')
    route = router.post(f'/api/track/{target.id}/audio-import').mock(return_value=mock_response(status))
    before = len(router.calls)
    if queue:
        await finish(client.import_audio(target, source, queue=True))
        assert len(router.calls) == before
        assert len(client.queue) == 1

    async def operation() -> None:
        if queue:
            await finish(client.commit())
        else:
            await finish(client.import_audio(target, source))

    if status == 500:
        with pytest.raises(HTTPError):
            await operation()
    else:
        await operation()
        assert not client.queue
    assert route.call_count == 1
    assert json.loads(request_body(route.calls[-1].request)) == {
        'id': target.id,
        'url': 'https://amqbot.082640.xyz/files/source.flac',
    }
    assert len(router.calls) == before + 1


async def test_failed_list_deletion_keeps_cached_lists(
    client: DBClient | AsyncDBClient,
    router: Router,
) -> None:
    lists = client.lists
    target = lists['MeiHayasaka']
    route = router.delete(f'/api/list/{target.id}').mock(return_value=mock_response(500))
    before = router['lists'].call_count
    with pytest.raises(HTTPError):
        await finish(client.list_delete(target))
    assert route.call_count == 1
    assert client.lists is lists
    assert router['lists'].call_count == before


@pytest.mark.parametrize('kind', ['ref', 'link', 'simple', 'track'])
@pytest.mark.parametrize('container', ['list', 'tuple', 'generator'])
async def test_list_edit_accepts_track_references(
    client: DBClient | AsyncDBClient,
    router: Router,
    kind: str,
    container: str,
) -> None:
    target = client.lists['MeiHayasaka']
    track = CSLTrack.from_json(load('sunshine/tracks')[0])
    link = CSLTrackLink(track.id, track.name, track.artist_credits)
    references = {'ref': CSLTrackRef(track.id), 'link': link, 'simple': track.simp, 'track': track}
    reference = references[kind]
    assert link.ref() == CSLTrackRef(track.id)
    add = [reference] if container == 'list' else (reference,) if container == 'tuple' else iter([reference])
    remove = [reference] if container == 'list' else (reference,) if container == 'tuple' else iter([reference])
    route = router.put(f'/api/list/{target.id}').mock(return_value=mock_response(200))
    before = len(router.calls)
    await finish(client.list_edit(target, add=add, remove=remove))
    assert route.call_count == 1
    payload = json.loads(request_body(route.calls[-1].request))
    assert payload['addSongIds'] == payload['removeSongIds'] == [track.id]
    assert len(router.calls) == before + 1


@pytest.mark.parametrize('client_type', [DBClient, AsyncDBClient])
@pytest.mark.parametrize('status', [400, 401, 500])
async def test_login_http_errors_preserve_status(
    client_type: type[DBClient] | type[AsyncDBClient],
    router: Router,
    tmp_path: Path,
    username: str,
    password: str,
    status: int,
) -> None:
    path = tmp_path / 'session.txt'
    router['login_you'].respond(status_code=status, text='Not JSON')
    client = client_type(username=username, password=password, session_path=path)
    with pytest.raises(HTTPError) as error:
        if isinstance(client, DBClient):
            with client:
                pytest.fail('Login unexpectedly succeeded')
        else:
            async with client:
                pytest.fail('Login unexpectedly succeeded')
    assert error.value.response is not None
    assert error.value.response.status_code == status
    assert not path.exists()


@pytest.mark.parametrize('status', [400, 401, 404, 500])
async def test_non_json_metadata_http_errors_preserve_status(
    client: DBClient | AsyncDBClient,
    router: Router,
    status: int,
) -> None:
    track = CSLTrack.from_json(load('sunshine/tracks')[0])
    router.get(path=f'/api/track/{track.id}/metadata').respond(status_code=status, text='Not JSON')
    with pytest.raises(HTTPError) as error:
        await finish(client.get_metadata(track))
    assert error.value.response is not None
    assert error.value.response.status_code == status


@pytest.mark.parametrize('disambiguation', [None, '', 'New value'])
async def test_song_edit_distinguishes_empty_disambiguation_from_omission(
    client: DBClient | AsyncDBClient,
    router: Router,
    disambiguation: str | None,
) -> None:
    song = CSLSong.from_json(load('idolypride/songs/blueskysummer'))
    expected = song.disambiguation if disambiguation is None else disambiguation
    route = router.put(
        f'/api/song/{song.id}', json={'id': EMPTY_ID, 'name': song.name, 'disambiguation': expected}
    ).respond()
    await finish(client.song_edit(song, disambiguation=disambiguation))
    assert route.call_count == 1


@pytest.mark.parametrize('status', [300, 301, 302, 303, 304, 307, 308])
async def test_redirects_are_rejected_without_following(
    client: DBClient | AsyncDBClient,
    router: Router,
    status: int,
) -> None:
    artist = CSLArtistSample.from_json(load('superstar/liella')[0])
    redirect = router.get(path=f'/api/artist/{artist.id}').respond(
        status_code=status, headers={'Location': '/destination'}
    )
    destination = router.get(path='/destination').respond(json={})
    with pytest.raises(niquests.HTTPError) as error:
        await finish(client.get_artist(artist))
    assert error.value.response is not None
    assert error.value.response.status_code == status
    assert redirect.call_count == 1
    assert not destination.called
    assert redirect.calls[-1].kwargs['timeout'] == (10, 30)


async def test_uploads_receive_longer_timeouts(
    client: DBClient | AsyncDBClient,
    router: Router,
    tmp_path: Path,
) -> None:
    track = CSLTrack.from_json(load('sunshine/tracks')[0])
    audio = tmp_path / 'audio.flac'
    audio.write_bytes(b'audio data')
    presign = router.post(f'/api/track/{track.id}/presigned-upload').respond(
        json={'sessionId': 'upload-session', 'key': 'upload-key', 'url': 'https://upload.test/audio'}
    )
    payload = b''

    def consume(request: niquests.PreparedRequest) -> niquests.Response:
        nonlocal payload
        assert isinstance(request.body, MultipartUpload)
        payload = b''.join(request.body)
        return mock_response()

    upload = router.post('https://upload.test/audio', params={'sessionId': 'upload-session', 'key': 'upload-key'}).mock(
        side_effect=consume
    )
    await finish(client.add_audio(track, audio))
    assert (presign.calls[-1].request.headers or {}).get('Cookie') is not None
    assert 'Cookie' not in (upload.calls[-1].request.headers or {})
    assert presign.calls[-1].kwargs['timeout'] == (10, 30)
    assert upload.calls[-1].kwargs['timeout'] == (120, 120)
    assert b'audio data' in payload


@pytest.mark.parametrize('client', ['async'], indirect=True)
async def test_parallel_uploads_preserve_limit_and_do_not_retry(
    client: DBClient | AsyncDBClient,
    router: Router,
    tmp_path: Path,
) -> None:
    assert isinstance(client, AsyncDBClient)
    client.max_request_count = 2
    track = CSLTrack.from_json(load('sunshine/tracks')[0])
    audio = tmp_path / 'audio.flac'
    audio.write_bytes(b'audio data')
    router.post(f'/api/track/{track.id}/presigned-upload').respond(
        json={'sessionId': 'upload-session', 'key': 'upload-key', 'url': 'https://upload.test/audio'}
    )
    active = 0
    peak = 0
    both_started = asyncio.Event()
    release = asyncio.Event()

    async def upload_response(request: niquests.PreparedRequest) -> niquests.Response:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        if active == 2:
            both_started.set()
        try:
            await asyncio.wait_for(release.wait(), timeout=1)
            raise niquests.Timeout('Upload stalled', request=request)
        finally:
            active -= 1

    upload = router.post('https://upload.test/audio', params={'sessionId': 'upload-session', 'key': 'upload-key'}).mock(
        side_effect=upload_response
    )
    for _ in range(3):
        await client.add_audio(track, audio, queue=True)
    task = asyncio.create_task(client.commit())
    try:
        await asyncio.wait_for(both_started.wait(), timeout=1)
        await asyncio.sleep(0)
        assert active == 2
        assert upload.call_count == 2
    finally:
        release.set()
        with pytest.raises(niquests.Timeout, match='Upload stalled'):
            await task
    assert peak == 2
    assert active == 0
    assert upload.call_count == 3


@pytest.mark.parametrize('client_type', [DBClient, AsyncDBClient])
@pytest.mark.parametrize('saved_cookie', ['', 'expired-cookie'])
async def test_login_removes_empty_or_expired_session_cookie(
    client_type: type[DBClient] | type[AsyncDBClient],
    router: Router,
    tmp_path: Path,
    username: str,
    password: str,
    mock_id: str,
    saved_cookie: str,
) -> None:
    path = tmp_path / 'session.txt'
    path.write_text(saved_cookie)
    client = client_type(username=username, password=password, session_path=path)
    if isinstance(client, DBClient):
        with client:
            assert path.read_text() == mock_id
    else:
        async with client:
            assert path.read_text() == mock_id
    login_request = router['login_you'].calls[-1].request
    assert 'session-id=' not in str((login_request.headers or {}).get('Cookie', ''))
    auth_request = router['auth_you'].calls[-1].request
    assert (auth_request.headers or {}).get('Cookie') == f'session-id={mock_id}'
    external = client.client.prepare_request(niquests.Request('GET', 'https://upload.test/audio'))
    assert 'Cookie' not in (external.headers or {})


async def test_saved_session_cookie_is_scoped_to_secure_database_requests(
    client: DBClient | AsyncDBClient,
    mock_id: str,
) -> None:
    from amqcsl.clients._client_consts import DB_URL

    database = client.client.prepare_request(niquests.Request('GET', f'{DB_URL}/api/auth/me'))
    assert (database.headers or {}).get('Cookie') == f'session-id={mock_id}'
    for url in ('https://upload.test/audio', DB_URL.replace('https://', 'http://') + '/api/auth/me'):
        request = client.client.prepare_request(niquests.Request('GET', url))
        assert 'Cookie' not in (request.headers or {})


@pytest.mark.parametrize('filename', ['audio.flac', '音楽"\r\n.flac'])
async def test_audio_upload_streams_valid_multipart(
    client: DBClient | AsyncDBClient,
    router: Router,
    tmp_path: Path,
    filename: str,
) -> None:
    track = CSLTrack.from_json(load('sunshine/tracks')[0])
    audio = tmp_path / filename
    content = bytes(range(256)) * 700
    audio.write_bytes(content)
    router.post(f'/api/track/{track.id}/presigned-upload').respond(
        json={'sessionId': 's', 'key': 'k', 'url': 'https://upload.test/audio'}
    )
    bodies: list[MultipartUpload] = []

    def verify(request: niquests.PreparedRequest, chunks: list[bytes]) -> niquests.Response:
        assert isinstance(request.body, AsyncIterable) == isinstance(client, AsyncDBClient)
        assert request.headers is not None
        assert 'Transfer-Encoding' not in request.headers
        payload = b''.join(chunks)
        assert int(request.headers['Content-Length']) == len(payload)
        assert max(map(len, chunks)) <= MultipartUpload.chunk_size
        message = BytesParser(policy=default).parsebytes(
            f'Content-Type: {request.headers["Content-Type"]}\r\nMIME-Version: 1.0\r\n\r\n'.encode() + payload
        )
        parts = [*message.iter_parts()]
        assert len(parts) == 1
        assert parts[0].get_param('name', header='content-disposition') == 'file'
        assert parts[0].get_filename() == filename.replace('\r', '%0D').replace('\n', '%0A').replace('"', '%22')
        assert parts[0].get_content_type() == mimetypes.guess_type(audio)[0]
        assert parts[0].get_payload(decode=True) == content
        return mock_response()

    def sync_response(request: niquests.PreparedRequest) -> niquests.Response:
        assert isinstance(request.body, MultipartUpload)
        bodies.append(request.body)
        return verify(request, [*request.body])

    async def async_response(request: niquests.PreparedRequest) -> niquests.Response:
        assert isinstance(request.body, AsyncMultipartUpload)
        bodies.append(request.body)
        return verify(request, [chunk async for chunk in request.body])

    router.post('https://upload.test/audio', params={'sessionId': 's', 'key': 'k'}).mock(
        side_effect=async_response if isinstance(client, AsyncDBClient) else sync_response
    )
    await finish(client.add_audio(track, audio))
    assert len(bodies) == 1
    assert bodies[0].closed


async def test_upload_preparation_does_not_read_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    audio = tmp_path / 'audio.flac'
    audio.write_bytes(b'audio')

    def unexpected_open(*args: object, **kwargs: object) -> None:
        pytest.fail('Preparing an upload opened its file')

    monkeypatch.setattr(Path, 'open', unexpected_open)
    with niquests.Session() as session:
        body = MultipartUpload(audio, 'audio/flac')
        request = build_request(session, 'POST', 'https://upload.test', upload=body)
    assert request.body is body
    assert body.closed


@pytest.mark.parametrize('failure', ['error', 'cancel'])
async def test_upload_closes_file_on_transport_failure(
    client: DBClient | AsyncDBClient,
    router: Router,
    tmp_path: Path,
    failure: str,
) -> None:
    track = CSLTrack.from_json(load('sunshine/tracks')[0])
    audio = tmp_path / 'audio.flac'
    audio.write_bytes(b'audio')
    router.post(f'/api/track/{track.id}/presigned-upload').respond(
        json={'sessionId': 's', 'key': 'k', 'url': 'https://upload.test/audio'}
    )
    bodies: list[MultipartUpload] = []

    def fail(request: niquests.PreparedRequest) -> niquests.Response:
        assert isinstance(request.body, MultipartUpload)
        bodies.append(request.body)
        assert not request.body.closed
        next(iter(request.body))
        if failure == 'cancel':
            raise asyncio.CancelledError
        raise niquests.Timeout('Upload stalled')

    router.post('https://upload.test/audio', params={'sessionId': 's', 'key': 'k'}).mock(side_effect=fail)
    with pytest.raises(asyncio.CancelledError if failure == 'cancel' else niquests.Timeout):
        await finish(client.add_audio(track, audio))
    assert len(bodies) == 1
    assert bodies[0].closed


async def test_cancelled_upload_waits_for_pending_file_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audio = tmp_path / 'audio.flac'
    audio.write_bytes(b'audio')
    body = AsyncMultipartUpload(audio, 'audio/flac')
    reading = asyncio.Event()
    release = asyncio.Event()
    finished = asyncio.Event()

    async def delayed_read(*args: object, **kwargs: object) -> bytes:
        reading.set()
        await release.wait()
        finished.set()
        return b'audio'

    monkeypatch.setattr(asyncio, 'to_thread', delayed_read)

    async def consume() -> None:
        with body.opened():
            async for _ in body:
                pass

    task = asyncio.create_task(consume())
    await asyncio.wait_for(reading.wait(), timeout=1)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    assert not body.closed
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished.is_set()
    assert body.closed


@pytest.mark.parametrize('client', ['async'], indirect=True)
async def test_queued_upload_does_not_open_file_before_transport_slot(
    client: DBClient | AsyncDBClient,
    router: Router,
    tmp_path: Path,
) -> None:
    assert isinstance(client, AsyncDBClient)
    audio = tmp_path / 'audio.flac'
    audio.write_bytes(b'audio')
    body = AsyncMultipartUpload(audio, 'audio/flac')
    request = build_request(client.client, 'POST', 'https://upload.test/audio', upload=body)
    router.post('https://upload.test/audio').respond()
    async with AsyncExitStack() as stack:
        for _ in range(client.max_request_count):
            await stack.enter_async_context(client._request_semaphore)  # type: ignore[reportPrivateUsage]
        task = asyncio.create_task(client._send_request(request))  # type: ignore[reportPrivateUsage]
        await asyncio.sleep(0)
        assert not task.done()
        assert body.closed
    await task
    assert body.closed


@pytest.mark.parametrize('client', ['async'], indirect=True)
@pytest.mark.parametrize('initial_limit, new_limit', [(1, 3), (3, 1)])
async def test_request_limit_changes_with_active_and_waiting_requests(
    client: DBClient | AsyncDBClient,
    router: Router,
    initial_limit: int,
    new_limit: int,
) -> None:
    assert isinstance(client, AsyncDBClient)
    client.max_request_count = initial_limit
    started = [asyncio.Event() for _ in range(5)]
    release = [asyncio.Event() for _ in range(5)]
    active = 0

    def response_for(idx: int):
        async def response(request: niquests.PreparedRequest) -> niquests.Response:
            nonlocal active
            active += 1
            started[idx].set()
            try:
                await release[idx].wait()
                return mock_response()
            finally:
                active -= 1

        return response

    for idx in range(5):
        router.get(f'/api/limit-test/{idx}').mock(side_effect=response_for(idx))
    tasks = [
        asyncio.create_task(
            client._send_request(  # type: ignore[reportPrivateUsage]
                build_request(client.client, 'GET', f'/api/limit-test/{idx}')
            )
        )
        for idx in range(5)
    ]
    try:
        await asyncio.wait_for(started[initial_limit - 1].wait(), timeout=1)
        await asyncio.sleep(0)
        assert active == initial_limit
        assert not started[initial_limit].is_set()
        client.max_request_count = new_limit
        assert client.max_request_count == new_limit
        if new_limit > initial_limit:
            await asyncio.wait_for(started[new_limit - 1].wait(), timeout=1)
            assert active == new_limit
            assert not started[new_limit].is_set()
            release[0].set()
            await tasks[0]
            await asyncio.wait_for(started[new_limit].wait(), timeout=1)
            assert active == new_limit
        else:
            for idx in range(initial_limit - 1):
                release[idx].set()
                await tasks[idx]
                await asyncio.sleep(0)
                assert active == initial_limit - idx - 1
                assert not started[initial_limit].is_set()
            release[initial_limit - 1].set()
            await tasks[initial_limit - 1]
            await asyncio.wait_for(started[initial_limit].wait(), timeout=1)
            assert active == new_limit
            assert not started[initial_limit + 1].is_set()
    finally:
        for event in release:
            event.set()
        await asyncio.gather(*tasks)
    assert active == 0
    assert all(event.is_set() for event in started)


@pytest.mark.parametrize('client', ['async'], indirect=True)
async def test_cancelling_waiting_requests_does_not_lose_capacity(
    client: DBClient | AsyncDBClient,
    router: Router,
) -> None:
    assert isinstance(client, AsyncDBClient)
    client.max_request_count = 1
    started = asyncio.Event()
    release = asyncio.Event()

    async def response(request: niquests.PreparedRequest) -> niquests.Response:
        started.set()
        await release.wait()
        return mock_response()

    router.get('/api/limit-test/active').mock(side_effect=response)
    waiting = router.get('/api/limit-test/waiting').respond()
    after = router.get('/api/limit-test/after').respond()

    async def send(path: str) -> niquests.Response:
        return await client._send_request(  # type: ignore[reportPrivateUsage]
            build_request(client.client, 'GET', path)
        )

    active = asyncio.create_task(send('/api/limit-test/active'))
    await asyncio.wait_for(started.wait(), timeout=1)
    cancelled = asyncio.create_task(send('/api/limit-test/waiting'))
    next_request = asyncio.create_task(send('/api/limit-test/after'))
    try:
        await asyncio.sleep(0)
        assert waiting.call_count == after.call_count == 0
        cancelled.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled
    finally:
        release.set()
        await asyncio.gather(active, next_request)
    assert waiting.call_count == 0
    assert after.call_count == 1
