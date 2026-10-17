import json
from io import StringIO
from pathlib import Path

import pytest
from attrs import evolve
from helpers import finish, request_body
from niquests_mock import MockRouter as Router

from amqcsl import AsyncDBClient, DBClient
from amqcsl.bundles import ParallelBundle, SongDeleteBundle, SongEditBundle, TrackEditBundle
from amqcsl.bundles._misc import CreateListBundle, TrackAddMetadataBundle
from amqcsl.objects import (
    ArtistCredit,
    CSLArtistSample,
    CSLExtraMetadata,
    CSLMetadata,
    CSLSong,
    CSLTrack,
    ExtraMetadata,
    bundle_to_json,
)
from amqcsl.queues import GroupedQueue


@pytest.fixture
def song() -> CSLSong:
    return CSLSong('song', 'Song', None, '2026-01-01', [], [])


@pytest.mark.parametrize('parallel', [False, True])
def test_existing_conflict_rejects_entire_batch(track: CSLTrack, parallel: bool) -> None:
    queue = GroupedQueue()
    queue.track_edit(track, name='Existing')
    original = next(iter(queue)).bundles[0]
    before = queue.to_json()
    incoming = [TrackEditBundle(evolve(track, id='other'), name='Valid'), TrackEditBundle(track, name='Conflict')]
    with pytest.raises(ValueError, match='Overlapping edits.*name'):
        if parallel:
            queue.enqueue(ParallelBundle(incoming))
        else:
            queue.enqueue_batch(incoming)
    assert queue.to_json() == before
    assert next(iter(queue)).bundles[0] is original


def test_conflict_within_batch_leaves_queue_empty(track: CSLTrack) -> None:
    queue = GroupedQueue()
    with pytest.raises(ValueError, match='Overlapping edits'):
        queue.enqueue_batch([TrackEditBundle(track, groups=[]), TrackEditBundle(track, groups=[])])
    assert not queue


@pytest.mark.asyncio
async def test_disjoint_track_edits_send_one_request(
    client: DBClient | AsyncDBClient,
    router: Router,
    track: CSLTrack,
) -> None:
    queue = GroupedQueue()
    queue.track_edit(track, name='Name')
    queue.track_edit(track, original_name='Original', groups=[])
    assert len(queue) == 1
    route = router.put(f'/api/track/{track.id}').respond(status_code=200)
    await finish(client.process_queue(queue, save_to=StringIO()))
    assert route.call_count == 1
    body = json.loads(request_body(route.calls[0].request))
    assert body['name'] == 'Name' and body['originalName'] == 'Original' and body['groupIds'] == []


def test_permitted_fields_keep_last_explicit_values(track: CSLTrack) -> None:
    queue = GroupedQueue(allow_overlapping_edits=True)
    queue.track_edit(track, name='First', original_name='Original', groups=track.groups)
    queue.track_edit(track, name='', groups=[])
    assert next(iter(queue)).bundles == (TrackEditBundle(track, name='', original_name='Original', groups=[]),)


def test_song_edits_merge_when_overlaps_are_allowed(song: CSLSong) -> None:
    strict = GroupedQueue()
    strict.song_edit(song, 'Name', None)
    with pytest.raises(ValueError, match='Overlapping edits'):
        strict.song_edit(song, None, 'Disambiguation')
    queue = GroupedQueue(allow_overlapping_edits=True)
    queue.song_edit(song, 'Name', None)
    queue.song_edit(song, None, 'Disambiguation')
    assert next(iter(queue)).bundles == (SongEditBundle(song, 'Name', 'Disambiguation'),)


@pytest.mark.parametrize('target', ['track', 'song'])
def test_metadata_add_remove_conflicts_are_rejected(target: str, track: CSLTrack, song: CSLSong) -> None:
    queue = GroupedQueue()
    metadata = ExtraMetadata(False, 'Character', 'You')
    old = CSLExtraMetadata('meta', 1, 'Character', 'You')
    if target == 'track':
        queue.track_add_metadata(track, metadata)
    else:
        queue.song_add_metadata(song, metadata)
    before = queue.to_json()
    with pytest.raises(ValueError, match='Overlapping edits'):
        if target == 'track':
            queue.track_remove_metadata(track, old)
        else:
            queue.song_delete_metadata(song, old)
    assert queue.to_json() == before


@pytest.mark.asyncio
@pytest.mark.parametrize('target', ['track', 'song'])
async def test_metadata_additions_send_one_request_with_last_artist_credit(
    target: str,
    client: DBClient | AsyncDBClient,
    router: Router,
    track: CSLTrack,
    song: CSLSong,
) -> None:
    queue = GroupedQueue(allow_overlapping_edits=True)
    artist = CSLArtistSample('artist', 'Artist', '', None, 1)
    first = ExtraMetadata(False, 'Character', 'You')
    second = ExtraMetadata(False, 'Character', 'Chika')
    if target == 'track':
        queue.track_add_metadata(track, first, ArtistCredit(artist, 'Vocal', 'Old credit'))
        queue.track_add_metadata(track, second, ArtistCredit(artist, 'Vocal', 'New credit'))
        path = f'/api/track/{track.id}/metadata'
    else:
        queue.song_add_metadata(song, first, ArtistCredit(artist, 'Vocal', 'Old credit'))
        queue.song_add_metadata(song, second, ArtistCredit(artist, 'Vocal', 'New credit'))
        path = f'/api/song/{song.id}/metadata'
    assert len(queue) == 1
    route = router.post(path).respond(status_code=200)
    await finish(client.process_queue(queue, save_to=StringIO()))
    assert route.call_count == 1
    body = json.loads(request_body(route.calls[0].request))
    assert len(body['artistCredits']) == 1 and body['artistCredits'][0]['credit'] == 'New credit'
    assert len(body['extraMetadatas']) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('target', ['track', 'song'])
@pytest.mark.parametrize('last', ['add', 'remove'])
async def test_add_remove_sends_only_last_operation(
    target: str,
    last: str,
    client: DBClient | AsyncDBClient,
    router: Router,
    track: CSLTrack,
    song: CSLSong,
) -> None:
    queue = GroupedQueue(allow_overlapping_edits=True)
    metadata = ExtraMetadata(False, 'Character', 'You')
    old = CSLExtraMetadata('meta', 1, 'Character', 'You')
    for operation in ('remove', 'add') if last == 'add' else ('add', 'remove'):
        match target, operation:
            case 'track', 'add':
                queue.track_add_metadata(track, metadata)
            case 'track', 'remove':
                queue.track_remove_metadata(track, old)
            case 'song', 'add':
                queue.song_add_metadata(song, metadata)
            case _:
                queue.song_delete_metadata(song, old)
    path = f'/api/track/{track.id}/metadata' if target == 'track' else f'/api/song/{song.id}/metadata'
    addition = router.post(path).respond(status_code=200)
    removal = router.delete(f'{path}/meta').respond(status_code=200)
    assert len(queue) == 1
    await finish(client.process_queue(queue, save_to=StringIO()))
    assert addition.call_count == int(last == 'add')
    assert removal.call_count == int(last == 'remove')


@pytest.mark.asyncio
async def test_repeated_removal_sends_only_latest_metadata_id(
    client: DBClient | AsyncDBClient,
    router: Router,
    track: CSLTrack,
) -> None:
    queue = GroupedQueue(allow_overlapping_edits=True)
    queue.track_remove_metadata(track, CSLExtraMetadata('old', 1, 'Character', 'You'))
    queue.track_remove_metadata(track, CSLExtraMetadata('new', 1, 'Character', 'You'))
    old = router.delete(f'/api/track/{track.id}/metadata/old').respond(status_code=200)
    new = router.delete(f'/api/track/{track.id}/metadata/new').respond(status_code=200)
    await finish(client.process_queue(queue, save_to=StringIO()))
    assert old.call_count == 0 and new.call_count == 1


@pytest.mark.asyncio
async def test_last_import_replaces_upload_and_earlier_imports(
    client: DBClient | AsyncDBClient,
    router: Router,
    track: CSLTrack,
    tmp_path: Path,
) -> None:
    audio = tmp_path / 'audio.flac'
    audio.write_bytes(b'audio')
    queue = GroupedQueue(allow_overlapping_edits=True)
    queue.add_audio(track, audio)
    queue.import_audio(track, evolve(track, id='first', audio_name='first.flac'))
    source = evolve(track, id='last', audio_name='last.flac')
    queue.import_audio(track, source)
    upload = router.post(f'/api/track/{track.id}/presigned-upload').respond(status_code=500)
    imported = router.post(f'/api/track/{track.id}/audio-import').respond(status_code=200)
    await finish(client.process_queue(queue, save_to=StringIO()))
    assert upload.call_count == 0 and imported.call_count == 1
    assert json.loads(request_body(imported.calls[0].request))['url'] == source.audio_url


def test_metadata_filtering_and_override_survive_merging(track: CSLTrack) -> None:
    existing = CSLMetadata(False, [], [CSLExtraMetadata('old', 1, 'Character', 'You')], 1, [])
    queue = GroupedQueue()
    queue.track_add_metadata(track, ExtraMetadata(False, 'Character', 'You'), override=True, existing_meta=existing)
    queue.track_add_metadata(track, ExtraMetadata(False, 'Character', 'Chika'))
    merged = next(iter(queue)).bundles[0]
    assert isinstance(merged, TrackAddMetadataBundle)
    assert merged.metas == [ExtraMetadata(False, 'Character', 'Chika')]
    with pytest.raises(ValueError, match='Overlapping edits.*override'):
        queue.track_add_metadata(track, override=False)
    queue.allow_overlapping_edits = True
    queue.track_add_metadata(track, override=False)
    assert next(iter(queue)).bundles[0] == TrackAddMetadataBundle(track, merged.metas, False)


@pytest.mark.parametrize('delete_first', [False, True])
def test_song_deletion_is_exclusive_unless_overlaps_are_allowed(song: CSLSong, delete_first: bool) -> None:
    for allow in (False, True):
        queue = GroupedQueue(allow_overlapping_edits=allow)
        operations = ('delete', 'edit') if delete_first else ('edit', 'delete')
        for idx, operation in enumerate(operations):
            if idx == 1 and not allow:
                with pytest.raises(ValueError, match='Overlapping edits'):
                    if operation == 'edit':
                        queue.song_edit(song, 'Name', None)
                    else:
                        queue.song_delete(song)
            elif operation == 'edit':
                queue.song_edit(song, 'Name', None)
            else:
                queue.song_delete(song)
        if allow:
            assert next(iter(queue)).bundles == (SongDeleteBundle(song),)


def test_completed_fields_stop_conflicting_and_failed_batch_keeps_original(track: CSLTrack) -> None:
    queue = GroupedQueue()
    queue.track_edit(track, name='First')
    queue.complete(next(iter(queue)).bundles[0])
    queue.track_edit(track, name='Second')
    original = next(iter(queue)).bundles[0]
    before = queue.to_json()
    with pytest.raises(TypeError, match='GroupedQueue does not support'):
        queue.enqueue_batch([TrackEditBundle(track, original_name='Valid'), CreateListBundle('Unsupported')])
    assert queue.to_json() == before and next(iter(queue)).bundles[0] is original


def test_recovery_preserves_overlap_option_and_normalized_requests(track: CSLTrack) -> None:
    queue = GroupedQueue(allow_overlapping_edits=True, worker_ratio=2)
    queue.track_edit(track, name='First')
    queue.track_edit(track, name='Last')
    queue.track_remove_metadata(track, CSLExtraMetadata('meta', 1, 'Character', 'You'))
    stream = StringIO()
    queue.to_file(stream)
    stream.seek(0)
    restored = GroupedQueue.from_file(stream)
    assert restored.allow_overlapping_edits
    assert restored.to_json() == queue.to_json()
    restored.track_edit(track, name='After reload')
    assert len(restored) == 2


@pytest.mark.asyncio
async def test_same_metadata_id_with_different_snapshots_is_deleted_once(
    client: DBClient | AsyncDBClient,
    router: Router,
    track: CSLTrack,
) -> None:
    old = CSLExtraMetadata('same', 1, 'Character', 'Old value')
    new = evolve(old, value='New value')
    queue = GroupedQueue()
    queue.track_remove_metadata(track, old)
    with pytest.raises(ValueError, match='metadata_id'):
        queue.track_remove_metadata(track, new)
    queue.allow_overlapping_edits = True
    queue.track_remove_metadata(track, new)
    route = router.delete(f'/api/track/{track.id}/metadata/same').respond(status_code=200)
    await finish(client.process_queue(queue, save_to=StringIO()))
    assert route.call_count == 1


def test_loading_group_that_normalizes_to_no_work_reports_invalid_layout(track: CSLTrack) -> None:
    data = GroupedQueue().to_json()
    data['groups'] = [{'kind': 'tracks', 'bundles': [bundle_to_json(TrackAddMetadataBundle(track, []))]}]
    with pytest.raises(ValueError, match='Serialized groups'):
        GroupedQueue.from_json(data)


@pytest.mark.asyncio
async def test_removal_matching_multiple_old_snapshots_is_not_duplicated(
    client: DBClient | AsyncDBClient,
    router: Router,
    track: CSLTrack,
) -> None:
    queue = GroupedQueue(allow_overlapping_edits=True)
    queue.track_remove_metadata(track, CSLExtraMetadata('one', 1, 'Character', 'One'))
    queue.track_remove_metadata(track, CSLExtraMetadata('two', 1, 'Character', 'Two'))
    queue.track_remove_metadata(track, CSLExtraMetadata('two', 1, 'Character', 'One'))
    assert len(queue) == 1
    route = router.delete(f'/api/track/{track.id}/metadata/two').respond(status_code=200)
    await finish(client.process_queue(queue, save_to=StringIO()))
    assert route.call_count == 1
