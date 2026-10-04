import asyncio
import json
import re
import threading
from collections.abc import AsyncIterator, Awaitable, Mapping, Sequence
from typing import cast
from urllib.parse import urlsplit

import pytest
from attrs import evolve
from helpers import load, mock_response, query_params, request_body, request_url
from niquests import HTTPError, Response
from niquests import PreparedRequest as Request
from niquests_mock import Call
from niquests_mock import MockRoute as Route
from niquests_mock import MockRouter as Router

from amqcsl import AsyncDBClient, DBClient
from amqcsl.clients.bundles._core import Bundle
from amqcsl.clients.bundles._parallel import (
    _ParallelActionsBundle,  # pyright: ignore[reportPrivateUsage] -- inspect queued actions
)
from amqcsl.exceptions import AMQCSLError, QueryError
from amqcsl.objects import (
    CSLArtist,
    CSLArtistSample,
    CSLTrack,
    ExtraMetadata,
    from_json,
)
from amqcsl.objects._json_types import JSONArtist, JSONArtistSample, JSONTrack, JSONType
from amqcsl.workflows import character as cm

pytestmark = pytest.mark.asyncio


def artist(
    name: str,
    *,
    group: bool = False,
    disambiguation: str | None = None,
) -> dict[str, JSONType]:
    return {
        'id': name + (disambiguation or ''),
        'name': name,
        'originalName': name,
        'disambiguation': disambiguation,
        'type': 3 if group else 1,
    }


def group_details(
    sample: dict[str, JSONType],
    members: Sequence[dict[str, JSONType]],
    other: Sequence[dict[str, JSONType]] = (),
) -> dict[str, JSONType]:
    relations: list[JSONType] = [
        {'id': f'member-{idx}', 'type': 1, 'artist': member} for idx, member in enumerate(members)
    ]
    relations.extend({'id': f'other-{idx}', 'type': 2, 'artist': member} for idx, member in enumerate(other))
    return {
        **sample,
        'forwardRelations': relations,
        'reverseRelations': [],
        'linkedAMQSongs': [],
        'linkedTracks': [],
    }


def track(*samples: dict[str, JSONType], track_id: str = 'test-track') -> CSLTrack:
    data: dict[str, JSONType] = cast(list[dict[str, JSONType]], load('superstar/aspire'))[0]
    credits: list[JSONType] = [
        {'name': sample['name'], 'joinPhrase': '', 'position': idx, 'artist': sample}
        for idx, sample in enumerate(samples)
    ]
    data = {
        **data,
        'id': track_id,
        'artistCredits': credits,
    }
    return from_json(cast(JSONTrack, data), CSLTrack)


def mock_search(router: Router, samples: Sequence[dict[str, JSONType]]) -> Route:
    def search(req: Request) -> Response:
        phrase = query_params(req)['searchTerm']
        found = [a for a in samples if phrase == 'all' or phrase == a['name']]
        skip, take = int(query_params(req)['skip']), int(query_params(req)['take'])
        return mock_response(200, json={'count': len(found), 'artists': found[skip : skip + take]})

    return router.get(path='/api/artists').mock(side_effect=search)


def mock_metadata(router: Router, extra: Sequence[dict[str, JSONType]] = ()) -> Route:
    if extra:
        response = mock_response(
            200,
            json={
                'override': False,
                'artistCredits': [],
                'extraMetas': [*extra],
                'totalCount': len(extra),
                'fields': [],
            },
        )
    else:
        response = mock_response(
            404, json={'statusCode': 404, 'errors': {'generalErrors': ['Song does not have metadata']}}
        )
    return router.get(url=re.compile(r'/api/track/[^/]+/metadata')).mock(return_value=response)


async def enqueue_edit(
    client: DBClient | AsyncDBClient,
    result: Bundle[None] | None | Awaitable[Bundle[None] | None],
) -> None:
    """Explicitly enqueue prepared edits in tests that exercise committing them."""
    bundle = await finish(result)
    if bundle is not None:
        client.enqueue(bundle)


async def finish[T](result: T | Awaitable[T]) -> T:
    return await cast(Awaitable[T], result) if isinstance(result, Awaitable) else cast(T, result)


async def test_infer_group_and_cache(
    client: DBClient | AsyncDBClient,
    router: Router,
) -> None:
    a, b, g = artist('Alice'), artist('Bob'), artist('Group', group=True)
    irrelevant = artist('Irrelevant')
    search = mock_search(router, [a, b, g])
    get_group = router.get(path='/api/artist/Group').mock(
        return_value=mock_response(200, json=group_details(g, [a, b, a], [irrelevant]))
    )
    mock_metadata(router)
    add = router.post(url=re.compile(r'/api/track/[^/]+/metadata')).mock(return_value=mock_response(200))
    mapping = await finish(cm.make_artist_to_meta(client, {'Alice': 'A', 'Bob': 'B'}, ['all']))
    assert len(mapping.metadata) == 2  # Discovered groups are not inferred until credited.
    assert search.call_count == 1
    for t in [track(g, a), track(g, track_id='second')]:
        await finish(enqueue_edit(client, mapping.apply(t, lambda _track, _reasons: pytest.fail('Unexpected failure'))))
    assert get_group.call_count == 1
    assert search.call_count == 1
    assert mapping.metadata[from_json(cast(JSONArtistSample, g), CSLArtistSample)] == [
        *mapping.metadata[from_json(cast(JSONArtistSample, a), CSLArtistSample)],
        *mapping.metadata[from_json(cast(JSONArtistSample, b), CSLArtistSample)],
    ]
    await finish(client.commit())
    assert add.call_count == 2
    for call in cast(Sequence[Call], add.calls):
        assert {meta['value'] for meta in json.loads(request_body(call.request))['extraMetadatas']} == {'A', 'B'}


async def test_explicit_group_overrides_members(
    client: DBClient | AsyncDBClient,
    router: Router,
) -> None:
    g = artist('Group', group=True)
    mock_search(router, [g])
    get_group = router.get(path='/api/artist/Group').mock(return_value=mock_response(500))
    mock_metadata(router)
    mapping = await finish(cm.make_artist_to_meta(client, {'Group': 'Override'}, ['all']))
    await finish(enqueue_edit(client, mapping.apply(track(g))))
    assert not get_group.called
    assert len(client.queue) == 1


async def test_callback_collates_failures_and_caches_exclusions(
    client: DBClient | AsyncDBClient,
    router: Router,
) -> None:
    a, b, c = artist('Alice'), artist('Bob'), artist('Carol')
    g, unknown = artist('Group', group=True), artist('Unknown')
    mock_search(router, [a])
    get_group = router.get(path='/api/artist/Group').mock(
        return_value=mock_response(200, json=group_details(g, [a, b, c]))
    )
    mock_metadata(router)
    calls: list[tuple[CSLTrack, Sequence[cm.Reason]]] = []

    def should_exclude(t: CSLTrack, reasons: Sequence[cm.Reason]) -> cm.ExcludeDecision:
        calls.append((t, reasons))
        return cm.ExcludeDecision.EXCLUDE

    mapping = await finish(cm.make_artist_to_meta(client, {'Alice': 'A'}, ['all']))
    first = track(g, unknown, unknown, a)
    await finish(enqueue_edit(client, mapping.apply(first, should_exclude)))
    await finish(enqueue_edit(client, mapping.apply(track(unknown, g, a, track_id='second'), should_exclude)))
    assert len(calls) == 1
    assert calls[0][0] is first
    reasons = calls[0][1]
    assert [r.artist.id for r in reasons] == ['Group', 'Unknown']
    assert isinstance(reasons[0].artist, CSLArtist)
    assert isinstance(reasons[0].reason, cm.INCOMPLETE_GROUP)
    assert [artist.name for artist in reasons[0].reason.known_artists] == ['Alice']
    assert [*reasons[0].reason.artists] == [
        from_json(cast(JSONArtistSample, b), CSLArtistSample),
        from_json(cast(JSONArtistSample, c), CSLArtistSample),
    ]
    assert reasons[1].reason is cm.UNKNOWN_ARTIST
    assert mapping.excluded_artists == {'Group', 'Unknown'}
    assert get_group.call_count == 1
    assert len(client.queue) == 2


async def test_rejected_exclusion_raises_without_queueing(
    client: DBClient | AsyncDBClient,
    router: Router,
) -> None:
    unknown = artist('Unknown')
    get_meta = mock_metadata(router)
    mapping = await finish(cm.make_artist_to_meta(client, {}))
    for _ in range(2):
        with pytest.raises(AMQCSLError, match='Cannot infer'):
            await finish(
                enqueue_edit(client, mapping.apply(track(unknown), lambda _track, _reasons: cm.ExcludeDecision.ERROR))
            )
    assert not mapping.excluded_artists
    assert not client.queue
    assert not get_meta.called


@pytest.mark.parametrize('members', [[], [artist('Nested', group=True)]])
async def test_empty_groups_resolve_to_empty_metadata(
    client: DBClient | AsyncDBClient,
    router: Router,
    members: list[dict[str, JSONType]],
) -> None:
    g = artist('Group', group=True)
    get_group = router.get(path='/api/artist/Group').mock(
        return_value=mock_response(200, json=group_details(g, members))
    )
    nested = router.get(path='/api/artist/Nested').mock(
        return_value=mock_response(200, json=group_details(artist('Nested', group=True), []))
    )
    mock_metadata(router)
    mapping = await finish(cm.make_artist_to_meta(client, {}))
    result = await finish(mapping.apply(track(g), lambda _t, _r: pytest.fail('Empty group prompted')))
    assert result is None
    assert not mapping.metadata[from_json(cast(JSONArtistSample, g), CSLArtistSample)]
    assert not mapping.excluded_artists
    assert get_group.called
    assert nested.called == bool(members)
    assert not client.queue

    await finish(mapping.apply(track(g, track_id='cached'), lambda _t, _r: pytest.fail('Cached group prompted')))
    assert get_group.call_count == 1


async def test_nested_group_with_explicit_metadata(
    client: DBClient | AsyncDBClient,
    router: Router,
) -> None:
    nested, g = artist('Nested', group=True), artist('Group', group=True)
    mock_search(router, [nested])
    _ = router.get(path='/api/artist/Group').mock(return_value=mock_response(200, json=group_details(g, [nested])))
    nested_query = router.get(path='/api/artist/Nested').mock(return_value=mock_response(500))
    mock_metadata(router)
    mapping = await finish(cm.make_artist_to_meta(client, {'Nested': 'Character'}))
    await finish(enqueue_edit(client, mapping.apply(track(g))))
    assert not nested_query.called
    assert (
        mapping.metadata[from_json(cast(JSONArtistSample, g), CSLArtistSample)]
        == mapping.metadata[from_json(cast(JSONArtistSample, nested), CSLArtistSample)]
    )


async def test_recursive_groups_share_members_and_cache_all_levels(
    client: DBClient | AsyncDBClient,
    router: Router,
) -> None:
    a, b = artist('Alice'), artist('Bob')
    root, left, right, shared = [artist(name, group=True) for name in ('Root', 'Left', 'Right', 'Shared')]
    mock_search(router, [a, b])
    queries = [
        router.get(path=f'/api/artist/{sample["id"]}').mock(
            return_value=mock_response(200, json=group_details(sample, members))
        )
        for sample, members in [(root, [left, right]), (left, [a, shared]), (right, [shared, b]), (shared, [b, a])]
    ]
    mock_metadata(router)
    add = router.post(url=re.compile(r'/api/track/[^/]+/metadata')).mock(return_value=mock_response(200))
    mapping = await finish(cm.make_artist_to_meta(client, {'Alice': 'A', 'Bob': 'B'}))
    for idx, sample in enumerate([root, shared, left, right, root]):
        await finish(
            enqueue_edit(
                client,
                mapping.apply(track(sample, track_id=str(idx)), lambda _t, _r: pytest.fail('Unexpected failure')),
            )
        )
    assert all(query.call_count == 1 for query in queries)
    assert len(mapping.metadata) == 6
    for sample in [root, left, right, shared]:
        values = mapping.metadata[from_json(cast(JSONArtistSample, sample), CSLArtistSample)]
        assert len(values) == 2
        assert {meta.value for meta in values} == {'A', 'B'}
    await finish(client.commit())
    assert add.call_count == 5
    for call in cast(Sequence[Call], add.calls):
        assert {meta['value'] for meta in json.loads(request_body(call.request))['extraMetadatas']} == {'A', 'B'}


async def test_recursive_missing_members_are_collated_for_the_credited_group(
    client: DBClient | AsyncDBClient,
    router: Router,
) -> None:
    a, missing, other = artist('Alice'), artist('Missing'), artist('Other')
    root, left, right = [artist(name, group=True) for name in ('Root', 'Left', 'Right')]
    mock_search(router, [a])
    for sample, members in [(root, [left, right]), (left, [a, missing]), (right, [missing, other])]:
        _ = router.get(path=f'/api/artist/{sample["id"]}').mock(
            return_value=mock_response(200, json=group_details(sample, members))
        )
    mock_metadata(router)
    add = router.post('/api/track/test-track/metadata').mock(return_value=mock_response(200))
    mapping = await finish(cm.make_artist_to_meta(client, {'Alice': 'A'}))
    failures: list[cm.Reason] = []

    def exclude(_: CSLTrack, reasons: Sequence[cm.Reason]) -> cm.ExcludeDecision:
        failures.extend(reasons)
        return cm.ExcludeDecision.EXCLUDE

    await finish(enqueue_edit(client, mapping.apply(track(root, a), exclude)))
    assert len(failures) == 1
    assert failures[0].artist.id == 'Root'
    assert isinstance(failures[0].reason, cm.INCOMPLETE_GROUP)
    assert [member.id for member in failures[0].reason.artists] == ['Missing', 'Other']
    assert [member.name for member in failures[0].reason.known_artists] == ['Alice']
    assert len(mapping.metadata) == 1
    assert mapping.excluded_artists == {'Root'}
    await finish(client.commit())
    assert json.loads(request_body(add.calls[0].request))['extraMetadatas'] == [
        {'isArtist': True, 'type': 'Character', 'value': 'A'},
    ]


@pytest.mark.parametrize('mutual', [False, True], ids=['self_cycle', 'mutual_cycle'])
async def test_recursive_group_cycles_are_incomplete(
    client: DBClient | AsyncDBClient,
    router: Router,
    mutual: bool,
) -> None:
    root, nested, a = artist('Root', group=True), artist('Nested', group=True), artist('Alice')
    mock_search(router, [a])
    root_query = router.get(path='/api/artist/Root').mock(
        return_value=mock_response(200, json=group_details(root, [nested if mutual else root, a]))
    )
    nested_query = router.get(path='/api/artist/Nested').mock(
        return_value=mock_response(200, json=group_details(nested, [root]))
    )
    metadata = mock_metadata(router)
    mapping = await finish(cm.make_artist_to_meta(client, {'Alice': 'A'}))
    failures: list[cm.Reason] = []

    def ignore(_: CSLTrack, reasons: Sequence[cm.Reason]) -> cm.ExcludeDecision:
        failures.extend(reasons)
        return cm.ExcludeDecision.IGNORE

    await finish(enqueue_edit(client, mapping.apply(track(root), ignore)))
    assert root_query.call_count == 1
    assert nested_query.call_count == int(mutual)
    assert len(failures) == 1 and failures[0].artist.id == 'Root'
    assert isinstance(failures[0].reason, cm.INCOMPLETE_GROUP)
    assert [member.id for member in failures[0].reason.artists] == ['Root']
    assert len(mapping.metadata) == 1
    assert not metadata.called and not client.queue and not mapping.excluded_artists


@pytest.mark.parametrize('exclude', [False, True], ids=['explicit_metadata', 'excluded_member'])
async def test_nested_explicit_metadata_or_exclusion_stops_cycle_traversal(
    client: DBClient | AsyncDBClient,
    router: Router,
    exclude: bool,
) -> None:
    root, nested, a = artist('Root', group=True), artist('Nested', group=True), artist('Alice')
    mock_search(router, [nested, a])
    _ = router.get(path='/api/artist/Root').mock(return_value=mock_response(200, json=group_details(root, [nested, a])))
    nested_query = router.get(path='/api/artist/Nested').mock(
        return_value=mock_response(200, json=group_details(nested, [root]))
    )
    mock_metadata(router)
    mapping = await finish(
        cm.make_artist_to_meta(
            client,
            {'Alice': 'A'} if exclude else {'Alice': 'A', 'Nested': 'Override'},
            exclude=['Nested'] if exclude else [],
        )
    )
    await finish(enqueue_edit(client, mapping.apply(track(root), lambda _t, _r: pytest.fail('Unexpected failure'))))
    assert not nested_query.called
    assert {meta.value for meta in mapping.metadata[from_json(cast(JSONArtistSample, root), CSLArtistSample)]} == (
        {'A'} if exclude else {'A', 'Override'}
    )


@pytest.mark.parametrize('client', ['async'], indirect=True)
async def test_nested_group_requests_run_in_parallel(
    client: DBClient | AsyncDBClient,
    router: Router,
) -> None:
    assert isinstance(client, AsyncDBClient)
    root, left, right = [artist(name, group=True) for name in ('Root', 'Left', 'Right')]
    a = artist('Alice')
    mock_search(router, [a])
    _ = router.get(path='/api/artist/Root').mock(
        return_value=mock_response(200, json=group_details(root, [left, right]))
    )
    arrived: set[str] = set()
    both_started = asyncio.Event()

    async def get_nested(req: Request) -> Response:
        group_id = urlsplit(request_url(req)).path.rsplit('/', 1)[-1]
        arrived.add(group_id)
        if len(arrived) == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), timeout=2)
        sample = left if group_id == 'Left' else right
        return mock_response(200, json=group_details(sample, [a]))

    router.get(url=re.compile(r'/api/artist/(Left|Right)')).mock(side_effect=get_nested)
    mock_metadata(router)
    mapping = await cm.make_artist_to_meta(client, {'Alice': 'A'})
    await enqueue_edit(client, mapping.apply(track(root)))
    assert arrived == {'Left', 'Right'}
    assert len(mapping.metadata) == 4 and len(client.queue) == 1


async def test_nested_group_query_failure_leaves_mapping_and_queue_unchanged(
    client: DBClient | AsyncDBClient,
    router: Router,
) -> None:
    root, nested, a = artist('Root', group=True), artist('Nested', group=True), artist('Alice')
    mock_search(router, [a])
    _ = router.get(path='/api/artist/Root').mock(return_value=mock_response(200, json=group_details(root, [a, nested])))
    nested_query = router.get(path='/api/artist/Nested').mock(return_value=mock_response(500))
    metadata = mock_metadata(router)
    mapping = await finish(cm.make_artist_to_meta(client, {'Alice': 'A'}))
    with pytest.raises(HTTPError):
        await finish(
            enqueue_edit(
                client, mapping.apply(track(root), lambda _t, _r: pytest.fail('Query failure called the callback'))
            )
        )
    assert nested_query.called and not metadata.called
    assert len(mapping.metadata) == 1 and not mapping.excluded_artists and not client.queue


async def test_exclusion_keeps_additions_and_all_stale_deletions(
    client: DBClient | AsyncDBClient,
    router: Router,
) -> None:
    a, unknown = artist('Alice'), artist('Unknown')
    mock_search(router, [a])
    extra: list[dict[str, JSONType]] = [
        {'id': 'stale1', 'type': 2, 'key': 'Character', 'value': 'Old'},
        {'id': 'stale2', 'type': 1, 'key': 'Character', 'value': 'Other'},
        {'id': 'unrelated', 'type': 1, 'key': 'Language', 'value': 'Japanese'},
    ]
    mock_metadata(router, extra)
    add = router.post('/api/track/test-track/metadata').mock(return_value=mock_response(200))
    delete1 = router.delete('/api/track/test-track/metadata/stale1').mock(return_value=mock_response(200))
    delete2 = router.delete('/api/track/test-track/metadata/stale2').mock(return_value=mock_response(200))
    unrelated = router.delete('/api/track/test-track/metadata/unrelated').mock(return_value=mock_response(500))
    mapping = await finish(cm.make_artist_to_meta(client, {'Alice': 'New'}))
    await finish(
        enqueue_edit(client, mapping.apply(track(a, unknown), lambda _track, _reasons: cm.ExcludeDecision.EXCLUDE))
    )
    assert len(client.queue) == 1
    assert isinstance(client.queue[0], _ParallelActionsBundle)
    assert len(client.queue[0].bundles) == 3
    assert not add.called and not delete1.called  # Only queued until commit.
    await finish(client.commit())
    assert add.called and delete1.called and delete2.called
    assert not unrelated.called


async def test_only_deletions_and_no_changes(
    client: DBClient | AsyncDBClient,
    router: Router,
) -> None:
    a, unknown = artist('Alice'), artist('Unknown')
    mock_search(router, [a])
    mock_metadata(router, [{'id': 'stale', 'type': 2, 'key': 'Character', 'value': 'Old'}])
    delete = router.delete('/api/track/test-track/metadata/stale').mock(return_value=mock_response(200))
    mapping = await finish(cm.make_artist_to_meta(client, {'Alice': 'Old'}))
    await finish(enqueue_edit(client, mapping.apply(track(a))))
    assert not client.queue
    await finish(
        enqueue_edit(client, mapping.apply(track(unknown), lambda _track, _reasons: cm.ExcludeDecision.EXCLUDE))
    )
    assert isinstance(client.queue[0], _ParallelActionsBundle)
    assert len(client.queue[0].bundles) == 1
    await finish(client.commit())
    assert delete.called


@pytest.mark.parametrize('type_id', [1, 2], ids=['off_vocal', 'instrumental'])
async def test_non_vocal_tracks_skip_queries_and_callback(
    client: DBClient | AsyncDBClient,
    router: Router,
    type_id: int,
) -> None:
    mapping = await finish(cm.make_artist_to_meta(client, {}))
    get_meta = mock_metadata(router)
    get_group = router.get(path='/api/artist/Group').mock(return_value=mock_response(500))
    t = evolve(track(artist('Unknown'), artist('Group', group=True)), type_id=type_id)
    await finish(
        enqueue_edit(
            client, mapping.apply(t, lambda _track, _reasons: pytest.fail('Skipped track called the callback'))
        )
    )
    assert not get_meta.called and not get_group.called and not client.queue
    assert not mapping.metadata and not mapping.excluded_artists


async def test_disambiguation_and_pagination(
    client: DBClient | AsyncDBClient,
    router: Router,
) -> None:
    a, b = artist('Alice', disambiguation='one'), artist('Alice', disambiguation='two')
    client.max_batch_size = 1
    search = mock_search(router, [a, b, artist('Unlisted', group=True)])
    mapping = await finish(cm.make_artist_to_meta(client, {('Alice', 'two'): 'B'}, ['all']))
    assert len(mapping.metadata) == 1
    assert mapping.metadata[from_json(cast(JSONArtistSample, b), CSLArtistSample)][0].value == 'B'
    assert search.call_count == 3


@pytest.mark.parametrize(
    'definitions, error',
    [
        ({'Missing': 'X'}, 'Could not find'),
        ({'Alice': 'X'}, '2 artists found'),
        ({'Alice': 'X', ('Alice', 'one'): 'Y'}, 'both match'),
    ],
)
async def test_matching_errors(
    client: DBClient | AsyncDBClient,
    router: Router,
    definitions: cm.ArtistDict,
    error: str,
) -> None:
    samples = [artist('Alice', disambiguation='one')]
    if error == '2 artists found':
        samples.append(artist('Alice', disambiguation='two'))
    mock_search(router, samples)
    with pytest.raises(AMQCSLError, match=error):
        await finish(cm.make_artist_to_meta(client, definitions, ['all']))


async def test_query_limit(client: DBClient | AsyncDBClient, router: Router) -> None:
    client.max_query_size = 1
    mock_search(router, [artist('Alice'), artist('Bob')])
    with pytest.raises(QueryError, match='max query size'):
        await finish(cm.make_artist_to_meta(client, {}, ['all']))


@pytest.mark.parametrize('client', ['async'], indirect=True)
async def test_global_searches_run_in_parallel(
    client: DBClient | AsyncDBClient,
    router: Router,
) -> None:
    assert isinstance(client, AsyncDBClient)
    arrived: set[str] = set()
    both_started = asyncio.Event()

    async def search(req: Request) -> Response:
        arrived.add(query_params(req)['searchTerm'])
        if len(arrived) == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), timeout=2)
        return mock_response(200, json={'artists': [], 'count': 0})

    route = router.get(path='/api/artists').mock(side_effect=search)
    mapping = await cm.make_artist_to_meta(client, {}, ['one', 'two', 'one'])
    assert not mapping.metadata
    assert route.call_count == 2


@pytest.mark.parametrize('client', ['async'], indirect=True)
async def test_concurrent_apply_shares_exclusion(
    client: DBClient | AsyncDBClient,
    router: Router,
) -> None:
    assert isinstance(client, AsyncDBClient)
    mapping = await cm.make_artist_to_meta(client, {})
    mock_metadata(router)
    calls: list[str] = []

    def should_exclude(t: CSLTrack, reasons: Sequence[cm.Reason]) -> cm.ExcludeDecision:
        calls.append(t.id)
        return cm.ExcludeDecision.EXCLUDE

    await asyncio.gather(
        *(
            enqueue_edit(client, mapping.apply(track(artist('Unknown'), track_id=str(i)), should_exclude))
            for i in range(3)
        )
    )
    assert len(calls) == 1


async def test_cached_group_can_be_used_as_member_without_refetching(
    client: DBClient | AsyncDBClient,
    router: Router,
) -> None:
    a, nested, parent = artist('Alice'), artist('Nested', group=True), artist('Parent', group=True)
    mock_search(router, [a])
    nested_query = router.get(path='/api/artist/Nested').mock(
        return_value=mock_response(200, json=group_details(nested, [a]))
    )
    parent_query = router.get(path='/api/artist/Parent').mock(
        return_value=mock_response(200, json=group_details(parent, [nested]))
    )
    mock_metadata(router)
    mapping = await finish(cm.make_artist_to_meta(client, {'Alice': 'A'}))
    await finish(enqueue_edit(client, mapping.apply(track(nested))))
    await finish(enqueue_edit(client, mapping.apply(track(parent, track_id='parent-track'))))
    assert nested_query.call_count == 1 and parent_query.call_count == 1
    assert (
        mapping.metadata[from_json(cast(JSONArtistSample, parent), CSLArtistSample)]
        == mapping.metadata[from_json(cast(JSONArtistSample, a), CSLArtistSample)]
    )


async def test_full_artist_credit_uses_existing_group_details(
    client: DBClient | AsyncDBClient,
    router: Router,
) -> None:
    a, g = artist('Alice'), artist('Group', group=True)
    mock_search(router, [a])
    get_group = router.get(path='/api/artist/Group').mock(return_value=mock_response(500))
    mock_metadata(router)
    mapping = await finish(cm.make_artist_to_meta(client, {'Alice': 'A'}))
    t = track(g)
    credit = evolve(t.artist_credits[0], artist=from_json(cast(JSONArtist, group_details(g, [a])), CSLArtist))
    await finish(enqueue_edit(client, mapping.apply(evolve(t, artist_credits=[credit]))))
    assert not get_group.called
    assert from_json(cast(JSONArtistSample, g), CSLArtistSample) in mapping.metadata


@pytest.mark.parametrize('client', ['async'], indirect=True)
async def test_group_queries_for_one_track_run_in_parallel(
    client: DBClient | AsyncDBClient,
    router: Router,
) -> None:
    assert isinstance(client, AsyncDBClient)
    a, g, h = artist('Alice'), artist('Group', group=True), artist('OtherGroup', group=True)
    mock_search(router, [a])
    arrived: set[str] = set()
    both_started = asyncio.Event()

    async def get_group(req: Request) -> Response:
        name = urlsplit(request_url(req)).path.rsplit('/', 1)[-1]
        arrived.add(name)
        if len(arrived) == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), timeout=2)
        return mock_response(200, json=group_details(g if name == 'Group' else h, [a]))

    router.get(url=re.compile(r'/api/artist/[^/]+')).mock(side_effect=get_group)
    mock_metadata(router)
    mapping = await cm.make_artist_to_meta(client, {'Alice': 'A'})
    await enqueue_edit(client, mapping.apply(track(g, h)))
    assert len(mapping.metadata) == 3


async def test_metadata_bundle_handles_multiple_adds_deletes_and_noop(
    client: DBClient | AsyncDBClient,
    router: Router,
) -> None:
    from amqcsl.clients.bundles import TrackAddMetadataBundle, TrackDeleteMetadataBundle, parallel_actions
    from amqcsl.objects import CSLExtraMetadata, ExtraMetadata

    t = track(artist('Alice'))
    add = router.post('/api/track/test-track/metadata').mock(return_value=mock_response(200))
    deletes = [
        router.delete(f'/api/track/test-track/metadata/{i}').mock(return_value=mock_response(200)) for i in range(2)
    ]
    bundles = [
        TrackAddMetadataBundle(t, []),
        TrackAddMetadataBundle(t, [ExtraMetadata(True, 'Character', 'A')]),
        TrackAddMetadataBundle(t, [ExtraMetadata(True, 'Character', 'B')]),
        *[TrackDeleteMetadataBundle(t, CSLExtraMetadata(str(i), 2, 'Character', 'Old')) for i in range(2)],
    ]
    await finish(client.process(parallel_actions(bundles)))
    assert add.call_count == 2
    assert all(route.call_count == 1 for route in deletes)


async def test_mapping_methods_delegate_to_metadata(
    client: DBClient | AsyncDBClient,
    router: Router,
) -> None:
    a = artist('Alice')
    mock_search(router, [a])
    mapping = await finish(cm.make_artist_to_meta(client, {'Alice': 'A'}))
    sample = from_json(cast(JSONArtistSample, a), CSLArtistSample)
    full = from_json(cast(JSONArtist, group_details(a, [])), CSLArtist)
    assert isinstance(mapping, Mapping)
    assert len(mapping) == 1
    assert [*mapping] == [sample]
    assert sample in mapping and full in mapping
    assert 'Alice' not in mapping
    assert mapping[sample] is mapping.metadata[sample]
    assert mapping[full] is mapping.metadata[sample]
    assert mapping.get(full) is mapping.metadata[sample]
    missing = from_json(cast(JSONArtistSample, artist('Missing')), CSLArtistSample)
    assert missing not in mapping
    assert mapping.get(missing) is None
    sentinel = object()
    assert mapping.get(missing, sentinel) is sentinel
    with pytest.raises(KeyError):
        mapping[missing]
    assert dict(mapping) == mapping.metadata
    assert mapping.keys() == mapping.metadata.keys()
    assert [*mapping.items()] == [*mapping.metadata.items()]
    assert [*mapping.values()] == [*mapping.metadata.values()]


async def test_all_unfound_names_are_reported_together(
    client: DBClient | AsyncDBClient,
    router: Router,
) -> None:
    search = mock_search(router, [artist('Alice')])
    with pytest.raises(AMQCSLError) as error:
        await finish(cm.make_artist_to_meta(client, {'Missing one': 'X', 'Alice': 'A', 'Missing two': 'Y'}, ['all']))
    assert 'Missing one' in str(error.value)
    assert 'Missing two' in str(error.value)
    assert search.call_count == 3


async def test_search_phase_logging(
    client: DBClient | AsyncDBClient,
    router: Router,
    caplog: pytest.LogCaptureFixture,
) -> None:
    mock_search(router, [artist('Alice')])
    with caplog.at_level('INFO', logger='amqcsl.workflows.character_metadata'):
        await finish(cm.make_artist_to_meta(client, {'Alice': 'A'}, ['no results']))
    assert 'Searching phrases for artists' in caplog.messages
    assert 'Searching for artists by name directly' in caplog.messages


@pytest.mark.parametrize('factory', ['make', 'class'])
@pytest.mark.parametrize('global_search', [False, True])
async def test_initial_exclusions_override_metadata_and_apply_across_tracks(
    client: DBClient | AsyncDBClient,
    router: Router,
    factory: str,
    global_search: bool,
) -> None:
    a, excluded = artist('Alice'), artist('Ignored')
    search = mock_search(router, [a, excluded])
    mock_metadata(router)
    add = router.post(url=re.compile(r'/api/track/[^/]+/metadata')).mock(return_value=mock_response(200))
    phrases = ['all'] if global_search else []
    definitions: cm.ArtistDict = {'Alice': 'A', 'Ignored': 'IgnoredCharacter'}
    if factory == 'make':
        mapping = await finish(cm.make_artist_to_meta(client, definitions, phrases, exclude=['Ignored', 'Ignored']))
    else:
        if isinstance(client, DBClient):
            mapping = cm.SyncArtistToMeta.create(client, definitions, phrases, exclude=['Ignored'])
        else:
            mapping = await cm.AsyncArtistToMeta.create(client, definitions, phrases, exclude=['Ignored'])
    assert mapping.excluded_artists == {'Ignored'}
    assert from_json(cast(JSONArtistSample, excluded), CSLArtistSample) not in mapping
    assert search.call_count == (1 if global_search else 2)
    for idx in range(2):
        await finish(
            enqueue_edit(
                client, mapping.apply(track(a, excluded, track_id=str(idx)), lambda _track, _reasons: pytest.fail())
            )
        )
    await finish(client.commit())
    assert add.call_count == 2
    for call in cast(Sequence[Call], add.calls):
        assert [meta['value'] for meta in json.loads(request_body(call.request))['extraMetadatas']] == ['A']


@pytest.mark.parametrize('key_kind', ['name', 'tuple', 'artist_name'])
async def test_exclude_only_mapping_resolves_names_and_skips_groups(
    client: DBClient | AsyncDBClient,
    router: Router,
    key_kind: str,
) -> None:
    g = artist('Group', group=True, disambiguation='one')
    other = artist('Group', group=True, disambiguation='two')
    samples = [g] if key_kind == 'name' else [g, other]
    mock_search(router, samples)
    group_query = router.get(url=re.compile(r'/api/artist/[^/]+')).mock(return_value=mock_response(500))
    mock_metadata(router, [{'id': 'stale', 'type': 2, 'key': 'Character', 'value': 'Old'}])
    delete = router.delete('/api/track/test-track/metadata/stale').mock(return_value=mock_response(200))
    keys: dict[str, cm.ArtistKey] = {
        'name': 'Group',
        'tuple': ('Group', 'one'),
        'artist_name': cm.ArtistName('Group', original_name='Group', disambiguation='one'),
    }
    mapping = await finish(cm.make_artist_to_meta(client, {}, exclude=[keys[key_kind]]))
    assert mapping.excluded_artists == {str(g['id'])}
    assert not mapping.metadata
    await finish(enqueue_edit(client, mapping.apply(track(g), lambda _track, _reasons: pytest.fail())))
    await finish(client.commit())
    assert delete.called and not group_query.called


@pytest.mark.parametrize('all_excluded', [False, True])
@pytest.mark.parametrize('initial', [False, True])
async def test_group_members_respect_initial_and_callback_exclusions(
    client: DBClient | AsyncDBClient,
    router: Router,
    all_excluded: bool,
    initial: bool,
) -> None:
    a, excluded, g = artist('Alice'), artist('Ignored', group=True), artist('Group', group=True)
    mock_search(router, [a, excluded])
    members = [excluded] if all_excluded else [a, excluded]
    get_group = router.get(path='/api/artist/Group').mock(
        return_value=mock_response(200, json=group_details(g, members))
    )
    nested = router.get(path='/api/artist/Ignored').mock(
        return_value=mock_response(200, json=group_details(excluded, []))
    )
    mock_metadata(router)
    mapping = await finish(cm.make_artist_to_meta(client, {'Alice': 'A'}, exclude=['Ignored'] if initial else []))
    if not initial:
        await finish(
            enqueue_edit(client, mapping.apply(track(excluded), lambda _track, _reasons: cm.ExcludeDecision.EXCLUDE))
        )
    await finish(
        enqueue_edit(
            client, mapping.apply(track(g), lambda _track, _reasons: pytest.fail('Excluded member reported missing'))
        )
    )
    expected: Sequence[ExtraMetadata] = (
        [] if all_excluded else mapping[from_json(cast(JSONArtistSample, a), CSLArtistSample)]
    )
    assert mapping[from_json(cast(JSONArtistSample, g), CSLArtistSample)] == expected
    assert get_group.call_count == 1
    assert nested.call_count == int(not initial)
    assert len(client.queue) == int(not all_excluded)


@pytest.mark.parametrize('ambiguous', [False, True])
async def test_excluded_names_must_resolve_uniquely(
    client: DBClient | AsyncDBClient,
    router: Router,
    ambiguous: bool,
) -> None:
    mock_search(
        router, [artist('Ignored', disambiguation='one'), artist('Ignored', disambiguation='two')] if ambiguous else []
    )
    with pytest.raises(AMQCSLError, match='2 artists found' if ambiguous else 'Could not find artists: Ignored'):
        await finish(cm.make_artist_to_meta(client, {}, exclude=['Ignored']))
    assert not client.queue


async def test_missing_exclusions_are_reported_with_missing_metadata_names(
    client: DBClient | AsyncDBClient,
    router: Router,
) -> None:
    search = mock_search(router, [])
    with pytest.raises(AMQCSLError) as error:
        await finish(cm.make_artist_to_meta(client, {'Missing': 'A'}, exclude=['Ignored']))
    assert 'Missing' in str(error.value) and 'Ignored' in str(error.value)
    assert search.call_count == 2


async def test_ignore_leaves_track_unchanged_without_caching_exclusions(
    client: DBClient | AsyncDBClient,
    router: Router,
) -> None:
    a, unknown, g, missing = artist('Alice'), artist('Unknown'), artist('Group', group=True), artist('Missing')
    excluded = artist('Already excluded')
    mock_search(router, [a, excluded])
    get_group = router.get(path='/api/artist/Group').mock(
        return_value=mock_response(200, json=group_details(g, [missing]))
    )
    mock_metadata(router)
    untouched = router.get(url=re.compile(r'/api/track/ignored-[^/]+/metadata')).mock(return_value=mock_response(500))
    add = router.post(url=re.compile(r'/api/track/[^/]+/metadata')).mock(return_value=mock_response(200))
    mapping = await finish(cm.make_artist_to_meta(client, {'Alice': 'A'}, exclude=['Already excluded']))
    await finish(enqueue_edit(client, mapping.apply(track(a, track_id='before'))))
    queued = [*client.queue]
    calls: list[tuple[CSLTrack, Sequence[cm.Reason]]] = []

    def ignore(t: CSLTrack, reasons: Sequence[cm.Reason]) -> cm.ExcludeDecision:
        calls.append((t, reasons))
        return cm.ExcludeDecision.IGNORE

    for idx in range(2):
        t = track(a, unknown, g, track_id=f'ignored-{idx}')
        await finish(enqueue_edit(client, mapping.apply(t, ignore)))
        assert calls[-1][0] is t
        assert [reason.artist.id for reason in calls[-1][1]] == ['Unknown', 'Group']
        assert client.queue == queued
        assert mapping.excluded_artists == {'Already excluded'}
    assert len(calls) == 2 and get_group.call_count == 2
    assert not untouched.called
    with pytest.raises(AMQCSLError, match='Cannot infer'):
        await finish(
            enqueue_edit(client, mapping.apply(track(unknown), lambda _track, _reasons: cm.ExcludeDecision.ERROR))
        )
    await finish(enqueue_edit(client, mapping.apply(track(a, track_id='after'))))
    await finish(client.commit())
    assert add.call_count == 2
    assert {urlsplit(request_url(call.request)).path for call in cast(Sequence[Call], add.calls)} == {
        '/api/track/before/metadata',
        '/api/track/after/metadata',
    }
    assert not untouched.called


@pytest.mark.parametrize('client', ['async'], indirect=True)
@pytest.mark.parametrize('request_kind', ['groups', 'metadata'])
async def test_track_application_requests_overlap_without_duplicate_exclusion_decisions(
    client: DBClient | AsyncDBClient,
    router: Router,
    request_kind: str,
) -> None:
    assert isinstance(client, AsyncDBClient)
    a, unknown = artist('Alice'), artist('Unknown')
    groups = [artist('Group', group=True), artist('OtherGroup', group=True)]
    mock_search(router, [a])
    arrived: set[str] = set()
    both_started = asyncio.Event()

    async def wait_for_both(key: str) -> None:
        arrived.add(key)
        if len(arrived) == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), timeout=2)

    async def get_group(req: Request) -> Response:
        group_id = urlsplit(request_url(req)).path.rsplit('/', 1)[-1]
        if request_kind == 'groups':
            await wait_for_both(group_id)
        group = next(sample for sample in groups if sample['id'] == group_id)
        return mock_response(200, json=group_details(group, [a]))

    async def get_metadata(req: Request) -> Response:
        if request_kind == 'metadata':
            await wait_for_both(urlsplit(request_url(req)).path)
        return mock_response(
            404, json={'statusCode': 404, 'errors': {'generalErrors': ['Song does not have metadata']}}
        )

    router.get(url=re.compile(r'/api/artist/[^/]+')).mock(side_effect=get_group)
    router.get(url=re.compile(r'/api/track/[^/]+/metadata')).mock(side_effect=get_metadata)
    add = router.post(url=re.compile(r'/api/track/[^/]+/metadata')).mock(return_value=mock_response(200))
    mapping = await cm.make_artist_to_meta(client, {'Alice': 'A'})
    failures: list[cm.Reason] = []

    def exclude(_: CSLTrack, reasons: Sequence[cm.Reason]) -> cm.ExcludeDecision:
        failures.extend(reasons)
        return cm.ExcludeDecision.EXCLUDE

    await asyncio.gather(
        *(
            enqueue_edit(client, mapping.apply(track(group, unknown, track_id=str(idx)), exclude))
            for idx, group in enumerate(groups)
        )
    )
    assert len(arrived) == 2
    assert [failure.artist.id for failure in failures] == ['Unknown']
    assert mapping.excluded_artists == {'Unknown'}
    assert len(mapping.metadata) == 3
    assert len(client.queue) == 2 and not add.called
    await client.commit()
    assert add.call_count == 2


async def test_default_ignore_prompt_does_not_fetch_metadata(
    client: DBClient | AsyncDBClient,
    router: Router,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mapping = await finish(cm.make_artist_to_meta(client, {}))
    metadata = mock_metadata(router)

    def read(_: str) -> str:
        return 'ignore'

    monkeypatch.setattr('builtins.input', read)
    await finish(enqueue_edit(client, mapping.apply(track(artist('Unknown')))))
    assert not metadata.called and not client.queue and not mapping.excluded_artists


@pytest.mark.parametrize('client', ['async'], indirect=True)
async def test_concurrent_group_fetches_recheck_cached_exclusions(
    client: DBClient | AsyncDBClient,
    router: Router,
) -> None:
    assert isinstance(client, AsyncDBClient)
    a, g, missing = artist('Alice'), artist('Group', group=True), artist('Missing')
    mock_search(router, [a])
    started = 0
    both_started = asyncio.Event()

    async def get_group(_: Request) -> Response:
        nonlocal started
        started += 1
        if started == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), timeout=2)
        return mock_response(200, json=group_details(g, [missing]))

    router.get(path='/api/artist/Group').mock(side_effect=get_group)
    mock_metadata(router)
    mapping = await cm.make_artist_to_meta(client, {'Alice': 'A'})
    failures: list[cm.Reason] = []

    def exclude(_: CSLTrack, reasons: Sequence[cm.Reason]) -> cm.ExcludeDecision:
        failures.extend(reasons)
        return cm.ExcludeDecision.EXCLUDE

    await asyncio.gather(
        *(enqueue_edit(client, mapping.apply(track(a, g, track_id=str(idx)), exclude)) for idx in range(2))
    )
    assert started == 2
    assert [failure.artist.id for failure in failures] == ['Group']
    assert mapping.excluded_artists == {'Group'}
    assert len(client.queue) == 2


@pytest.mark.parametrize('client', ['async'], indirect=True)
@pytest.mark.parametrize('cancel', [False, True])
async def test_open_prompt_allows_requests_and_serializes_decisions(
    client: DBClient | AsyncDBClient,
    router: Router,
    monkeypatch: pytest.MonkeyPatch,
    cancel: bool,
) -> None:
    assert isinstance(client, AsyncDBClient)
    a, unknown = artist('Alice'), artist('Unknown')
    mock_search(router, [a])
    metadata = mock_metadata(router)
    mapping = await cm.make_artist_to_meta(client, {'Alice': 'A'})
    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    release = threading.Event()
    calls = 0

    def read(_: str) -> str:
        nonlocal calls
        calls += 1
        loop.call_soon_threadsafe(started.set)
        if not release.wait(timeout=5):
            raise AssertionError('Prompt was not released')
        return 'yes'

    monkeypatch.setattr('builtins.input', read)
    first = asyncio.create_task(enqueue_edit(client, mapping.apply(track(unknown, track_id='first'))))
    second: asyncio.Task[None] | None = None
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        second = asyncio.create_task(enqueue_edit(client, mapping.apply(track(unknown, track_id='second'))))
        if cancel:
            first.cancel()
        await asyncio.wait_for(enqueue_edit(client, mapping.apply(track(a, track_id='known'))), timeout=2)
        assert metadata.called
        assert calls == 1
        assert not first.done() and not second.done()
    finally:
        release.set()
        await asyncio.gather(first, *([second] if second is not None else []), return_exceptions=True)
    assert calls == (2 if cancel else 1)
    assert first.cancelled() is cancel
    assert second is not None and not second.cancelled() and second.exception() is None
    assert mapping.excluded_artists == {'Unknown'}
    assert len(client.queue) == 1


async def test_iter_edits_leaves_enqueueing_to_caller(
    client: DBClient | AsyncDBClient,
    router: Router,
) -> None:
    a, unknown = artist('Alice'), artist('Unknown')
    mock_search(router, [a])
    mock_metadata(router)
    mapping = await finish(cm.make_artist_to_meta(client, {'Alice': 'A'}))
    tracks = [track(a, track_id='first'), track(unknown), track(a, track_id='last')]

    def ignore(_: CSLTrack, reasons: Sequence[cm.Reason]) -> cm.ExcludeDecision:
        assert reasons
        assert threading.current_thread() is threading.main_thread()
        return cm.ExcludeDecision.IGNORE

    async def source() -> AsyncIterator[CSLTrack]:
        for item in tracks:
            yield item

    if isinstance(mapping, cm.AsyncArtistToMeta):
        edits = [edit async for edit in mapping.iter_edits(source(), ignore)]
    else:
        edits = [*mapping.iter_edits(tracks, ignore)]
    assert len(edits) == 2 and not client.queue
    for edit in edits:
        client.enqueue(edit)
    assert len(client.queue) == 2


@pytest.mark.parametrize('client', ['async'], indirect=True)
async def test_iter_edits_serializes_async_callbacks_and_keeps_requests_running(
    client: DBClient | AsyncDBClient,
    router: Router,
) -> None:
    assert isinstance(client, AsyncDBClient)
    a, unknown = artist('Alice'), artist('Unknown')
    mock_search(router, [a])
    metadata = mock_metadata(router)
    mapping = await cm.make_artist_to_meta(client, {'Alice': 'A'})
    started, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def exclude(_: CSLTrack, reasons: Sequence[cm.Reason]) -> cm.ExcludeDecision:
        nonlocal calls
        calls += 1
        assert [reason.artist.id for reason in reasons] == ['Unknown']
        started.set()
        await release.wait()
        return cm.ExcludeDecision.EXCLUDE

    async def source() -> AsyncIterator[CSLTrack]:
        yield track(a, unknown, track_id='first')
        yield track(a, unknown, track_id='second')
        await started.wait()
        yield track(a, track_id='known')

    edits = mapping.iter_edits(source(), exclude)
    try:
        edit = await asyncio.wait_for(anext(edits), timeout=2)
        assert started.is_set() and calls == 1 and metadata.called
        assert not client.queue
        release.set()
        remaining = [item async for item in edits]
        assert len(remaining) == 2
        assert calls == 1 and mapping.excluded_artists == {'Unknown'}
        client.enqueue(edit)
    finally:
        release.set()
        await edits.aclose()


@pytest.mark.parametrize('client', ['async'], indirect=True)
async def test_iter_edits_closing_cancels_pending_decisions(
    client: DBClient | AsyncDBClient,
    router: Router,
) -> None:
    assert isinstance(client, AsyncDBClient)
    a = artist('Alice')
    mock_search(router, [a])
    mock_metadata(router)
    mapping = await cm.make_artist_to_meta(client, {'Alice': 'A'})
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def ignore(_: CSLTrack, _reasons: Sequence[cm.Reason]) -> cm.ExcludeDecision:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
        return cm.ExcludeDecision.IGNORE

    async def source() -> AsyncIterator[CSLTrack]:
        yield track(artist('Unknown'))
        await started.wait()
        yield track(a)

    edits = mapping.iter_edits(source(), ignore)
    await asyncio.wait_for(anext(edits), timeout=2)
    await asyncio.wait_for(edits.aclose(), timeout=2)
    assert cancelled.is_set() and not client.queue and not mapping.excluded_artists


@pytest.mark.parametrize('client', ['async'], indirect=True)
async def test_iter_edits_propagates_decision_errors(
    client: DBClient | AsyncDBClient,
    router: Router,
) -> None:
    assert isinstance(client, AsyncDBClient)
    mapping = cm.AsyncArtistToMeta(client, {})

    async def source() -> AsyncIterator[CSLTrack]:
        yield track(artist('Unknown'))

    def fail(_: CSLTrack, _reasons: Sequence[cm.Reason]) -> cm.ExcludeDecision:
        raise ValueError('decision failed')

    with pytest.raises(ExceptionGroup) as caught:
        _ = [edit async for edit in mapping.iter_edits(source(), fail)]
    assert any(isinstance(error, ValueError) for error in caught.value.exceptions)
    assert not client.queue


async def test_apply_returns_unqueued_edits(
    client: DBClient | AsyncDBClient,
    router: Router,
) -> None:
    a = artist('Alice')
    mock_search(router, [a])
    mock_metadata(router)
    add = router.post(url=re.compile(r'/api/track/[^/]+/metadata')).mock(return_value=mock_response(200))
    mapping = await finish(cm.make_artist_to_meta(client, {'Alice': 'A'}))

    bundle = await finish(mapping.apply(track(a)))
    assert bundle is not None
    assert not client.queue and not add.called
    ignored = await finish(mapping.apply(track(artist('Unknown')), lambda _t, _r: cm.ExcludeDecision.IGNORE))
    assert ignored is None and not client.queue and not add.called

    client.enqueue(bundle)
    await finish(client.commit())
    assert add.call_count == 1


async def test_empty_group_prepares_stale_character_removal(
    client: DBClient | AsyncDBClient,
    router: Router,
) -> None:
    g = artist('Group', group=True)
    router.get('/api/artist/Group').mock(return_value=mock_response(200, json=group_details(g, [])))
    mock_metadata(
        router,
        [
            {'id': 'stale', 'type': 2, 'key': 'Character', 'value': 'Old'},
            {'id': 'language', 'type': 1, 'key': 'Language', 'value': 'Japanese'},
        ],
    )
    delete = router.delete('/api/track/test-track/metadata/stale').mock(return_value=mock_response(200))
    unrelated = router.delete('/api/track/test-track/metadata/language').mock(return_value=mock_response(500))
    mapping = await finish(cm.make_artist_to_meta(client, {}))
    bundle = await finish(mapping.apply(track(g), lambda _t, _r: pytest.fail('Empty group prompted')))
    assert bundle is not None and not client.queue and not delete.called
    client.enqueue(bundle)
    await finish(client.commit())
    assert delete.call_count == 1 and not unrelated.called


@pytest.mark.parametrize('separator', [None, ' / '])
async def test_artist_dictionary_uses_character_names_and_supports_custom_separators(
    client: DBClient | AsyncDBClient,
    router: Router,
    separator: str | None,
) -> None:
    a = artist('Alice')
    mock_search(router, [a])
    definitions: cm.ArtistDict = {'Alice': (separator or ', ').join(['First Character', 'Second Character'])}
    mapping = await finish(
        cm.make_artist_to_meta(client, definitions)
        if separator is None
        else cm.make_artist_to_meta(client, definitions, sep=separator)
    )
    sample = from_json(cast(JSONArtistSample, a), CSLArtistSample)
    assert [meta.value for meta in mapping[sample]] == ['First Character', 'Second Character']


async def test_make_artist_to_meta_rejects_unsupported_client() -> None:
    unsupported = cast(DBClient, object())
    with pytest.raises(TypeError, match='Expected DBClient or AsyncDBClient, received object'):
        cm.make_artist_to_meta(unsupported, {})
