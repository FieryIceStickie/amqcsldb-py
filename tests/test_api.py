from pathlib import Path

from helpers import load
from httpx import Response
from respx import Router

from amqcsl import DBClient
from amqcsl.objects import CSLArtist, CSLMetadata, CSLSong, ExtraMetadata
from amqcsl.objects._db_types import AlbumTrack


def test_list(router: Router, client: DBClient):
    data = load('lists')
    for list_json in data:
        csl_list = client.lists[list_json['name']]
        assert list_json['id'] == csl_list.id
        assert list_json['name'] == csl_list.name
        assert list_json['count'] == csl_list.count

    assert router.routes['lists'].call_count == 1


def test_group(router: Router, client: DBClient):
    data = load('groups')
    for group_json in data:
        group = client.groups[group_json['name']]
        assert group_json['id'] == group.id
        assert group_json['name'] == group.name

    assert router.routes['groups'].call_count == 1


def test_track_by_list(router: Router, client: DBClient):
    expected = load('idolypride/tracks')
    route = router.post(
        '/api/tracks',
        name='tracks',
        json__activeListId='mock-id-list-meihayasaka',
        json__quickFilters__0=3,
    ) % Response(200, json={'tracks': expected, 'count': len(expected)})
    mei_list = client.lists['MeiHayasaka']
    tracks = {track.id for track in client.iter_tracks(active_list=mei_list)}
    assert tracks == {track['id'] for track in expected}
    assert route.call_count == 1


def test_track_by_group(router: Router, client: DBClient):
    expected = load('idolypride/tracks')
    route = router.post(
        '/api/tracks',
        name='tracks',
        json__groupFilters__0='mock-id-group-idolypride',
    ) % Response(200, json={'tracks': expected, 'count': len(expected)})
    idoly_pride_group = client.groups['IDOLY PRIDE']
    tracks = {track.id for track in client.iter_tracks(groups=[idoly_pride_group])}
    assert tracks == {track['id'] for track in expected}
    assert route.call_count == 1


def test_track_with_pages(router: Router, client: DBClient):
    expected = load('idolypride/tracks')
    assert len(expected) == 8
    first_page = router.post(
        '/api/tracks',
        name='tracks_first',
        json__groupFilters__0='mock-id-group-idolypride',
        json__skip=0,
        json__take=4,
    ) % Response(200, json={'tracks': expected[:4], 'count': len(expected)})
    second_page = router.post(
        '/api/tracks',
        name='tracks_second',
        json__groupFilters__0='mock-id-group-idolypride',
        json__skip=4,
        json__take=4,
    ) % Response(200, json={'tracks': expected[4:], 'count': len(expected)})
    idoly_pride_group = client.groups['IDOLY PRIDE']
    tracks = {track.id for track in client.iter_tracks(groups=[idoly_pride_group], batch_size=4)}
    assert tracks == {track['id'] for track in expected}
    assert first_page.call_count == 1
    assert second_page.call_count == 1


def test_track_search(router: Router, client: DBClient):
    expected = load('idolypride/tracks')
    target_track_id = 'mock-id-track-blueskysummer'
    expected_track = next(track for track in expected if track['id'] == target_track_id)
    route = router.post(
        '/api/tracks',
        name='tracks',
        json__searchTerm='Blue sky',
    ) % Response(200, json={'tracks': [expected_track], 'count': 1})
    tracks = {track.id for track in client.iter_tracks('Blue sky')}
    assert tracks == {target_track_id}
    assert route.call_count == 1


def test_artist_search(router: Router, client: DBClient):
    expected = [
        track
        for track in load('idolypride/artists')  #
        if 'idoly pride' in (track['disambiguation'] or '').lower()
    ]
    route = router.get(
        '/api/artists',
        name='artists',
        params={'searchTerm': 'IDOLY PRIDE'},
    ) % Response(200, json={'artists': expected, 'count': len(expected)})
    assert {obj.id for obj in client.iter_artists('IDOLY PRIDE')} == {obj['id'] for obj in expected}
    assert route.call_count == 1


def test_song_search(router: Router, client: DBClient):
    expected = load('idolypride/songs')
    route = router.get(
        '/api/songs',
        name='songs',
        params={'searchTerm': 'IDOLY PRIDE'},
    ) % Response(200, json={'songs': expected, 'count': len(expected)})
    assert {obj.id for obj in client.iter_songs('IDOLY PRIDE')} == {obj['id'] for obj in expected}
    assert route.call_count == 1


def test_get_song(router: Router, client: DBClient):
    target_id = 'mock-id-song-blueskysummer'
    expected_song_sample = next(
        song
        for song in load('idolypride/songs')  #
        if song['id'] == target_id
    )
    expected_song = load('idolypride/songs/blueskysummer')
    iter_route = router.get(
        '/api/songs',
        name='iter_song',
        params={'searchTerm': 'Blue sky summer'},
    ) % Response(200, json={'songs': [expected_song_sample], 'count': 1})
    song_route = router.get(
        f'/api/song/{expected_song["id"]}',
        name='get_song',
    ) % Response(200, json=expected_song)

    song_sample = next(client.iter_songs('Blue sky summer'))
    song = client.get_song(song_sample)
    assert song == CSLSong.from_json(expected_song)
    assert iter_route.call_count == 1
    assert song_route.call_count == 1


def test_get_artist(router: Router, client: DBClient):
    target_id = 'mock-id-artist-shukasaitou'
    expected_artist_sample = next(
        artist
        for artist in load('sunshine/artists')  #
        if artist['id'] == target_id
    )
    expected_artist = load('sunshine/artists/shukasaitou')
    iter_route = router.get(
        '/api/artists',
        name='iter_artists',
        params={'searchTerm': 'Shuka Saitou'},
    ) % Response(200, json={'artists': [expected_artist_sample], 'count': 1})
    artist_route = router.get(
        f'/api/artist/{expected_artist["id"]}',
        name='get_artist',
    ) % Response(200, json=expected_artist)

    artist_sample = next(client.iter_artists('Shuka Saitou'))
    artist = client.get_artist(artist_sample)
    assert artist == CSLArtist.from_json(expected_artist)
    assert iter_route.call_count == 1
    assert artist_route.call_count == 1


def test_get_metadata(router: Router, client: DBClient):
    target_id = 'mock-id-track-sukiforyou-you'
    expected_track = next(
        track
        for track in load('sunshine/tracks')  #
        if track['id'] == target_id
    )
    expected_meta = load('sunshine/metadata/sukiforyou')
    track_route = router.post(
        '/api/tracks',
        name='iter_tracks',
        json__searchTerm='SUKI for you',
    ) % Response(200, json={'tracks': [expected_track], 'count': 1})
    meta_route = router.get(
        f'/api/track/{target_id}/metadata',
        name='get_meta',
    ) % Response(200, json=expected_meta)

    track = next(client.iter_tracks('SUKI for you'))
    meta = client.get_metadata(track)
    assert meta == CSLMetadata.from_json(expected_meta)
    assert track_route.call_count == 1
    assert meta_route.call_count == 1


def test_get_no_metadata(router: Router, client: DBClient):
    target_id = 'mock-id-track-sukiforyou-you'
    expected_track = next(
        track
        for track in load('sunshine/tracks')  #
        if track['id'] == target_id
    )
    track_route = router.post(
        '/api/tracks',
        name='iter_tracks',
        json__searchTerm='SUKI for you',
    ) % Response(200, json={'tracks': [expected_track], 'count': 1})
    meta_route = router.get(
        f'/api/track/{target_id}/metadata',
        name='get_meta',
    ) % Response(404, json=load('errors/no_meta'))

    track = next(client.iter_tracks('SUKI for you'))
    meta = client.get_metadata(track)
    assert meta is None
    assert track_route.call_count == 1
    assert meta_route.call_count == 1


def test_create_list(router: Router, client: DBClient, cookies: dict[str, str]):
    lists_route = router.get('/api/lists', name='lists', cookies=cookies)
    lists = load('lists')
    mock_list = {'id': 'mock-id-list-youmei', 'name': 'youmei', 'count': 16}
    lists_route.side_effect = [
        Response(200, json=lists),
        Response(200, json=lists + [mock_list]),
    ]

    mei_list = client.lists['MeiHayasaka']
    you_list = client.lists['yousoro']
    assert lists_route.call_count == 1

    add_route = router.post(
        '/api/list',
        name='add_list',
        json={'importListIds': [mei_list.id, you_list.id], 'name': 'youmei'},
    ) % Response(200, json={'ok': True})

    youmei_list = client.create_list('youmei', mei_list, you_list)
    assert youmei_list.id == mock_list['id']
    assert lists_route.call_count == 2
    assert add_route.call_count == 1


def test_list_edit(router: Router, client: DBClient):
    tracks = load('idolypride/tracks')
    assert tracks
    _ = router.post(
        '/api/tracks',
        name='remove_track',
        json__activeListId='mock-id-list-meihayasaka',
        json__quickFilters__0=3,
    ) % Response(200, json={'tracks': tracks, 'count': len(tracks)})
    remove_track_json = tracks[0]

    target_id = 'mock-id-track-sukiforyou-you'
    add_track_json = next(
        track
        for track in load('sunshine/tracks')  #
        if track['id'] == target_id
    )
    _ = router.post(
        '/api/tracks',
        name='add_track',
        json__searchTerm='SUKI for you',
    ) % Response(200, json={'tracks': [add_track_json], 'count': 1})

    mei_list = client.lists['MeiHayasaka']
    route = router.put(
        f'/api/list/{mei_list.id}',
        name='list_edit',
        json__addSongIds=[add_track_json['id']],
        json__name='meichan',
        json__removeSongIds=[remove_track_json['id']],
    ) % Response(200)

    add_track = next(client.iter_tracks('SUKI for you'))
    remove_track = next(client.iter_tracks(active_list=mei_list))
    client.list_edit(
        mei_list,
        name='meichan',
        add=[add_track],
        remove=[remove_track],
    )
    assert route.call_count == 1


def test_add_group(router: Router, client: DBClient):
    route = router.post(
        '/api/group',
        name='add_group',
        json={'name': 'Genshin Impact'},
    ) % Response(200, json={'id': 'mock-id-group-genshinimpact', 'name': 'Genshin Impact'})
    group = client.add_group('Genshin Impact')
    assert group.id == 'mock-id-group-genshinimpact'
    assert group.name == 'Genshin Impact'
    assert route.call_count == 1


def test_track_add_metadata(router: Router, client: DBClient):
    target_id = 'mock-id-track-sukiforyou-you'
    track_json = next(
        track
        for track in load('sunshine/tracks')  #
        if track['id'] == target_id
    )
    meta_json = load('sunshine/metadata/sukiforyou')
    meta_json['extraMetas'][0]['value'] = 'Chika Takami'
    _ = router.post(
        '/api/tracks',
        name='iter_tracks',
        json__searchTerm='SUKI for you',
    ) % Response(200, json={'tracks': [track_json], 'count': 1})
    _ = router.get(
        f'/api/track/{target_id}/metadata',
        name='get_meta',
    ) % Response(200, json=meta_json)
    route = router.post(
        f'/api/track/{track_json["id"]}/metadata',
        name='post_meta',
        json__override=False,
        json__extraMetadatas=[{'isArtist': True, 'type': 'Character', 'value': 'You Watanabe'}],
    ) % Response(200)
    track = next(client.iter_tracks('SUKI for you'))
    meta = client.get_metadata(track)
    client.track_add_metadata(
        track,
        ExtraMetadata(True, 'Character', 'Chika Takami'),
        ExtraMetadata(True, 'Character', 'You Watanabe'),
        existing_meta=meta,
        override=False,
    )
    assert route.call_count == 1


def test_track_remove_metadata(router: Router, client: DBClient):
    target_id = 'mock-id-track-sukiforyou-you'
    track_json = next(
        track
        for track in load('sunshine/tracks')  #
        if track['id'] == target_id
    )
    meta_json = load('sunshine/metadata/sukiforyou')
    _ = router.post(
        '/api/tracks',
        name='iter_tracks',
        json__searchTerm='SUKI for you',
    ) % Response(200, json={'tracks': [track_json], 'count': 1})
    _ = router.get(
        f'/api/track/{target_id}/metadata',
        name='get_meta',
    ) % Response(200, json=meta_json)
    route = router.delete(
        f'/api/track/{track_json["id"]}/metadata/{meta_json["extraMetas"][0]["id"]}',
        name='post_meta',
    ) % Response(200)
    track = next(client.iter_tracks('SUKI for you'))
    meta = client.get_metadata(track)
    assert meta is not None
    assert len(meta.extra_metas) == 1
    client.track_remove_metadata(track, meta.extra_metas[0])
    assert route.call_count == 1


def test_track_metadata_queue(router: Router, client: DBClient):
    target_id = 'mock-id-track-sukiforyou-you'
    track_json = next(
        track
        for track in load('sunshine/tracks')  #
        if track['id'] == target_id
    )
    meta_json = load('sunshine/metadata/sukiforyou')
    _ = router.post(
        '/api/tracks',
        name='iter_tracks',
        json__searchTerm='SUKI for you',
    ) % Response(200, json={'tracks': [track_json], 'count': 1})
    _ = router.get(
        f'/api/track/{target_id}/metadata',
        name='get_meta',
    ) % Response(200, json=meta_json)
    route = router.delete(
        f'/api/track/{track_json["id"]}/metadata/{meta_json["extraMetas"][0]["id"]}',
        name='post_meta',
    ) % Response(200)
    track = next(client.iter_tracks('SUKI for you'))
    meta = client.get_metadata(track)
    assert meta is not None
    assert len(meta.extra_metas) == 1
    client.track_remove_metadata(track, meta.extra_metas[0], queue=True)
    assert route.call_count == 0
    client.commit()
    assert route.call_count == 1


def test_track_edit(router: Router, client: DBClient):
    target_id = 'mock-id-track-sukiforyou-you'
    track_json = next(
        track
        for track in load('sunshine/tracks')  #
        if track['id'] == target_id
    )
    _ = router.post(
        '/api/tracks',
        name='iter_tracks',
        json__searchTerm='SUKI for you',
    ) % Response(200, json={'tracks': [track_json], 'count': 1})
    route = router.put(
        f'/api/track/{track_json["id"]}',
        name='post_meta',
        json__name='SUKI for you, DREAM for you! (You Watanabe Solo ver.)',
    ) % Response(200)
    track = next(client.iter_tracks('SUKI for you'))
    client.track_edit(track, name='SUKI for you, DREAM for you! (You Watanabe Solo ver.)')
    assert route.call_count == 1


def test_add_album(router: Router, client: DBClient):
    album_name = 'Love Live! Sunshine!! Duo & Trio Collection CD Vol. 2 Winter Vacation'
    original_album_name = 'Duo & Trio Collection CD Vol. 2 Winter Vacation'
    year = 2024
    album_track = AlbumTrack('Misty Frosty Love', 'Misty frosty love', 'Shuka Saitou, Rikako Aida')
    track = album_track.to_json(1, 1, 1)
    group = client.groups['Love Live! Sunshine!!']
    route = router.post(
        '/api/album',
        name='album',
        json__album=album_name,
        json__discTotal=1,
        json__groupIds=[group.id],
        json__originalAlbum=original_album_name,
        json__year=year,
        json__tracks=[track],
    ) % Response(200)
    client.add_album(album_name, original_album_name, year, [group], [[album_track]])
    assert route.call_count == 1


def test_add_audio(router: Router, client: DBClient, tmp_path: Path):
    audio_name = 'mock_audio.flac'
    audio_path = tmp_path / audio_name
    audio_path.write_bytes(b'abcde')
    target_id = 'mock-id-track-sukiforyou-you'
    track_json = next(
        track
        for track in load('sunshine/tracks')  #
        if track['id'] == target_id
    )
    _ = router.post(
        '/api/tracks',
        name='iter_tracks',
        json__searchTerm='SUKI for you',
    ) % Response(200, json={'tracks': [track_json], 'count': 1})
    presign_route = router.post(
        f'/api/track/{track_json["id"]}/presigned-upload',
        name='presign',
        json={},
    ) % Response(
        200,
        json={
            'sessionId': 'mock-sessionid',
            'key': 'mock-key',
            'url': 'https://mock-url',
        },
    )
    upload_route = router.post(
        'https://mock-url',
        name='upload',
        params={'sessionId': 'mock-sessionid', 'key': 'mock-key'},
    ) % Response(200)

    track = next(client.iter_tracks('SUKI for you'))
    client.add_audio(track, audio_path)
    assert presign_route.call_count == 1
    assert upload_route.call_count == 1
