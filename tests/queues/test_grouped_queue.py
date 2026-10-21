import asyncio
from collections.abc import Iterator
from io import StringIO
from typing import Any, cast

import niquests
import pytest
import rich.repr
from attrs import evolve, frozen
from helpers import finish, mock_response
from niquests_mock import MockRouter as Router

from amqcsl import AsyncDBClient, DBClient
from amqcsl.bundles import Bundle, ParallelBundle, SingleVendor, action_bundle_compose, httpClient
from amqcsl.bundles._misc import (
    CreateAlbumBundle,
    CreateGroupBundle,
    CreateListBundle,
    GroupDeleteBundle,
    GroupEditBundle,
    ListDeleteBundle,
    ListEditBundle,
    TrackDeleteMetadataBundle,
    TrackEditBundle,
)
from amqcsl.bundles._pages import IterTracksBundle
from amqcsl.objects import CSLExtraMetadata, CSLGroup, CSLList, CSLSong, CSLTrack, ExtraMetadata, bundle_to_json
from amqcsl.queues import GroupedQueue, StandardQueue
from amqcsl.queues.grouped._core import PreparedTask


@frozen
class UnknownBundle(Bundle[str]):
    name: str

    def vendor(self, client: httpClient) -> SingleVendor[str]:
        yield from ()
        return self.name

    def __rich_repr__(self) -> rich.repr.Result:
        yield 'name', self.name


def test_groups_targets_in_first_appearance_order(track: CSLTrack) -> None:
    song = CSLSong(track.id, track.name or '', None, '2026-01-01', [], [])
    queue = GroupedQueue()
    queue.song_edit(song, 'New name', None)
    queue.track_edit(track, name='First edit')
    queue.track_edit(evolve(track, name='Another snapshot'), original_name='Second edit')
    queue.song_add_metadata(evolve(song, name='Another snapshot'), ExtraMetadata(False, 'Language', 'Japanese'))
    assert [len(group.bundles) for group in queue] == [2, 1]
    assert [group['kind'] for group in queue.to_json()['groups']] == [
        'songs',
        'tracks',
    ]
    assert len(queue) == 3


def test_recursive_flatten_regroups_targets_and_preserves_child_order(track: CSLTrack) -> None:
    other = evolve(track, id='other')
    first = TrackEditBundle(track, name='First')
    second = TrackEditBundle(other, name='Second')
    third = TrackEditBundle(track, original_name='Third')
    queue = GroupedQueue()
    queue.enqueue(ParallelBundle([first, action_bundle_compose([second, third])]))
    merged = evolve(first, original_name='Third')
    assert [group.bundles for group in queue] == [(merged,), (second,)]
    snapshot = [*queue]
    queue.complete(snapshot[0].bundles[0])
    assert snapshot[0].bundles == (merged,)
    assert [group.bundles for group in queue] == [(second,)]
    queue.enqueue(TrackEditBundle(track, name='Reappeared'))
    assert [cast(TrackEditBundle, group.bundles[0]).track.id for group in queue] == [other.id, track.id]
    with pytest.raises(ValueError, match='not pending'):
        queue.complete(first)


@pytest.mark.parametrize(
    'operation',
    [
        'create_list',
        'list_edit',
        'list_delete',
        'create_group',
        'group_edit',
        'group_delete',
        'create_album',
        'unknown',
    ],
)
@pytest.mark.parametrize('parallel', [False, True])
def test_unsupported_edits_are_rejected_atomically(
    operation: str,
    parallel: bool,
    track: CSLTrack,
) -> None:
    unsupported: Bundle[Any]
    match operation:
        case 'create_list':
            unsupported = CreateListBundle('List')
        case 'list_edit':
            unsupported = ListEditBundle(CSLList('list', 'List', 0), name='New')
        case 'list_delete':
            unsupported = ListDeleteBundle(CSLList('list', 'List', 0))
        case 'create_group':
            unsupported = CreateGroupBundle('Group')
        case 'group_edit':
            unsupported = GroupEditBundle(CSLGroup('group', 'Group'), 'New')
        case 'group_delete':
            unsupported = GroupDeleteBundle(CSLGroup('group', 'Group'))
        case 'create_album':
            unsupported = CreateAlbumBundle('Album', 'Original', 2026, [], [])
        case _:
            unsupported = UnknownBundle('Unknown')
    encoded = bundle_to_json(unsupported)
    queue = GroupedQueue()
    queue.track_edit(track, name='Existing')
    before = queue.to_json()
    if parallel:
        unsupported = ParallelBundle(
            [TrackEditBundle(track, original_name='Never added'), ParallelBundle([unsupported])]
        )
    with pytest.raises(TypeError, match='GroupedQueue does not support'):
        queue.enqueue(unsupported)
    assert queue.to_json() == before
    if operation != 'unknown':
        data = before
        data['groups'][0]['bundles'].append(encoded)
        with pytest.raises(TypeError, match='GroupedQueue does not support'):
            GroupedQueue.from_json(data)


def test_prepared_task_accumulates_bundles_by_target(track: CSLTrack) -> None:
    first = TrackEditBundle(track, name='First')
    second = TrackEditBundle(track, original_name='Second')
    other = evolve(track, id='other')
    third = TrackEditBundle(other, name='Third')
    prepared = PreparedTask()
    prepared.add(first)
    prepared.add(ParallelBundle([third, second]))
    assert prepared.order == [('tracks', track.id), ('tracks', other.id)]
    assert prepared.groups['tracks', track.id].target is track
    assert prepared.groups['tracks', track.id].bundles == [evolve(first, original_name='Second')]
    assert prepared.groups['tracks', other.id].bundles == [third]


def test_enqueue_batch_preserves_order_and_rejects_unsupported_edits_atomically(track: CSLTrack) -> None:
    queue = GroupedQueue()
    queue.track_edit(track, name='Existing')
    before = queue.to_json()
    with pytest.raises(TypeError, match='GroupedQueue does not support'):
        queue.enqueue_batch([TrackEditBundle(track, original_name='Never added'), CreateListBundle('List')])
    assert queue.to_json() == before
    first = TrackEditBundle(track, original_name='First')
    second = TrackEditBundle(track, original_artist='Second')
    other = TrackEditBundle(evolve(track, id='other'), name='Other')
    queue.enqueue_batch([first, ParallelBundle([other, second])])
    groups = [*queue]
    assert groups[0].bundles == (
        TrackEditBundle(track, name='Existing', original_name='First', original_artist='Second'),
    )
    assert groups[1].bundles == (other,)
    assert len(queue) == 2


def test_enqueue_batch_iteration_failure_does_not_modify_queue(track: CSLTrack) -> None:
    queue = GroupedQueue()
    queue.track_edit(track, name='Existing')
    before = queue.to_json()

    def bundles() -> Iterator[Bundle[Any]]:
        yield TrackEditBundle(evolve(track, id='other'), name='Never added')
        yield TrackEditBundle(track, original_name='Never added')
        raise RuntimeError('Iterator failed')

    with pytest.raises(RuntimeError, match='Iterator failed'):
        queue.enqueue_batch(bundles())
    assert queue.to_json() == before


def test_stream_rejection_is_atomic(track: CSLTrack) -> None:
    queue = GroupedQueue()
    stream = cast(Bundle[Any], IterTracksBundle.from_client(DBClient(), ''))
    with pytest.raises(TypeError, match='Streaming'):
        queue.enqueue(ParallelBundle([TrackEditBundle(track), stream]))
    assert not queue
    queue.enqueue(ParallelBundle[Any]([]))
    assert not queue
    queue.track_edit(track)
    assert len(queue) == 1


@pytest.mark.parametrize('ratio', [0, -1, float('inf'), float('nan')])
def test_invalid_worker_ratio(ratio: float) -> None:
    with pytest.raises(ValueError, match='finite and positive'):
        GroupedQueue(worker_ratio=ratio)


def test_worker_counts_follow_pending_group_sizes(track: CSLTrack) -> None:
    queue = GroupedQueue()
    assert queue.num_workers(10) == 0
    with pytest.raises(ValueError, match='positive'):
        queue.num_workers(0)
    for idx in range(4):
        for edit in range(3):
            queue.track_remove_metadata(
                evolve(track, id=str(idx)), CSLExtraMetadata(str(edit), 1, 'Language', str(edit))
            )
    assert queue.num_workers(10) == 3
    assert queue.num_workers(1) == 1
    for bundle in next(iter(queue)).bundles:
        queue.complete(bundle)
    queue.complete(next(iter(queue)).bundles[0])
    assert queue.num_workers(5) == 1
    queue.worker_ratio = 2
    assert queue.num_workers(5) == 2
    queue.worker_ratio = 100
    assert queue.num_workers(5) == 1
    queue.worker_ratio = 0.5
    assert queue.num_workers(5) == 3
    queue.worker_ratio = 1e-320
    assert queue.num_workers(5) == 3
    with pytest.raises(ValueError, match='positive'):
        queue.worker_ratio = -1


def test_round_trip_preserves_remaining_group_order_and_ratio(track: CSLTrack) -> None:
    queue = GroupedQueue(worker_ratio=2.5)
    queue.track_edit(track, name='Done')
    queue.track_edit(evolve(track, id='other'), name='Other')
    queue.track_remove_metadata(track, CSLExtraMetadata('remaining', 1, 'Language', 'Remaining'))
    queue.complete(next(iter(queue)).bundles[0])
    stream = StringIO()
    queue.to_file(stream)
    stream.seek(0)
    restored = GroupedQueue.from_file(stream)
    assert restored.to_json() == queue.to_json()
    assert restored.worker_ratio == 2.5
    assert [group.bundles for group in restored] == [group.bundles for group in queue]
    restored.complete(next(iter(restored)).bundles[0])
    restored.track_edit(track, name='Reappeared')
    assert [cast(TrackEditBundle, group.bundles[0]).track.id for group in restored] == ['other', track.id]
    with pytest.raises(ValueError, match='queue type'):
        StandardQueue.from_json(queue.to_json())
    with pytest.raises(ValueError, match='queue type'):
        GroupedQueue.from_json(StandardQueue().to_json())


@pytest.mark.parametrize('mutation', ['kind', 'split', 'mixed', 'interleaved', 'empty', 'version', 'ratio'])
def test_loading_rejects_invalid_groups(mutation: str, track: CSLTrack) -> None:
    queue = GroupedQueue()
    queue.track_remove_metadata(track, CSLExtraMetadata('one', 1, 'Language', 'One'))
    queue.track_remove_metadata(track, CSLExtraMetadata('two', 1, 'Language', 'Two'))
    data = queue.to_json()
    if mutation == 'kind':
        data['groups'][0]['kind'] = 'songs'
    elif mutation == 'split':
        second = data['groups'][0]['bundles'].pop()
        data['groups'].append({'kind': 'tracks', 'bundles': [second]})
    elif mutation == 'mixed':
        other = GroupedQueue()
        other.track_edit(evolve(track, id='other'))
        data['groups'][0]['bundles'].extend(other.to_json()['groups'][0]['bundles'])
    elif mutation == 'interleaved':
        other = GroupedQueue()
        other.track_remove_metadata(evolve(track, id='other'), CSLExtraMetadata('one', 1, 'Language', 'One'))
        other.track_remove_metadata(evolve(track, id='other'), CSLExtraMetadata('two', 1, 'Language', 'Two'))
        first, second = data['groups'][0]['bundles']
        other_first, other_second = other.to_json()['groups'][0]['bundles']
        data['groups'] = [
            {'kind': 'tracks', 'bundles': [first, other_first]},
            {'kind': 'tracks', 'bundles': [second, other_second]},
        ]
    elif mutation == 'empty':
        data['groups'][0]['bundles'] = []
    elif mutation == 'version':
        data['version'] = '1.0'
    else:
        data['worker_ratio'] = 0
    with pytest.raises(ValueError):
        GroupedQueue.from_json(data)


@pytest.mark.asyncio
@pytest.mark.parametrize('stop_if_err', [False, True])
async def test_failure_keeps_group_edits_and_retry_preserves_order(
    client: DBClient | AsyncDBClient,
    router: Router,
    track: CSLTrack,
    stop_if_err: bool,
) -> None:
    if isinstance(client, AsyncDBClient):
        client.max_request_count = 1
    queue = GroupedQueue(worker_ratio=100)
    other = evolve(track, id='other')
    bundles = [
        TrackDeleteMetadataBundle(track, CSLExtraMetadata(name, 1, 'Language', name))
        for name in (
            'done',
            'fail',
            'skipped',
        )
    ]
    queue.enqueue(bundles[0])
    queue.track_remove_metadata(other, CSLExtraMetadata('next', 1, 'Language', 'Next'))
    queue.enqueue(ParallelBundle(bundles[1:]))
    done = router.delete(f'/api/track/{track.id}/metadata/done').respond(status_code=200)
    failed = router.delete(f'/api/track/{track.id}/metadata/fail').respond(status_code=500)
    skipped = router.delete(f'/api/track/{track.id}/metadata/skipped').respond(status_code=200)
    next_group = router.delete('/api/track/other/metadata/next').respond(status_code=200)
    output = StringIO()
    if stop_if_err:
        with pytest.raises(niquests.HTTPError):
            await finish(client.process_queue(queue, save_to=output))
    else:
        await finish(client.process_queue(queue, save_to=output, stop_if_err=False))
    assert done.call_count == 1 and not skipped.called
    assert next_group.called is not stop_if_err
    assert next(iter(queue)).bundles == (*bundles[1:],)
    output.seek(0)
    restored = GroupedQueue.from_file(output)
    failed.respond(status_code=200)
    await finish(client.process_queue(restored, save_to=StringIO()))
    assert not restored and done.call_count == 1 and skipped.call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('client', ['async'], indirect=True)
async def test_async_workers_process_groups_concurrently_and_recover_cancellation(
    client: DBClient | AsyncDBClient,
    router: Router,
    track: CSLTrack,
) -> None:
    assert isinstance(client, AsyncDBClient)
    client.max_request_count = 2
    queue = GroupedQueue(worker_ratio=1)
    started: list[str] = []
    both_started = asyncio.Event()
    drained: list[str] = []

    async def hold(request: niquests.PreparedRequest) -> niquests.Response:
        assert request.url is not None
        started.append(request.url)
        if len(started) == 2:
            both_started.set()
        try:
            await asyncio.Event().wait()
            return mock_response(200)
        finally:
            drained.append(request.url)

    for idx in range(2):
        target = evolve(track, id=str(idx))
        queue.track_remove_metadata(target, CSLExtraMetadata('active', 1, 'Language', 'Active'))
        router.delete(f'/api/track/{idx}/metadata/active').mock(side_effect=hold)
    output = StringIO()
    task = asyncio.create_task(client.process_queue(queue, save_to=output))
    await asyncio.wait_for(both_started.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(drained) == 2 and len(queue) == 2
    output.seek(0)
    assert GroupedQueue.from_file(output).to_json() == queue.to_json()
