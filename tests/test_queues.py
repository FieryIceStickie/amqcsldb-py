import asyncio
import json
import logging
from io import StringIO
from pathlib import Path
from typing import Any, ClassVar, cast

import niquests
import pytest
import rich.repr
from attrs import frozen
from helpers import finish, load, mock_response
from niquests_mock import MockRouter as Router
from rich.console import Console

from amqcsl import AsyncDBClient, DBClient
from amqcsl.bundles import Bundle, ParallelBundle, SingleVendor, action_bundle_compose, httpClient
from amqcsl.bundles._misc import GroupEditBundle, TrackDeleteMetadataBundle
from amqcsl.bundles._pages import IterTracksBundle
from amqcsl.objects import (
    AlbumTrack,
    ArtistCredit,
    CSLArtistSample,
    CSLExtraMetadata,
    CSLGroup,
    CSLList,
    CSLMetadata,
    CSLSong,
    CSLSongArtistCredit,
    CSLSongSample,
    CSLTrack,
    CSLTrackRef,
    ExtraMetadata,
    NewSong,
    TrackPutArtistCredit,
    from_json,
)
from amqcsl.objects._json_types import JSONTrack
from amqcsl.queues import QueueGroup, StandardQueue


@pytest.fixture
def track() -> CSLTrack:
    return from_json(cast(JSONTrack, load('sunshine/tracks')[0]), CSLTrack)


@pytest.fixture
def song() -> CSLSong:
    return CSLSong('song', 'Song', None, '2026-01-01', [], [])


@frozen
class CustomBundle(Bundle[str]):
    name: str

    def vendor(self, client: httpClient) -> SingleVendor[str]:
        yield from ()
        return self.name

    def __rich_repr__(self) -> rich.repr.Result:
        yield 'name', self.name


class BrokenStream(StringIO):
    def write(self, text: str) -> int:
        raise OSError('Cannot write recovery output')


class OneWorkerQueue(StandardQueue):
    def num_workers(self, max_request_count: int) -> int:
        return min(1, super().num_workers(max_request_count))


def leaves(queue: StandardQueue) -> list[Bundle[Any]]:
    return [bundle for group in queue for bundle in group.bundles]


def test_recursive_parallel_groups_and_identity_completion() -> None:
    first, second, third = CustomBundle('same'), CustomBundle('same'), CustomBundle('third')
    queue = StandardQueue()
    queue.enqueue(ParallelBundle([first, ParallelBundle([second, third])]))
    assert [*queue] == [QueueGroup((first, second, third))]
    snapshot = [*queue]
    queue.complete(second)
    assert leaves(queue) == [first, third]
    assert snapshot[0].bundles[1] is second
    assert len(queue) == 2
    with pytest.raises(ValueError, match='not pending'):
        queue.complete(second)
    queue.complete(first)
    queue.complete(third)
    assert not queue and [*queue] == []


def test_action_bundle_compose_and_atomic_stream_rejection() -> None:
    queue = StandardQueue()
    first = GroupEditBundle(CSLGroup('group', 'Old'), 'New')
    queue.enqueue(action_bundle_compose([first]))
    assert leaves(queue) == [first]
    stream = cast(Bundle[Any], IterTracksBundle.from_client(DBClient(), ''))
    with pytest.raises(TypeError, match='Streaming'):
        queue.enqueue(ParallelBundle([first, stream]))
    assert leaves(queue) == [first]
    queue.enqueue(ParallelBundle[Any]([]))
    assert len(queue) == 1


def test_processing_guard_is_reset_on_error() -> None:
    queue = StandardQueue()
    bundle = CustomBundle('pending')
    queue.enqueue(bundle)
    with pytest.raises(ValueError, match='test failure'), queue.processing():
        with pytest.raises(RuntimeError, match='Cannot enqueue'):
            queue.enqueue(CustomBundle('new'))
        with pytest.raises(RuntimeError, match='already processing'), queue.processing():
            pass
        queue.complete(bundle)
        raise ValueError('test failure')
    queue.enqueue(CustomBundle('new'))
    assert len(queue) == 1


def test_display_uses_rich_repr() -> None:
    queue = StandardQueue()
    queue.enqueue(CustomBundle('Readable name'))
    output = StringIO()
    console = Console(file=output, width=100)
    console.print(queue)
    assert "CustomBundle(name='Readable name')" in output.getvalue()
    console.print(queue.display({}))


@pytest.mark.parametrize(
    'operation',
    [
        'create_list',
        'list_edit',
        'list_delete',
        'create_group',
        'group_edit',
        'group_delete',
        'song_edit',
        'song_delete',
        'song_add_metadata',
        'song_delete_metadata',
        'track_add_metadata',
        'track_remove_metadata',
        'track_edit',
        'create_album',
        'add_audio',
        'import_audio',
    ],
)
def test_builtin_edits_round_trip(
    operation: str,
    track: CSLTrack,
    song: CSLSong,
    tmp_path: Path,
) -> None:
    queue = StandardQueue()
    artist = CSLArtistSample('artist', 'Artist', 'Original', None, 1)
    group = CSLGroup('group', 'Group')
    csl_list = CSLList('list', 'List', 1)
    metadata = ExtraMetadata(False, 'Character', 'Character name')
    credit = ArtistCredit(artist, 'Vocal', 'Credit')
    old_meta = CSLExtraMetadata('metadata', 2, 'Character', 'Old name')
    old_credit = CSLSongArtistCredit('credit', 'Vocal', artist)
    audio = tmp_path / 'audio.flac'
    audio.write_bytes(b'audio')
    match operation:
        case 'create_list':
            queue.create_list('New list', csl_list)
        case 'list_edit':
            queue.list_edit(csl_list, name='New name', add=iter([CSLTrackRef(track.id)]), remove=[track])
        case 'list_delete':
            queue.list_delete(csl_list)
        case 'create_group':
            queue.create_group('New group')
        case 'group_edit':
            queue.group_edit(group, 'New name')
        case 'group_delete':
            queue.group_delete(group)
        case 'song_edit':
            queue.song_edit(song, 'New name', 'New disambiguation')
        case 'song_delete':
            queue.song_delete(song)
        case 'song_add_metadata':
            queue.song_add_metadata(song, metadata, credit)
        case 'song_delete_metadata':
            queue.song_delete_metadata(song, old_credit)
        case 'track_add_metadata':
            queue.track_add_metadata(
                track, metadata, credit, override=True, existing_meta=CSLMetadata(False, [], [old_meta], 1, [])
            )
        case 'track_remove_metadata':
            queue.track_remove_metadata(track, old_meta)
        case 'track_edit':
            queue.track_edit(
                track,
                artist_credits=[TrackPutArtistCredit(artist, 'Credit', ' & ')],
                groups=[group],
                name='New',
                original_name='Original',
                original_artist='Original artist',
                song=NewSong('New song', 'Disambiguation'),
                type='OffVocal',
            )
        case 'create_album':
            queue.create_album(
                'Album', 'Original album', 2026, iter([group]), [[AlbumTrack('Track', 'Original', 'Artist')]]
            )
        case 'add_audio':
            queue.add_audio(track, audio)
        case 'import_audio':
            queue.import_audio(track, track)
        case _:
            pytest.fail(f'Unknown operation: {operation}')
    stream = StringIO()
    queue.to_file(stream)
    assert not stream.closed
    stream.seek(0)
    restored = StandardQueue.from_file(stream)
    assert restored.to_json() == queue.to_json()
    assert leaves(restored) == leaves(queue)


@pytest.mark.parametrize('song_value', ['sample', 'new', 'none'])
def test_track_edit_song_union_round_trip(track: CSLTrack, song_value: str) -> None:
    song: NewSong | CSLSongSample | None = None
    if song_value == 'sample':
        song = CSLSongSample('song', 'Song', None, '2026-01-01')
    elif song_value == 'new':
        song = NewSong('Song', None)
    queue = StandardQueue()
    queue.track_edit(track, song=song)
    assert leaves(StandardQueue.from_json(queue.to_json())) == leaves(queue)


def test_round_trip_preserves_batches(track: CSLTrack) -> None:
    first = TrackDeleteMetadataBundle(track, CSLExtraMetadata('one', 1, 'Language', 'Japanese'))
    second = TrackDeleteMetadataBundle(track, CSLExtraMetadata('two', 1, 'Language', 'English'))
    queue = StandardQueue()
    queue.enqueue(ParallelBundle([first, ParallelBundle([second])]))
    queue.enqueue(TrackDeleteMetadataBundle(track, first.meta))
    restored = StandardQueue.from_json(queue.to_json())
    assert [len(group.bundles) for group in restored] == [2, 1]


def test_custom_bundle_fallback_and_complete_load_failure() -> None:
    queue = StandardQueue()
    queue.create_group('Built in')
    queue.enqueue(CustomBundle('Recover manually'))
    stream = StringIO()
    queue.to_file(stream)
    data = json.loads(stream.getvalue())
    assert "CustomBundle(name='Recover manually')" == data['groups'][1][0]['repr']
    stream.seek(0)
    with pytest.raises(ValueError, match='implement serialization support.*Recover manually'):
        StandardQueue.from_file(stream)
    assert len(queue) == 2


@pytest.mark.parametrize('version', ['1.0', '0.1', '0', 'x.y', '-1.0', '0.0.0'])
def test_rejects_unknown_or_invalid_version(version: str) -> None:
    data = StandardQueue().to_json()
    data['version'] = version
    with pytest.raises(ValueError, match='version'):
        StandardQueue.from_json(data)


def test_concrete_queue_owns_type_and_version() -> None:
    class AnotherQueue(StandardQueue):
        queue_type: ClassVar[str] = 'another'
        version: ClassVar[tuple[int, int]] = (1, 0)

    stream = StringIO()
    AnotherQueue().to_file(stream)
    stream.seek(0)
    with pytest.raises(ValueError, match='queue type'):
        StandardQueue.from_file(stream)
    stream.seek(0)
    assert isinstance(AnotherQueue.from_file(stream), AnotherQueue)


@pytest.mark.asyncio
async def test_stream_position_stdout_and_empty_success(
    client: DBClient | AsyncDBClient,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    queue = StandardQueue()
    queue.enqueue(CustomBundle('result is discarded'))
    output = StringIO('prefix:tail')
    output.seek(len('prefix:'))
    with caplog.at_level(logging.INFO, logger='amqcsl.queue'):
        await finish(client.process_queue(queue, save_to=output))
    assert output.getvalue().startswith('prefix:{')
    assert json.loads(output.getvalue()[len('prefix:') :])['groups'] == []
    assert not output.closed and not queue
    assert 'Writing remaining queue to StringIO' in caplog.text
    await finish(client.process_queue(queue))
    assert json.loads(capsys.readouterr().out)['groups'] == []


@pytest.mark.asyncio
@pytest.mark.parametrize('stop_if_err', [False, True])
async def test_partial_group_recovery_and_retry(
    client: DBClient | AsyncDBClient,
    router: Router,
    track: CSLTrack,
    stop_if_err: bool,
) -> None:
    if isinstance(client, AsyncDBClient):
        client.max_request_count = 1
    queue = StandardQueue()
    metas = [CSLExtraMetadata(name, 1, 'Language', name) for name in ('success', 'failure', 'skipped')]
    bundles = [TrackDeleteMetadataBundle(track, meta) for meta in metas]
    queue.enqueue(ParallelBundle(bundles))
    success = router.delete(f'/api/track/{track.id}/metadata/success').respond(status_code=200)
    failure = router.delete(f'/api/track/{track.id}/metadata/failure').respond(status_code=500)
    skipped = router.delete(f'/api/track/{track.id}/metadata/skipped').respond(status_code=200)
    output = StringIO()
    if stop_if_err:
        with pytest.raises(niquests.HTTPError):
            await finish(client.process_queue(queue, save_to=output))
    else:
        await finish(client.process_queue(queue, save_to=output, stop_if_err=False))
    assert success.call_count == failure.call_count == 1
    assert not skipped.called
    assert leaves(queue) == bundles[1:]
    output.seek(0)
    restored = StandardQueue.from_file(output)
    assert leaves(restored) == bundles[1:]
    failure.respond(status_code=200)
    await finish(client.process_queue(restored, save_to=StringIO()))
    assert not restored and success.call_count == 1 and skipped.call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('stop_if_err', [False, True])
async def test_stop_taking_groups(
    client: DBClient | AsyncDBClient,
    router: Router,
    track: CSLTrack,
    stop_if_err: bool,
) -> None:
    queue = OneWorkerQueue()
    queue.track_remove_metadata(track, CSLExtraMetadata('fail', 1, 'Language', 'Japanese'))
    queue.track_remove_metadata(track, CSLExtraMetadata('next', 1, 'Language', 'English'))
    router.delete(f'/api/track/{track.id}/metadata/fail').respond(status_code=500)
    next_route = router.delete(f'/api/track/{track.id}/metadata/next').respond(status_code=200)
    if stop_if_err:
        with pytest.raises(niquests.HTTPError):
            await finish(client.process_queue(queue, save_to=StringIO()))
    else:
        await finish(client.process_queue(queue, stop_if_err=False, save_to=StringIO()))
    assert next_route.called is not stop_if_err
    assert len(queue) == (2 if stop_if_err else 1)


@pytest.mark.asyncio
async def test_processing_and_save_errors_are_grouped(
    client: DBClient | AsyncDBClient,
    router: Router,
    track: CSLTrack,
) -> None:
    queue = StandardQueue()
    queue.track_remove_metadata(track, CSLExtraMetadata('fail', 1, 'Language', 'Japanese'))
    router.delete(f'/api/track/{track.id}/metadata/fail').respond(status_code=500)
    with pytest.raises(ExceptionGroup) as caught:
        await finish(client.process_queue(queue, save_to=BrokenStream()))
    assert isinstance(caught.value.exceptions[0], niquests.HTTPError)
    assert isinstance(caught.value.exceptions[1], OSError)
    assert len(queue) == 1
    queue.create_group('Guard released')


@pytest.mark.asyncio
async def test_successful_processing_propagates_save_error(client: DBClient | AsyncDBClient) -> None:
    queue = StandardQueue()
    queue.enqueue(CustomBundle('No requests'))
    with pytest.raises(OSError, match='Cannot write'):
        await finish(client.process_queue(queue, save_to=BrokenStream()))
    assert not queue
    queue.create_group('Guard released')


@pytest.mark.asyncio
@pytest.mark.parametrize('client', ['async'], indirect=True)
async def test_child_cancellation_propagates_and_saves_remaining(
    client: DBClient | AsyncDBClient,
    router: Router,
    track: CSLTrack,
) -> None:
    assert isinstance(client, AsyncDBClient)
    client.max_request_count = 1
    queue = StandardQueue()
    queue.enqueue(
        ParallelBundle(
            [
                TrackDeleteMetadataBundle(track, CSLExtraMetadata(name, 1, 'Language', name))
                for name in ('cancel', 'waiting')
            ]
        )
    )

    async def cancel(_: niquests.PreparedRequest) -> niquests.Response:
        raise asyncio.CancelledError

    router.delete(f'/api/track/{track.id}/metadata/cancel').mock(side_effect=cancel)
    waiting = router.delete(f'/api/track/{track.id}/metadata/waiting').respond(status_code=200)
    output = StringIO()
    with pytest.raises(asyncio.CancelledError):
        await client.process_queue(queue, save_to=output)
    assert not waiting.called
    assert len(queue) == 2
    output.seek(0)
    assert len(StandardQueue.from_file(output)) == 2
    queue.create_group('Guard released')


@pytest.mark.asyncio
@pytest.mark.parametrize('client', ['async'], indirect=True)
async def test_cancellation_drains_workers_and_saves_remaining(
    client: DBClient | AsyncDBClient,
    router: Router,
    track: CSLTrack,
) -> None:
    assert isinstance(client, AsyncDBClient)
    client.max_request_count = 1
    queue = StandardQueue()
    queue.enqueue(
        ParallelBundle(
            [
                TrackDeleteMetadataBundle(track, CSLExtraMetadata(name, 1, 'Language', name))
                for name in ('success', 'active', 'waiting')
            ]
        )
    )
    started = asyncio.Event()
    drained = asyncio.Event()
    router.delete(f'/api/track/{track.id}/metadata/success').respond(status_code=200)

    async def hold(_: niquests.PreparedRequest) -> niquests.Response:
        started.set()
        try:
            await asyncio.Event().wait()
            return mock_response(200)
        finally:
            drained.set()

    router.delete(f'/api/track/{track.id}/metadata/active').mock(side_effect=hold)
    waiting = router.delete(f'/api/track/{track.id}/metadata/waiting').respond(status_code=200)
    output = StringIO()
    task = asyncio.create_task(client.process_queue(queue, save_to=output))
    await asyncio.wait_for(started.wait(), timeout=1)
    with pytest.raises(RuntimeError, match='Cannot enqueue'):
        queue.create_group('Blocked')
    with pytest.raises(RuntimeError, match='already processing'):
        await client.process_queue(queue, save_to=StringIO())
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert drained.is_set() and not waiting.called and len(queue) == 2
    output.seek(0)
    assert len(StandardQueue.from_file(output)) == 2
    queue.create_group('Guard released')
    assert client._request_semaphore._active == 0  # pyright: ignore[reportPrivateUsage]


@pytest.mark.asyncio
@pytest.mark.parametrize('client', ['async'], indirect=True)
async def test_cancellation_preserved_when_save_fails(
    client: DBClient | AsyncDBClient,
    router: Router,
    track: CSLTrack,
    caplog: pytest.LogCaptureFixture,
) -> None:
    assert isinstance(client, AsyncDBClient)
    queue = StandardQueue()
    queue.track_remove_metadata(track, CSLExtraMetadata('active', 1, 'Language', 'Japanese'))
    started = asyncio.Event()

    async def hold(_: niquests.PreparedRequest) -> niquests.Response:
        started.set()
        await asyncio.Event().wait()
        return mock_response(200)

    router.delete(f'/api/track/{track.id}/metadata/active').mock(side_effect=hold)
    task = asyncio.create_task(client.process_queue(queue, save_to=BrokenStream()))
    await asyncio.wait_for(started.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert 'Could not write the recovery queue' in caplog.text
    assert len(queue) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('client', ['async'], indirect=True)
async def test_in_flight_siblings_finish_after_group_failure(
    client: DBClient | AsyncDBClient,
    router: Router,
    track: CSLTrack,
) -> None:
    assert isinstance(client, AsyncDBClient)
    client.max_request_count = 2
    queue = StandardQueue()
    queue.enqueue(
        ParallelBundle(
            [
                TrackDeleteMetadataBundle(track, CSLExtraMetadata(name, 1, 'Language', name))
                for name in ('failure', 'active', 'waiting')
            ]
        )
    )
    active_started = asyncio.Event()
    release_active = asyncio.Event()
    completed = asyncio.Event()

    async def fail(_: niquests.PreparedRequest) -> niquests.Response:
        await active_started.wait()
        return mock_response(500)

    async def succeed(_: niquests.PreparedRequest) -> niquests.Response:
        active_started.set()
        await release_active.wait()
        completed.set()
        return mock_response(200)

    router.delete(f'/api/track/{track.id}/metadata/failure').mock(side_effect=fail)
    router.delete(f'/api/track/{track.id}/metadata/active').mock(side_effect=succeed)
    waiting = router.delete(f'/api/track/{track.id}/metadata/waiting').respond(status_code=200)
    task = asyncio.create_task(client.process_queue(queue, save_to=StringIO()))
    try:
        await asyncio.wait_for(active_started.wait(), timeout=1)
        await asyncio.sleep(0)
    finally:
        release_active.set()
        with pytest.raises(niquests.HTTPError):
            await task
    assert completed.is_set() and not waiting.called
    assert len(queue) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('target', ['list', 'group', 'track'])
async def test_only_changed_caches_are_refreshed(
    client: DBClient | AsyncDBClient,
    router: Router,
    track: CSLTrack,
    target: str,
) -> None:
    queue = StandardQueue()
    _ = client.lists, client.groups
    match target:
        case 'list':
            csl_list = next(iter(client.lists.values()))
            queue.list_edit(csl_list, name='Renamed')
            router.put(f'/api/list/{csl_list.id}').respond(status_code=200)
            router['lists'].respond(json=[])
        case 'group':
            group = next(iter(client.groups.values()))
            queue.group_edit(group, 'Renamed')
            router.put(f'/api/group/{group.id}').respond(status_code=200)
            router['groups'].respond(json=[])
        case 'track':
            queue.track_edit(track, name='Renamed')
            router.put(f'/api/track/{track.id}').respond(status_code=200)
        case _:
            pytest.fail(f'Unknown target: {target}')
    assert queue.changes_lists() is (target == 'list')
    assert queue.changes_groups() is (target == 'group')
    await finish(client.process_queue(queue, save_to=StringIO()))
    if target == 'list':
        assert client.lists == {}
    if target == 'group':
        assert client.groups == {}
    assert router['lists'].call_count == (2 if target == 'list' else 1)
    assert router['groups'].call_count == (2 if target == 'group' else 1)


def test_loading_audio_checks_original_file(track: CSLTrack, tmp_path: Path) -> None:
    from amqcsl.exceptions import QueryError

    path = tmp_path / 'audio.flac'
    path.write_bytes(b'audio')
    queue = StandardQueue()
    queue.add_audio(track, path)
    data = queue.to_json()
    path.unlink()
    with pytest.raises(QueryError, match='does not exist'):
        StandardQueue.from_json(data)


@pytest.mark.parametrize('bad_groups', [None, {}, 'text', [None], [{}]])
def test_invalid_groups_are_rejected(bad_groups: object) -> None:
    data = StandardQueue().to_json()
    data['groups'] = bad_groups
    with pytest.raises((TypeError, ValueError), match='groups'):
        StandardQueue.from_json(data)


def test_builtin_constructor_fields_are_checked() -> None:
    queue = StandardQueue()
    queue.create_group('Group')
    data = queue.to_json()
    data['groups'][0][0]['data']['unexpected'] = 'field'
    with pytest.raises(ValueError, match='constructor fields'):
        StandardQueue.from_json(data)


def test_rich_queue_mixin_requires_display() -> None:
    from amqcsl.queues._core import RichQueueMixin

    with pytest.raises(TypeError, match='abstract.*display'):
        cast(Any, RichQueueMixin)()


@pytest.mark.asyncio
@pytest.mark.parametrize('client', ['async'], indirect=True)
@pytest.mark.parametrize('execution_fails', [False, True])
async def test_cache_refresh_errors_preserve_recovery_and_execution_error(
    client: DBClient | AsyncDBClient,
    router: Router,
    execution_fails: bool,
) -> None:
    assert isinstance(client, AsyncDBClient)
    queue = StandardQueue()
    group = next(iter(client.groups.values()))
    queue.group_edit(group, 'Renamed')
    router.put(f'/api/group/{group.id}').respond(status_code=500 if execution_fails else 200)
    router['groups'].respond(status_code=503)
    output = StringIO()
    if execution_fails:
        with pytest.raises(ExceptionGroup) as caught:
            await client.process_queue(queue, save_to=output)
        assert len(caught.value.exceptions) == 2
        assert all(isinstance(error, niquests.HTTPError) for error in caught.value.exceptions)
    else:
        with pytest.raises(niquests.HTTPError):
            await client.process_queue(queue, save_to=output)
    assert client.groups == {}
    output.seek(0)
    assert len(StandardQueue.from_file(output)) == int(execution_fails)
    queue.create_group('Processing guard released')


@pytest.mark.asyncio
@pytest.mark.parametrize('client', ['async'], indirect=True)
async def test_cancellation_during_cache_refresh_still_saves(
    client: DBClient | AsyncDBClient,
    router: Router,
) -> None:
    assert isinstance(client, AsyncDBClient)
    queue = StandardQueue()
    group = next(iter(client.groups.values()))
    queue.group_edit(group, 'Renamed')
    router.put(f'/api/group/{group.id}').respond(status_code=200)
    refreshing = asyncio.Event()

    async def hold(_: niquests.PreparedRequest) -> niquests.Response:
        refreshing.set()
        await asyncio.Event().wait()
        return mock_response(200, json=[])

    router['groups'].mock(side_effect=hold)
    output = StringIO()
    task = asyncio.create_task(client.process_queue(queue, save_to=output))
    await asyncio.wait_for(refreshing.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not queue and client.groups == {}
    output.seek(0)
    assert not StandardQueue.from_file(output)
    queue.create_group('Processing guard released')


def test_custom_stream_is_rejected() -> None:
    from amqcsl.bundles import Items, StreamingBundle, StreamingVendor

    class CustomStream(StreamingBundle[int]):
        def vendor(self, client: httpClient) -> StreamingVendor[int]:
            yield Items([1])

        def __rich_repr__(self) -> rich.repr.Result:
            yield 'kind', 'custom stream'

    queue = StandardQueue()
    with pytest.raises(TypeError, match='Streaming bundles cannot be queued'):
        queue.enqueue(cast(Bundle[Any], CustomStream()))
    assert not queue
