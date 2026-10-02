import json
from pathlib import Path
from typing import Any, Literal

import pytest
from helpers import collect, finish, load
from httpx import HTTPStatusError, Response
from respx import Router

from amqcsl import AsyncDBClient, DBClient
from amqcsl.exceptions import QueryError
from amqcsl.objects import (
    AlbumTrack,
    ArtistCredit,
    CSLArtist,
    CSLExtraMetadata,
    CSLMetadata,
    CSLSong,
    CSLSongArtistCredit,
    CSLSongSample,
    CSLTrack,
    ExtraMetadata,
    NewSong,
    TrackPutArtistCredit,
)
from amqcsl.objects._obj_consts import EMPTY_ID, REVERSE_TRACK_TYPE, TrackType

pytestmark = pytest.mark.asyncio


@pytest.fixture
def track() -> CSLTrack:
    return CSLTrack.from_json(load('sunshine/tracks')[0])


@pytest.fixture
def song() -> CSLSong:
    return CSLSong.from_json(load('idolypride/songs/blueskysummer'))


@pytest.fixture
def artist() -> CSLArtist:
    return CSLArtist.from_json(load('sunshine/artists/shukasaitou'))


async def test_full_objects_do_not_refetch(
    client: DBClient | AsyncDBClient,
    router: Router,
    song: CSLSong,
    artist: CSLArtist,
) -> None:
    before = router.calls.call_count
    assert await finish(client.get_song(song)) is song
    assert await finish(client.get_artist(artist)) is artist
    assert router.calls.call_count == before


async def test_cached_collections_and_explicit_refresh(
    client: DBClient | AsyncDBClient,
    router: Router,
) -> None:
    lists, groups = client.lists, client.groups
    assert client.lists is lists and client.groups is groups
    assert router.routes['lists'].call_count == router.routes['groups'].call_count == 1
    _ = router.routes['lists'] % Response(200, json=[])
    _ = router.routes['groups'] % Response(200, json=[])
    if isinstance(client, AsyncDBClient):
        await client.refresh_lists()
        await client.refresh_groups()
    else:
        # Sync exposes cached collections only, with no public refresh method.
        assert client.lists is lists and client.groups is groups
        assert router.routes['lists'].call_count == router.routes['groups'].call_count == 1
        return
    assert client.lists == client.groups == {}
    assert router.routes['lists'].call_count == router.routes['groups'].call_count == 2


@pytest.mark.parametrize('queue', [False, True])
@pytest.mark.parametrize('operation', ['edit', 'delete'])
async def test_group_changes_immediate_or_queued(
    client: DBClient | AsyncDBClient,
    router: Router,
    queue: bool,
    operation: str,
) -> None:
    group = client.groups['IDOLY PRIDE']
    if isinstance(client, AsyncDBClient):
        route = (
            (router.put(f'/api/group/{group.id}') % Response(200))
            if operation == 'edit'
            else (router.delete(f'/api/group/{group.id}') % Response(200))
        )
        if queue:
            method: Any = client.group_edit if operation == 'edit' else client.group_delete
            args = (group, 'Renamed') if operation == 'edit' else (group,)
            with pytest.raises(TypeError, match='queue'):
                await method(*args, queue=True)
            assert not route.called and not client.queue
            return
        if operation == 'edit':
            await client.group_edit(group, 'Renamed')
        else:
            await client.group_delete(group)
        assert route.call_count == 1
        return
    if operation == 'edit':
        route = router.put(f'/api/group/{group.id}', json={'id': EMPTY_ID, 'name': 'Renamed'}) % Response(200)
        await finish(client.group_edit(group, 'Renamed', queue=queue))
    else:
        route = router.delete(f'/api/group/{group.id}') % Response(200)
        await finish(client.group_delete(group, queue=queue))
    assert route.call_count == (0 if queue else 1)
    assert len(client.queue) == (1 if queue else 0)
    await finish(client.commit())
    assert route.call_count == 1
    assert not client.queue


@pytest.mark.parametrize('queue', [False, True])
async def test_song_delete_immediate_or_queued(
    client: DBClient | AsyncDBClient,
    router: Router,
    song: CSLSong,
    queue: bool,
) -> None:
    route = router.delete(f'/api/song/{song.id}') % Response(200)
    if isinstance(client, AsyncDBClient):
        if queue:
            method: Any = client.song_delete
            with pytest.raises(TypeError, match='queue'):
                await method(song, queue=True)
            assert not route.called and not client.queue
            return
        await client.song_delete(song)
    else:
        client.song_delete(song, queue=queue)
    assert route.call_count == (0 if queue else 1)
    await finish(client.commit())
    assert route.call_count == 1 and not client.queue


async def test_song_metadata_accepts_both_metadata_types(
    client: DBClient | AsyncDBClient,
    router: Router,
    song: CSLSong,
    artist: CSLArtist,
) -> None:
    route = router.post(
        f'/api/song/{song.id}',
        json={
            'id': song.id,
            'artistCredits': [{'artistId': artist.id, 'type': 'Composer', 'credit': 'As credited'}],
            'extraMetadatas': [{'isArtist': False, 'type': 'Language', 'value': 'Japanese'}],
        },
    ) % Response(200)
    await finish(
        client.song_add_metadata(
            song, ArtistCredit(artist, 'Composer', 'As credited'), ExtraMetadata(False, 'Language', 'Japanese')
        )
    )
    assert route.call_count == 1


@pytest.mark.parametrize('override', [None, False, True])
async def test_empty_metadata_only_requests_explicit_override(
    client: DBClient | AsyncDBClient,
    router: Router,
    track: CSLTrack,
    override: bool | None,
) -> None:
    route = router.post(
        f'/api/track/{track.id}/metadata',
        json={'id': EMPTY_ID, 'artistCredits': [], 'extraMetadatas': [], 'override': override},
    ) % Response(200)
    await finish(client.track_add_metadata(track, override=override))
    assert route.call_count == (0 if override is None else 1)


async def test_metadata_deduplicates_against_existing_and_within_input(
    client: DBClient | AsyncDBClient,
    router: Router,
    track: CSLTrack,
    artist: CSLArtist,
) -> None:
    existing = CSLMetadata(
        False,
        [CSLSongArtistCredit('credit', 'Composer', artist.to_sample())],
        [CSLExtraMetadata('meta', 2, 'Character', 'Existing')],
        2,
        [],
    )
    unchanged_credit = ArtistCredit(artist.to_sample(), 'Composer')
    unchanged_meta = ExtraMetadata(True, 'Character', 'Existing')
    new_credit = ArtistCredit(artist.to_sample(), 'Composer', 'Alternate credit')
    new_meta = ExtraMetadata(False, 'Character', 'Existing')
    route = router.post(
        f'/api/track/{track.id}/metadata',
        json={
            'id': EMPTY_ID,
            'override': None,
            'artistCredits': [new_credit.to_json()],
            'extraMetadatas': [new_meta.to_json()],
        },
    ) % Response(200)
    await finish(
        client.track_add_metadata(
            track, unchanged_credit, unchanged_meta, new_credit, new_credit, new_meta, new_meta, existing_meta=existing
        )
    )
    assert route.call_count == 1
    assert len(existing.artist_credits) == len(existing.extra_metas) == 1
    route.reset()
    await finish(client.track_add_metadata(track, unchanged_credit, unchanged_meta, existing_meta=existing))
    assert not route.called


@pytest.mark.parametrize('song_kind', ['new', 'existing', 'unchanged'])
@pytest.mark.parametrize('track_type', ['Vocal', 'OffVocal', 'Instrumental', 'Dialogue', 'Other'])
async def test_track_edit_serializes_song_variants_and_types(
    client: DBClient | AsyncDBClient,
    router: Router,
    track: CSLTrack,
    artist: CSLArtist,
    song: CSLSong,
    song_kind: Literal['new', 'existing', 'unchanged'],
    track_type: TrackType,
) -> None:
    replacement: NewSong | CSLSongSample | None
    if song_kind == 'new':
        replacement = NewSong('New title', 'Version')
    elif song_kind == 'existing':
        replacement = song
    else:
        replacement = None
    group = client.groups['IDOLY PRIDE']
    credit = TrackPutArtistCredit(artist, ' & ', 'Credited name')
    route = router.put(
        f'/api/track/{track.id}',
        json={
            'id': EMPTY_ID,
            'artistCredits': [{'artistId': artist.id, 'joinPhrase': ' & ', 'name': 'Credited name', 'position': 0}],
            'batchSongIds': None,
            'groupIds': [group.id],
            'name': 'Renamed',
            'newSong': replacement.to_json() if isinstance(replacement, NewSong) else None,
            'originalArtist': 'Original artist',
            'originalName': 'Original name',
            'songId': song.id if song_kind == 'existing' else None,
            'type': REVERSE_TRACK_TYPE[track_type],
        },
    ) % Response(200)
    await finish(
        client.track_edit(
            track,
            artist_credits=(credit,),
            groups=(group,),
            name='Renamed',
            original_artist='Original artist',
            original_name='Original name',
            song=replacement,
            type=track_type,
        )
    )
    assert route.call_count == 1


@pytest.mark.parametrize('clear', [False, True])
async def test_track_edit_distinguishes_empty_sequences_from_omitted_values(
    client: DBClient | AsyncDBClient,
    router: Router,
    track: CSLTrack,
    clear: bool,
) -> None:
    route = router.put(f'/api/track/{track.id}') % Response(200)
    await finish(client.track_edit(track, artist_credits=() if clear else None, groups=() if clear else None))
    body = route.calls.last.request.read()
    payload = json.loads(body)
    assert payload['artistCredits'] == ([] if clear else None)
    assert payload['groupIds'] == ([] if clear else None)


async def test_album_multiple_discs_track_numbers_and_totals(
    client: DBClient | AsyncDBClient,
    router: Router,
) -> None:
    a, b, c = AlbumTrack('A', 'a', 'Artist'), AlbumTrack('B', 'b', 'Artist'), AlbumTrack('C', 'c', 'Artist')
    group = client.groups['IDOLY PRIDE']
    route = router.post(
        '/api/album',
        json={
            'album': 'Album',
            'originalAlbum': 'Original',
            'year': 2025,
            'discTotal': 2,
            'groupIds': [group.id],
            'tracks': [a.to_json(1, 1, 2), b.to_json(1, 2, 2), c.to_json(2, 1, 1)],
        },
    ) % Response(200)
    await finish(client.create_album('Album', 'Original', 2025, (group,), ((a, b), (c,))))
    assert route.call_count == 1


@pytest.mark.parametrize('status', [400, 401, 404, 500])
async def test_unrecognized_metadata_errors_propagate(
    client: DBClient | AsyncDBClient,
    router: Router,
    track: CSLTrack,
    status: int,
) -> None:
    route = router.get(f'/api/track/{track.id}/metadata') % Response(
        status, json={'errors': {'generalErrors': ['Different error']}}
    )
    with pytest.raises(HTTPStatusError) as error:
        await finish(client.get_metadata(track))
    assert error.value.response.status_code == status
    assert route.call_count == 1


@pytest.mark.parametrize('kind', ['tracks', 'songs', 'artists'])
@pytest.mark.parametrize(
    'batch_size, error',
    [(0, 'positive'), (-1, 'positive'), (101, 'max batch size')],
)
async def test_invalid_query_batch_sizes_fail_before_requests(
    client: DBClient | AsyncDBClient,
    router: Router,
    kind: str,
    batch_size: Any,
    error: str,
) -> None:
    before = router.calls.call_count
    with pytest.raises(QueryError, match=error):
        await collect(getattr(client, f'iter_{kind}')('test', batch_size=batch_size))
    assert router.calls.call_count == before


@pytest.mark.parametrize(
    'operation, kwargs, error',
    [
        ('create_group', {'name': ''}, ValueError),
        ('create_list', {'name': ''}, ValueError),
    ],
)
async def test_empty_names_fail_before_requests(
    client: DBClient | AsyncDBClient,
    router: Router,
    operation: str,
    kwargs: dict[str, Any],
    error: type[Exception],
) -> None:
    before = router.calls.call_count
    with pytest.raises(error):
        await finish(getattr(client, operation)(**kwargs))
    assert router.calls.call_count == before and not client.queue


@pytest.mark.parametrize(
    'kwargs, error',
    [
        ({'name': ''}, ValueError),
        ({'original_name': ''}, ValueError),
        ({'year': 0}, ValueError),
        ({'year': -1}, ValueError),
    ],
)
async def test_invalid_album_inputs_fail_before_requests(
    client: DBClient | AsyncDBClient,
    router: Router,
    kwargs: dict[str, Any],
    error: type[Exception],
) -> None:
    values: dict[str, Any] = {'name': 'Album', 'original_name': 'Original', 'year': 2025, 'groups': (), 'tracks': ()}
    values.update(kwargs)
    before = router.calls.call_count
    with pytest.raises(error):
        await finish(client.create_album(**values))
    assert router.calls.call_count == before


@pytest.mark.parametrize('kind', ['missing', 'directory', 'text', 'unknown'])
async def test_invalid_audio_paths(
    client: DBClient | AsyncDBClient,
    router: Router,
    track: CSLTrack,
    tmp_path: Path,
    kind: str,
) -> None:
    path = tmp_path / ('audio.txt' if kind == 'text' else 'audio.unknown')
    if kind == 'directory':
        path.mkdir()
    elif kind != 'missing':
        path.write_bytes(b'audio')
    presign = router.post(f'/api/track/{track.id}/presigned-upload') % Response(
        200, json={'sessionId': 's', 'key': 'k', 'url': 'https://upload.test'}
    )
    upload = router.post('https://upload.test') % Response(200)
    with pytest.raises(QueryError):
        await finish(client.add_audio(track, str(path)))
    assert not upload.called
    assert presign.call_count == (0 if kind in ('missing', 'directory') else 1)


@pytest.mark.parametrize(
    'payload', [{}, {'sessionId': 1, 'key': 'k', 'url': 'https://upload.test'}, {'sessionId': 's', 'key': 'k'}]
)
async def test_malformed_audio_presign_response(
    client: DBClient | AsyncDBClient,
    router: Router,
    track: CSLTrack,
    tmp_path: Path,
    payload: dict[str, Any],
) -> None:
    path = tmp_path / 'audio.flac'
    path.write_bytes(b'audio')
    _ = router.post(f'/api/track/{track.id}/presigned-upload') % Response(200, json=payload)
    upload = router.post('https://upload.test') % Response(200)
    with pytest.raises(QueryError, match='unknown json'):
        await finish(client.add_audio(track, path))
    assert not upload.called


@pytest.mark.parametrize('missing_audio', [False, True])
@pytest.mark.parametrize('missing_info', [False, True])
@pytest.mark.parametrize('from_active_list', [False, True])
async def test_query_filter_flags_are_combined_in_order(
    client: DBClient | AsyncDBClient,
    router: Router,
    missing_audio: bool,
    missing_info: bool,
    from_active_list: bool,
) -> None:
    route = router.post('/api/tracks') % Response(200, json={'count': 0, 'tracks': []})
    assert (
        await collect(
            client.iter_tracks(
                missing_audio=missing_audio, missing_info=missing_info, from_active_list=from_active_list
            )
        )
        == []
    )
    body = json.loads(route.calls.last.request.content)
    expected = [idx for idx, enabled in enumerate([missing_audio, missing_info, from_active_list], start=1) if enabled]
    assert body['quickFilters'] == expected


@pytest.mark.parametrize('active', [False, True])
async def test_default_list_filter_depends_on_active_list(
    client: DBClient | AsyncDBClient,
    router: Router,
    active: bool,
) -> None:
    active_list = client.lists['MeiHayasaka'] if active else None
    route = router.post('/api/tracks') % Response(200, json={'count': 0, 'tracks': []})
    assert await collect(client.iter_tracks(active_list=active_list)) == []
    body = json.loads(route.calls.last.request.content)
    assert body['activeListId'] == (active_list.id if active_list else None)
    assert body['quickFilters'] == ([3] if active else [])


@pytest.mark.parametrize('kind', ['tracks', 'songs', 'artists'])
@pytest.mark.parametrize(
    'payload',
    [
        {},
        {'count': '1', 'items': []},
        {'count': 1, 'items': {}},
        {'count': 1, 'items': [], 'unexpected': []},
    ],
)
async def test_malformed_query_responses_are_rejected(
    client: DBClient | AsyncDBClient,
    router: Router,
    kind: str,
    payload: dict[str, Any],
) -> None:
    route = router.post(f'/api/{kind}') if kind == 'tracks' else router.get(f'/api/{kind}')
    _ = route % Response(200, json=payload)
    with pytest.raises(QueryError, match='Unexpected query response'):
        await collect(getattr(client, f'iter_{kind}')('test'))
    assert route.call_count == 1


@pytest.mark.parametrize('stop_if_err', [False, True])
@pytest.mark.parametrize('client', ['sync'], indirect=True)
async def test_sync_commit_error_policy_and_queue_state(
    client: DBClient | AsyncDBClient,
    router: Router,
    stop_if_err: bool,
) -> None:
    assert isinstance(client, DBClient)
    track = CSLTrack.from_json(load('sunshine/tracks')[0])
    first, second = (
        CSLExtraMetadata('first', 1, 'Language', 'Japanese'),
        CSLExtraMetadata('second', 1, 'Language', 'English'),
    )
    failure = router.delete(f'/api/track/{track.id}/metadata/{first.id}') % Response(500)
    success = router.delete(f'/api/track/{track.id}/metadata/{second.id}') % Response(200)
    client.track_remove_metadata(track, first, queue=True)
    client.track_remove_metadata(track, second, queue=True)
    if stop_if_err:
        with pytest.raises(HTTPStatusError):
            client.commit(stop_if_err=True)
        assert not success.called and len(client.queue) == 2
    else:
        client.commit(stop_if_err=False)
        assert success.called and not client.queue
    assert failure.call_count == 1
