"""Regression coverage for endpoint routing, errors, and iterable inputs."""

import json
from pathlib import Path
from typing import Any

import pytest
from helpers import collect, finish, load
from httpx import HTTPStatusError, Response
from respx import Router

from amqcsl import AsyncDBClient, DBClient
from amqcsl.objects import AlbumTrack, CSLExtraMetadata, CSLSong, CSLTrack

pytestmark = pytest.mark.asyncio


async def test_song_edit_uses_song_endpoint(
    client: DBClient | AsyncDBClient,
    router: Router,
) -> None:
    song = CSLSong.from_json(load('idolypride/songs/blueskysummer'))
    correct = router.put(f'/api/song/{song.id}') % Response(200)
    incorrect = router.put(f'/api/group/{song.id}') % Response(200)
    await finish(client.song_edit(song, name='Renamed'))
    assert correct.called and not incorrect.called


async def test_song_metadata_deletion_uses_song_endpoint(
    client: DBClient | AsyncDBClient,
    router: Router,
) -> None:
    song = CSLSong.from_json(load('idolypride/songs/blueskysummer'))
    meta = CSLExtraMetadata('meta-id', 1, 'Language', 'Japanese')
    correct = router.delete(f'/api/song/{song.id}/metadata/{meta.id}') % Response(200)
    incorrect = router.delete(f'/api/track/{song.id}/metadata/{meta.id}') % Response(200)
    await finish(client.song_delete_metadata(song, meta))
    assert correct.called and not incorrect.called


async def test_track_edit_propagates_http_errors(
    client: DBClient | AsyncDBClient,
    router: Router,
) -> None:
    track = CSLTrack.from_json(load('sunshine/tracks')[0])
    _ = router.put(f'/api/track/{track.id}') % Response(500)
    with pytest.raises(HTTPStatusError):
        await finish(client.track_edit(track, name='Renamed'))


@pytest.mark.parametrize('client', ['async'], indirect=True)
async def test_async_commit_raises_when_requested(
    client: DBClient | AsyncDBClient,
    router: Router,
) -> None:
    assert isinstance(client, AsyncDBClient)
    track = CSLTrack.from_json(load('sunshine/tracks')[0])
    meta = CSLExtraMetadata('meta-id', 1, 'Language', 'Japanese')
    _ = router.delete(f'/api/track/{track.id}/metadata/{meta.id}') % Response(500)
    await client.track_remove_metadata(track, meta, queue=True)
    with pytest.raises(HTTPStatusError):
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
    _ = router.delete(f'/api/track/{track.id}/metadata/{first.id}') % Response(500)
    success = router.delete(f'/api/track/{track.id}/metadata/{second.id}') % Response(200)
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
        route = router.post('/api/tracks') % Response(200, json={'tracks': [], 'count': 0})
        await collect(client.iter_tracks(groups=values))
        field, expected = 'groupFilters', group.id
    elif operation in ('list_edit', 'list_remove'):
        csl_list = client.lists['MeiHayasaka']
        route = router.put(f'/api/list/{csl_list.id}') % Response(200)
        if operation == 'list_edit':
            await finish(client.list_edit(csl_list, add=values))
        else:
            await finish(client.list_edit(csl_list, remove=values))
        field, expected = ('addSongIds' if operation == 'list_edit' else 'removeSongIds'), track.id
    else:
        route = router.post('/api/album') % Response(200)
        await finish(
            client.create_album('Album', 'Original', 2025, values, [[AlbumTrack('Track', 'Original', 'Artist')]])
        )
        field, expected = 'groupIds', group.id
    assert json.loads(route.calls.last.request.content)[field] == [expected]


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
    _ = router.post(f'/api/track/{track.id}/presigned-upload') % Response(
        200, json={'sessionId': 's', 'key': 'k', 'url': 'https://upload.test'}
    )
    await client.add_audio(track, path, queue=True)
    with pytest.raises(QueryError, match='not an audio file'):
        await client.commit(stop_if_err=stop_if_err)
    assert len(client.queue) == 1


@pytest.mark.parametrize('client', ['async'], indirect=True)
async def test_async_commit_finishes_siblings_before_raising(
    client: DBClient | AsyncDBClient,
    router: Router,
) -> None:
    assert isinstance(client, AsyncDBClient)
    import asyncio
    from httpx import Request

    track = CSLTrack.from_json(load('sunshine/tracks')[0])
    first = CSLExtraMetadata('first', 1, 'Language', 'Japanese')
    second = CSLExtraMetadata('second', 1, 'Language', 'English')
    completed = asyncio.Event()

    async def finish_second(_: Request) -> Response:
        await asyncio.sleep(0)
        completed.set()
        return Response(200)

    _ = router.delete(f'/api/track/{track.id}/metadata/{first.id}') % Response(500)
    router.delete(f'/api/track/{track.id}/metadata/{second.id}').mock(side_effect=finish_second)
    await client.track_remove_metadata(track, first, queue=True)
    await client.track_remove_metadata(track, second, queue=True)
    with pytest.raises(HTTPStatusError):
        await client.commit()
    assert completed.is_set()
    assert len(client.queue) == 2


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
    create_list = router.post('/api/list', json={'name': 'Copy', 'importListIds': [imported.id]}) % Response(200)
    song_meta = router.post(f'/api/song/{song.id}') % Response(200)
    track_meta = router.post(f'/api/track/{track.id}/metadata') % Response(200)
    await finish(client.process(CreateListBundle('Copy', (item for item in [imported]))))
    await finish(client.process(SongAddMetadataBundle(song, (item for item in [meta]))))
    await finish(client.process(TrackAddMetadataBundle(track, (item for item in [meta]))))
    assert create_list.call_count == song_meta.call_count == track_meta.call_count == 1
    assert json.loads(song_meta.calls.last.request.content)['extraMetadatas'] == [meta.to_json()]
    assert json.loads(track_meta.calls.last.request.content)['extraMetadatas'] == [meta.to_json()]
