import asyncio
import inspect
import json
from collections.abc import Sequence

import pytest
from attrs import evolve
from helpers import load
from httpx import Request, Response
from respx import Router

from amqcsl import AsyncDBClient, DBClient
from amqcsl.exceptions import AMQCSLError, QueryError
from amqcsl.objects import CSLArtist, CSLArtistSample, CSLTrack
from amqcsl.workflows import character as cm

pytestmark = pytest.mark.asyncio


def artist(name: str, *, group: bool = False, disambiguation: str | None = None) -> dict:
    return {
        'id': name + (disambiguation or ''),
        'name': name,
        'originalName': name,
        'disambiguation': disambiguation,
        'type': 3 if group else 1,
    }


def group_details(sample: dict, members: Sequence[dict], other: Sequence[dict] = ()) -> dict:
    return {
        **sample,
        'forwardRelations': [{'id': f'member-{i}', 'type': 1, 'artist': member} for i, member in enumerate(members)]
        + [{'id': f'other-{i}', 'type': 2, 'artist': member} for i, member in enumerate(other)],
        'reverseRelations': [],
        'linkedAMQSongs': [],
        'linkedTracks': [],
    }


def track(*samples: dict, track_id: str = 'test-track') -> CSLTrack:
    data = load('superstar/aspire')[0]
    data = {
        **data,
        'id': track_id,
        'artistCredits': [
            {'name': sample['name'], 'joinPhrase': '', 'position': i, 'artist': sample}
            for i, sample in enumerate(samples)
        ],
    }
    return CSLTrack.from_json(data)


def mock_search(router: Router, samples: Sequence[dict]):
    def search(req: Request):
        phrase = req.url.params['searchTerm']
        found = [a for a in samples if phrase == 'all' or phrase == a['name']]
        skip, take = int(req.url.params['skip']), int(req.url.params['take'])
        return Response(200, json={'count': len(found), 'artists': found[skip : skip + take]})

    return router.get('/api/artists').mock(side_effect=search)


def mock_metadata(router: Router, extra: Sequence[dict] = ()):
    if extra:
        response = Response(
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
        response = Response(404, json={'statusCode': 404, 'errors': {'generalErrors': ['Song does not have metadata']}})
    return router.get(url__regex=r'/api/track/[^/]+/metadata') % response


async def finish(result):
    return await result if inspect.isawaitable(result) else result


@pytest.fixture(params=['sync', 'async'])
def db(request, client: DBClient, aclient: AsyncDBClient):
    return client if request.param == 'sync' else aclient


async def test_infer_group_and_cache(db, router: Router):
    a, b, g = artist('Alice'), artist('Bob'), artist('Group', group=True)
    irrelevant = artist('Irrelevant')
    search = mock_search(router, [a, b, g])
    get_group = router.get('/api/artist/Group') % Response(200, json=group_details(g, [a, b, a], [irrelevant]))
    mock_metadata(router)
    add = router.post(url__regex=r'/api/track/[^/]+/metadata') % Response(200)
    mapping = await finish(cm.make_artist_to_meta(db, {'a': 'A', 'b': 'B'}, {'Alice': 'a', 'Bob': 'b'}, ['all']))
    assert len(mapping.metadata) == 2  # Discovered groups are not inferred until credited.
    assert search.call_count == 1
    for t in [track(g, a), track(g, track_id='second')]:
        await finish(cm.apply_artist_to_meta(db, mapping, t, lambda *_: pytest.fail('Unexpected failure')))
    assert get_group.call_count == 1
    assert search.call_count == 1
    assert mapping.metadata[CSLArtistSample.from_json(g)] == [
        *mapping.metadata[CSLArtistSample.from_json(a)],
        *mapping.metadata[CSLArtistSample.from_json(b)],
    ]
    await finish(db.commit())
    assert add.call_count == 2
    for call in add.calls:
        assert {meta['value'] for meta in json.loads(call.request.content)['extraMetadatas']} == {'A', 'B'}


async def test_explicit_group_overrides_members(db, router: Router):
    g = artist('Group', group=True)
    mock_search(router, [g])
    get_group = router.get('/api/artist/Group') % Response(500)
    mock_metadata(router)
    mapping = await finish(cm.compact_make_artist_to_meta(db, {'Group': 'Override'}, ['all']))
    await finish(cm.apply_artist_to_meta(db, mapping, track(g)))
    assert not get_group.called
    assert len(db.queue) == 1


async def test_callback_collates_failures_and_caches_exclusions(db, router: Router):
    a, b, c = artist('Alice'), artist('Bob'), artist('Carol')
    g, unknown = artist('Group', group=True), artist('Unknown')
    mock_search(router, [a])
    get_group = router.get('/api/artist/Group') % Response(200, json=group_details(g, [a, b, c]))
    mock_metadata(router)
    calls: list[tuple[CSLTrack, Sequence[cm.Reason]]] = []

    def should_exclude(t, reasons):
        calls.append((t, reasons))
        return True

    mapping = await finish(cm.compact_make_artist_to_meta(db, {'Alice': 'A'}, ['all']))
    first = track(g, unknown, unknown, a)
    await finish(cm.apply_artist_to_meta(db, mapping, first, should_exclude))
    await finish(cm.apply_artist_to_meta(db, mapping, track(unknown, g, a, track_id='second'), should_exclude))
    assert len(calls) == 1
    assert calls[0][0] is first
    reasons = calls[0][1]
    assert [r.artist.id for r in reasons] == ['Group', 'Unknown']
    assert isinstance(reasons[0].artist, CSLArtist)
    assert isinstance(reasons[0].reason, cm.INCOMPLETE_GROUP)
    assert [*reasons[0].reason.artists] == [CSLArtistSample.from_json(b), CSLArtistSample.from_json(c)]
    assert reasons[1].reason is cm.UNKNOWN_ARTIST
    assert mapping.excluded_artists == {'Group', 'Unknown'}
    assert get_group.call_count == 1
    assert len(db.queue) == 2


async def test_rejected_exclusion_raises_without_queueing(db, router: Router):
    unknown = artist('Unknown')
    get_meta = mock_metadata(router)
    mapping = await finish(cm.compact_make_artist_to_meta(db, {}))
    for _ in range(2):
        with pytest.raises(AMQCSLError, match='Cannot infer'):
            await finish(cm.apply_artist_to_meta(db, mapping, track(unknown), lambda *_: False))
    assert not mapping.excluded_artists
    assert not db.queue
    assert not get_meta.called


@pytest.mark.parametrize('members', [[], [artist('Nested', group=True)]])
async def test_missing_or_nested_members_do_not_recurse(db, router: Router, members):
    g = artist('Group', group=True)
    get_group = router.get('/api/artist/Group') % Response(200, json=group_details(g, members))
    nested = router.get('/api/artist/Nested') % Response(500)
    mock_metadata(router)
    mapping = await finish(cm.compact_make_artist_to_meta(db, {}))
    failures = []

    def should_exclude(_, reasons):
        failures.extend(reasons)
        return True

    await finish(cm.apply_artist_to_meta(db, mapping, track(g), should_exclude))
    assert isinstance(failures[0].reason, cm.INCOMPLETE_GROUP)
    assert [*failures[0].reason.artists] == [CSLArtistSample.from_json(m) for m in members]
    assert get_group.called and not nested.called
    assert not db.queue


async def test_nested_group_with_explicit_metadata(db, router: Router):
    nested, g = artist('Nested', group=True), artist('Group', group=True)
    mock_search(router, [nested])
    router.get('/api/artist/Group') % Response(200, json=group_details(g, [nested]))
    nested_query = router.get('/api/artist/Nested') % Response(500)
    mock_metadata(router)
    mapping = await finish(cm.compact_make_artist_to_meta(db, {'Nested': 'Character'}))
    await finish(cm.apply_artist_to_meta(db, mapping, track(g)))
    assert not nested_query.called
    assert mapping.metadata[CSLArtistSample.from_json(g)] == mapping.metadata[CSLArtistSample.from_json(nested)]


async def test_exclusion_keeps_additions_and_all_stale_deletions(db, router: Router):
    a, unknown = artist('Alice'), artist('Unknown')
    mock_search(router, [a])
    extra = [
        {'id': 'stale1', 'type': 2, 'key': 'Character', 'value': 'Old'},
        {'id': 'stale2', 'type': 1, 'key': 'Character', 'value': 'Other'},
        {'id': 'unrelated', 'type': 1, 'key': 'Language', 'value': 'Japanese'},
    ]
    mock_metadata(router, extra)
    add = router.post('/api/track/test-track/metadata') % Response(200)
    delete1 = router.delete('/api/track/test-track/metadata/stale1') % Response(200)
    delete2 = router.delete('/api/track/test-track/metadata/stale2') % Response(200)
    unrelated = router.delete('/api/track/test-track/metadata/unrelated') % Response(500)
    mapping = await finish(cm.compact_make_artist_to_meta(db, {'Alice': 'New'}))
    await finish(cm.apply_artist_to_meta(db, mapping, track(a, unknown), lambda *_: True))
    assert len(db.queue) == 1 and len(db.queue[0].bundles) == 3
    assert not add.called and not delete1.called  # Only queued until commit.
    await finish(db.commit())
    assert add.called and delete1.called and delete2.called
    assert not unrelated.called


async def test_only_deletions_and_no_changes(db, router: Router):
    a, unknown = artist('Alice'), artist('Unknown')
    mock_search(router, [a])
    mock_metadata(router, [{'id': 'stale', 'type': 2, 'key': 'Character', 'value': 'Old'}])
    delete = router.delete('/api/track/test-track/metadata/stale') % Response(200)
    mapping = await finish(cm.compact_make_artist_to_meta(db, {'Alice': 'Old'}))
    await finish(cm.apply_artist_to_meta(db, mapping, track(a)))
    assert not db.queue
    await finish(cm.apply_artist_to_meta(db, mapping, track(unknown), lambda *_: True))
    assert len(db.queue[0].bundles) == 1
    await finish(db.commit())
    assert delete.called


async def test_off_vocal_skips_queries_and_callback(db, router: Router):
    mapping = await finish(cm.compact_make_artist_to_meta(db, {}))
    get_meta = mock_metadata(router)
    await finish(
        cm.apply_artist_to_meta(db, mapping, evolve(track(artist('Unknown')), type_id=1), lambda *_: pytest.fail())
    )
    assert not get_meta.called and not db.queue


async def test_disambiguation_and_pagination(db, router: Router):
    a, b = artist('Alice', disambiguation='one'), artist('Alice', disambiguation='two')
    db.max_batch_size = 1
    search = mock_search(router, [a, b, artist('Unlisted', group=True)])
    mapping = await finish(cm.compact_make_artist_to_meta(db, {('Alice', 'two'): 'B'}, ['all']))
    assert len(mapping.metadata) == 1
    assert mapping.metadata[CSLArtistSample.from_json(b)][0].value == 'B'
    assert search.call_count == 3


@pytest.mark.parametrize(
    'definitions, error',
    [
        ({'Missing': 'X'}, 'Could not find'),
        ({'Alice': 'X'}, '2 artists found'),
        ({'Alice': 'X', ('Alice', 'one'): 'Y'}, 'both match'),
    ],
)
async def test_matching_errors(db, router: Router, definitions, error):
    samples = [artist('Alice', disambiguation='one')]
    if error == '2 artists found':
        samples.append(artist('Alice', disambiguation='two'))
    mock_search(router, samples)
    with pytest.raises(AMQCSLError, match=error):
        await finish(cm.compact_make_artist_to_meta(db, definitions, ['all']))


async def test_query_limit(db, router: Router):
    db.max_query_size = 1
    mock_search(router, [artist('Alice'), artist('Bob')])
    with pytest.raises(QueryError, match='max query size'):
        await finish(cm.compact_make_artist_to_meta(db, {}, ['all']))


async def test_global_searches_run_in_parallel(aclient: AsyncDBClient, router: Router):
    arrived: set[str] = {*()}
    both_started = asyncio.Event()

    async def search(req: Request):
        arrived.add(req.url.params['searchTerm'])
        if len(arrived) == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), timeout=2)
        return Response(200, json={'artists': [], 'count': 0})

    route = router.get('/api/artists').mock(side_effect=search)
    mapping = await cm.compact_make_artist_to_meta(aclient, {}, ['one', 'two', 'one'])
    assert not mapping.metadata
    assert route.call_count == 2


async def test_concurrent_apply_shares_exclusion(aclient: AsyncDBClient, router: Router):
    mapping = await cm.compact_make_artist_to_meta(aclient, {})
    mock_metadata(router)
    calls = []

    def should_exclude(t, reasons):
        calls.append(t.id)
        return True

    await asyncio.gather(
        *(
            cm.apply_artist_to_meta(aclient, mapping, track(artist('Unknown'), track_id=str(i)), should_exclude)
            for i in range(3)
        )
    )
    assert len(calls) == 1


async def test_cached_group_can_be_used_as_member_without_recursion(db, router: Router):
    a, nested, parent = artist('Alice'), artist('Nested', group=True), artist('Parent', group=True)
    mock_search(router, [a])
    nested_query = router.get('/api/artist/Nested') % Response(200, json=group_details(nested, [a]))
    parent_query = router.get('/api/artist/Parent') % Response(200, json=group_details(parent, [nested]))
    mock_metadata(router)
    mapping = await finish(cm.compact_make_artist_to_meta(db, {'Alice': 'A'}))
    await finish(cm.apply_artist_to_meta(db, mapping, track(nested)))
    await finish(cm.apply_artist_to_meta(db, mapping, track(parent, track_id='parent-track')))
    assert nested_query.call_count == 1 and parent_query.call_count == 1
    assert mapping.metadata[CSLArtistSample.from_json(parent)] == mapping.metadata[CSLArtistSample.from_json(a)]


async def test_full_artist_credit_uses_existing_group_details(db, router: Router):
    a, g = artist('Alice'), artist('Group', group=True)
    mock_search(router, [a])
    get_group = router.get('/api/artist/Group') % Response(500)
    mock_metadata(router)
    mapping = await finish(cm.compact_make_artist_to_meta(db, {'Alice': 'A'}))
    t = track(g)
    credit = evolve(t.artist_credits[0], artist=CSLArtist.from_json(group_details(g, [a])))
    await finish(cm.apply_artist_to_meta(db, mapping, evolve(t, artist_credits=[credit])))
    assert not get_group.called
    assert CSLArtistSample.from_json(g) in mapping.metadata


async def test_group_queries_for_one_track_run_in_parallel(aclient: AsyncDBClient, router: Router):
    a, g, h = artist('Alice'), artist('Group', group=True), artist('OtherGroup', group=True)
    mock_search(router, [a])
    arrived: set[str] = {*()}
    both_started = asyncio.Event()

    async def get_group(req: Request):
        name = req.url.path.rsplit('/', 1)[-1]
        arrived.add(name)
        if len(arrived) == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), timeout=2)
        return Response(200, json=group_details(g if name == 'Group' else h, [a]))

    router.get(url__regex=r'/api/artist/[^/]+').mock(side_effect=get_group)
    mock_metadata(router)
    mapping = await cm.compact_make_artist_to_meta(aclient, {'Alice': 'A'})
    await cm.apply_artist_to_meta(aclient, mapping, track(g, h))
    assert len(mapping.metadata) == 3


async def test_metadata_bundle_handles_multiple_adds_deletes_and_noop(db, router: Router):
    from amqcsl.clients.bundles import TrackAddMetadataBundle, TrackDeleteMetadataBundle, parallel_actions
    from amqcsl.objects import CSLExtraMetadata, ExtraMetadata

    t = track(artist('Alice'))
    add = router.post('/api/track/test-track/metadata') % Response(200)
    deletes = [router.delete(f'/api/track/test-track/metadata/{i}') % Response(200) for i in range(2)]
    bundles = [
        TrackAddMetadataBundle(t, []),
        TrackAddMetadataBundle(t, [ExtraMetadata(True, 'Character', 'A')]),
        TrackAddMetadataBundle(t, [ExtraMetadata(True, 'Character', 'B')]),
        *[TrackDeleteMetadataBundle(t, CSLExtraMetadata(str(i), 2, 'Character', 'Old')) for i in range(2)],
    ]
    await finish(db.process(parallel_actions(bundles)))
    assert add.call_count == 2
    assert all(route.call_count == 1 for route in deletes)


async def test_mapping_methods_delegate_to_metadata(db, router: Router):
    a = artist('Alice')
    mock_search(router, [a])
    mapping = await finish(cm.compact_make_artist_to_meta(db, {'Alice': 'A'}))
    sample = CSLArtistSample.from_json(a)
    full = CSLArtist.from_json(group_details(a, []))
    assert len(mapping) == 1
    assert [*mapping] == [sample]
    assert sample in mapping and full in mapping
    assert 'Alice' not in mapping
    assert mapping[sample] is mapping.metadata[sample]
    assert mapping[full] is mapping.metadata[sample]
    assert mapping.get(full) is mapping.metadata[sample]
    missing = CSLArtistSample.from_json(artist('Missing'))
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


async def test_all_unfound_names_are_reported_together(db, router: Router):
    search = mock_search(router, [artist('Alice')])
    with pytest.raises(AMQCSLError) as error:
        await finish(
            cm.compact_make_artist_to_meta(db, {'Missing one': 'X', 'Alice': 'A', 'Missing two': 'Y'}, ['all'])
        )
    assert 'Missing one' in str(error.value)
    assert 'Missing two' in str(error.value)
    assert search.call_count == 3


async def test_search_phase_logging(db, router: Router, caplog):
    mock_search(router, [artist('Alice')])
    with caplog.at_level('INFO', logger='amqcsl.workflows.character_metadata'):
        await finish(cm.compact_make_artist_to_meta(db, {'Alice': 'A'}, ['no results']))
    assert 'Searching phrases for artists' in caplog.messages
    assert 'Searching for artists by name directly' in caplog.messages
