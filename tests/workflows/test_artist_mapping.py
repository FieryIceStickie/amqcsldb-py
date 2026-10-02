import asyncio
import json
from collections.abc import Awaitable, Sequence
from typing import cast

import pytest
from attrs import evolve
from helpers import load
from httpx import Request, Response
from respx import Route, Router
from respx.models import Call

from amqcsl import AsyncDBClient, DBClient
from amqcsl.exceptions import AMQCSLError, QueryError
from amqcsl.objects import CSLArtist, CSLArtistSample, CSLTrack, ExtraMetadata
from amqcsl.objects._json_types import JSONType
from amqcsl.clients.bundles._parallel import _ParallelActionsBundle  # pyright: ignore[reportPrivateUsage] -- inspect queued actions
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
    return CSLTrack.from_json(data)


def mock_search(router: Router, samples: Sequence[dict[str, JSONType]]) -> Route:
    def search(req: Request) -> Response:
        phrase = req.url.params['searchTerm']
        found = [a for a in samples if phrase == 'all' or phrase == a['name']]
        skip, take = int(req.url.params['skip']), int(req.url.params['take'])
        return Response(200, json={'count': len(found), 'artists': found[skip : skip + take]})

    return router.get('/api/artists').mock(side_effect=search)


def mock_metadata(router: Router, extra: Sequence[dict[str, JSONType]] = ()) -> Route:
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


async def finish[T](result: T | Awaitable[T]) -> T:
    return await cast(Awaitable[T], result) if isinstance(result, Awaitable) else cast(T, result)


@pytest.fixture(params=['sync', 'async'])
def db(
    request: pytest.FixtureRequest,
    client: DBClient,
    aclient: AsyncDBClient,
) -> DBClient | AsyncDBClient:
    return client if request.param == 'sync' else aclient


async def test_infer_group_and_cache(
    db: DBClient | AsyncDBClient,
    router: Router,
) -> None:
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
        await finish(mapping.apply(t, lambda _track, _reasons: pytest.fail('Unexpected failure')))
    assert get_group.call_count == 1
    assert search.call_count == 1
    assert mapping.metadata[CSLArtistSample.from_json(g)] == [
        *mapping.metadata[CSLArtistSample.from_json(a)],
        *mapping.metadata[CSLArtistSample.from_json(b)],
    ]
    await finish(db.commit())
    assert add.call_count == 2
    for call in cast(Sequence[Call], add.calls):
        assert {meta['value'] for meta in json.loads(call.request.content)['extraMetadatas']} == {'A', 'B'}


async def test_explicit_group_overrides_members(
    db: DBClient | AsyncDBClient,
    router: Router,
) -> None:
    g = artist('Group', group=True)
    mock_search(router, [g])
    get_group = router.get('/api/artist/Group') % Response(500)
    mock_metadata(router)
    mapping = await finish(cm.compact_make_artist_to_meta(db, {'Group': 'Override'}, ['all']))
    await finish(mapping.apply(track(g)))
    assert not get_group.called
    assert len(db.queue) == 1


async def test_callback_collates_failures_and_caches_exclusions(
    db: DBClient | AsyncDBClient,
    router: Router,
) -> None:
    a, b, c = artist('Alice'), artist('Bob'), artist('Carol')
    g, unknown = artist('Group', group=True), artist('Unknown')
    mock_search(router, [a])
    get_group = router.get('/api/artist/Group') % Response(200, json=group_details(g, [a, b, c]))
    mock_metadata(router)
    calls: list[tuple[CSLTrack, Sequence[cm.Reason]]] = []

    def should_exclude(t: CSLTrack, reasons: Sequence[cm.Reason]) -> bool:
        calls.append((t, reasons))
        return True

    mapping = await finish(cm.compact_make_artist_to_meta(db, {'Alice': 'A'}, ['all']))
    first = track(g, unknown, unknown, a)
    await finish(mapping.apply(first, should_exclude))
    await finish(mapping.apply(track(unknown, g, a, track_id='second'), should_exclude))
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


async def test_rejected_exclusion_raises_without_queueing(
    db: DBClient | AsyncDBClient,
    router: Router,
) -> None:
    unknown = artist('Unknown')
    get_meta = mock_metadata(router)
    mapping = await finish(cm.compact_make_artist_to_meta(db, {}))
    for _ in range(2):
        with pytest.raises(AMQCSLError, match='Cannot infer'):
            await finish(mapping.apply(track(unknown), lambda _track, _reasons: False))
    assert not mapping.excluded_artists
    assert not db.queue
    assert not get_meta.called


@pytest.mark.parametrize('members', [[], [artist('Nested', group=True)]])
async def test_missing_or_nested_members_do_not_recurse(
    db: DBClient | AsyncDBClient,
    router: Router,
    members: list[dict[str, JSONType]],
) -> None:
    g = artist('Group', group=True)
    get_group = router.get('/api/artist/Group') % Response(200, json=group_details(g, members))
    nested = router.get('/api/artist/Nested') % Response(500)
    mock_metadata(router)
    mapping = await finish(cm.compact_make_artist_to_meta(db, {}))
    failures: list[cm.Reason] = []

    def should_exclude(_: CSLTrack, reasons: Sequence[cm.Reason]) -> bool:
        failures.extend(reasons)
        return True

    await finish(mapping.apply(track(g), should_exclude))
    assert isinstance(failures[0].reason, cm.INCOMPLETE_GROUP)
    assert [*failures[0].reason.artists] == [CSLArtistSample.from_json(m) for m in members]
    assert get_group.called and not nested.called
    assert not db.queue


async def test_nested_group_with_explicit_metadata(
    db: DBClient | AsyncDBClient,
    router: Router,
) -> None:
    nested, g = artist('Nested', group=True), artist('Group', group=True)
    mock_search(router, [nested])
    _ = router.get('/api/artist/Group') % Response(200, json=group_details(g, [nested]))
    nested_query = router.get('/api/artist/Nested') % Response(500)
    mock_metadata(router)
    mapping = await finish(cm.compact_make_artist_to_meta(db, {'Nested': 'Character'}))
    await finish(mapping.apply(track(g)))
    assert not nested_query.called
    assert mapping.metadata[CSLArtistSample.from_json(g)] == mapping.metadata[CSLArtistSample.from_json(nested)]


async def test_exclusion_keeps_additions_and_all_stale_deletions(
    db: DBClient | AsyncDBClient,
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
    add = router.post('/api/track/test-track/metadata') % Response(200)
    delete1 = router.delete('/api/track/test-track/metadata/stale1') % Response(200)
    delete2 = router.delete('/api/track/test-track/metadata/stale2') % Response(200)
    unrelated = router.delete('/api/track/test-track/metadata/unrelated') % Response(500)
    mapping = await finish(cm.compact_make_artist_to_meta(db, {'Alice': 'New'}))
    await finish(mapping.apply(track(a, unknown), lambda _track, _reasons: True))
    assert len(db.queue) == 1
    assert isinstance(db.queue[0], _ParallelActionsBundle)
    assert len(db.queue[0].bundles) == 3
    assert not add.called and not delete1.called  # Only queued until commit.
    await finish(db.commit())
    assert add.called and delete1.called and delete2.called
    assert not unrelated.called


async def test_only_deletions_and_no_changes(
    db: DBClient | AsyncDBClient,
    router: Router,
) -> None:
    a, unknown = artist('Alice'), artist('Unknown')
    mock_search(router, [a])
    mock_metadata(router, [{'id': 'stale', 'type': 2, 'key': 'Character', 'value': 'Old'}])
    delete = router.delete('/api/track/test-track/metadata/stale') % Response(200)
    mapping = await finish(cm.compact_make_artist_to_meta(db, {'Alice': 'Old'}))
    await finish(mapping.apply(track(a)))
    assert not db.queue
    await finish(mapping.apply(track(unknown), lambda _track, _reasons: True))
    assert isinstance(db.queue[0], _ParallelActionsBundle)
    assert len(db.queue[0].bundles) == 1
    await finish(db.commit())
    assert delete.called


async def test_off_vocal_skips_queries_and_callback(
    db: DBClient | AsyncDBClient,
    router: Router,
) -> None:
    mapping = await finish(cm.compact_make_artist_to_meta(db, {}))
    get_meta = mock_metadata(router)
    await finish(mapping.apply(evolve(track(artist('Unknown')), type_id=1), lambda _track, _reasons: pytest.fail()))
    assert not get_meta.called and not db.queue


async def test_disambiguation_and_pagination(
    db: DBClient | AsyncDBClient,
    router: Router,
) -> None:
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
async def test_matching_errors(
    db: DBClient | AsyncDBClient,
    router: Router,
    definitions: cm.ArtistDict,
    error: str,
) -> None:
    samples = [artist('Alice', disambiguation='one')]
    if error == '2 artists found':
        samples.append(artist('Alice', disambiguation='two'))
    mock_search(router, samples)
    with pytest.raises(AMQCSLError, match=error):
        await finish(cm.compact_make_artist_to_meta(db, definitions, ['all']))


async def test_query_limit(db: DBClient | AsyncDBClient, router: Router) -> None:
    db.max_query_size = 1
    mock_search(router, [artist('Alice'), artist('Bob')])
    with pytest.raises(QueryError, match='max query size'):
        await finish(cm.compact_make_artist_to_meta(db, {}, ['all']))


async def test_global_searches_run_in_parallel(
    aclient: AsyncDBClient,
    router: Router,
) -> None:
    arrived: set[str] = set()
    both_started = asyncio.Event()

    async def search(req: Request) -> Response:
        arrived.add(req.url.params['searchTerm'])
        if len(arrived) == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), timeout=2)
        return Response(200, json={'artists': [], 'count': 0})

    route = router.get('/api/artists').mock(side_effect=search)
    mapping = await cm.compact_make_artist_to_meta(aclient, {}, ['one', 'two', 'one'])
    assert not mapping.metadata
    assert route.call_count == 2


async def test_concurrent_apply_shares_exclusion(
    aclient: AsyncDBClient,
    router: Router,
) -> None:
    mapping = await cm.compact_make_artist_to_meta(aclient, {})
    mock_metadata(router)
    calls: list[str] = []

    def should_exclude(t: CSLTrack, reasons: Sequence[cm.Reason]) -> bool:
        calls.append(t.id)
        return True

    await asyncio.gather(*(mapping.apply(track(artist('Unknown'), track_id=str(i)), should_exclude) for i in range(3)))
    assert len(calls) == 1


async def test_cached_group_can_be_used_as_member_without_recursion(
    db: DBClient | AsyncDBClient,
    router: Router,
) -> None:
    a, nested, parent = artist('Alice'), artist('Nested', group=True), artist('Parent', group=True)
    mock_search(router, [a])
    nested_query = router.get('/api/artist/Nested') % Response(200, json=group_details(nested, [a]))
    parent_query = router.get('/api/artist/Parent') % Response(200, json=group_details(parent, [nested]))
    mock_metadata(router)
    mapping = await finish(cm.compact_make_artist_to_meta(db, {'Alice': 'A'}))
    await finish(mapping.apply(track(nested)))
    await finish(mapping.apply(track(parent, track_id='parent-track')))
    assert nested_query.call_count == 1 and parent_query.call_count == 1
    assert mapping.metadata[CSLArtistSample.from_json(parent)] == mapping.metadata[CSLArtistSample.from_json(a)]


async def test_full_artist_credit_uses_existing_group_details(
    db: DBClient | AsyncDBClient,
    router: Router,
) -> None:
    a, g = artist('Alice'), artist('Group', group=True)
    mock_search(router, [a])
    get_group = router.get('/api/artist/Group') % Response(500)
    mock_metadata(router)
    mapping = await finish(cm.compact_make_artist_to_meta(db, {'Alice': 'A'}))
    t = track(g)
    credit = evolve(t.artist_credits[0], artist=CSLArtist.from_json(group_details(g, [a])))
    await finish(mapping.apply(evolve(t, artist_credits=[credit])))
    assert not get_group.called
    assert CSLArtistSample.from_json(g) in mapping.metadata


async def test_group_queries_for_one_track_run_in_parallel(
    aclient: AsyncDBClient,
    router: Router,
) -> None:
    a, g, h = artist('Alice'), artist('Group', group=True), artist('OtherGroup', group=True)
    mock_search(router, [a])
    arrived: set[str] = set()
    both_started = asyncio.Event()

    async def get_group(req: Request) -> Response:
        name = req.url.path.rsplit('/', 1)[-1]
        arrived.add(name)
        if len(arrived) == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), timeout=2)
        return Response(200, json=group_details(g if name == 'Group' else h, [a]))

    router.get(url__regex=r'/api/artist/[^/]+').mock(side_effect=get_group)
    mock_metadata(router)
    mapping = await cm.compact_make_artist_to_meta(aclient, {'Alice': 'A'})
    await mapping.apply(track(g, h))
    assert len(mapping.metadata) == 3


async def test_metadata_bundle_handles_multiple_adds_deletes_and_noop(
    db: DBClient | AsyncDBClient,
    router: Router,
) -> None:
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


async def test_mapping_methods_delegate_to_metadata(
    db: DBClient | AsyncDBClient,
    router: Router,
) -> None:
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


async def test_all_unfound_names_are_reported_together(
    db: DBClient | AsyncDBClient,
    router: Router,
) -> None:
    search = mock_search(router, [artist('Alice')])
    with pytest.raises(AMQCSLError) as error:
        await finish(
            cm.compact_make_artist_to_meta(db, {'Missing one': 'X', 'Alice': 'A', 'Missing two': 'Y'}, ['all'])
        )
    assert 'Missing one' in str(error.value)
    assert 'Missing two' in str(error.value)
    assert search.call_count == 3


async def test_search_phase_logging(
    db: DBClient | AsyncDBClient,
    router: Router,
    caplog: pytest.LogCaptureFixture,
) -> None:
    mock_search(router, [artist('Alice')])
    with caplog.at_level('INFO', logger='amqcsl.workflows.character_metadata'):
        await finish(cm.compact_make_artist_to_meta(db, {'Alice': 'A'}, ['no results']))
    assert 'Searching phrases for artists' in caplog.messages
    assert 'Searching for artists by name directly' in caplog.messages


@pytest.mark.parametrize('factory', ['compact', 'normal', 'class'])
@pytest.mark.parametrize('global_search', [False, True])
async def test_initial_exclusions_override_metadata_and_apply_across_tracks(
    db: DBClient | AsyncDBClient,
    router: Router,
    factory: str,
    global_search: bool,
) -> None:
    a, excluded = artist('Alice'), artist('Ignored')
    search = mock_search(router, [a, excluded])
    mock_metadata(router)
    add = router.post(url__regex=r'/api/track/[^/]+/metadata') % Response(200)
    phrases = ['all'] if global_search else []
    definitions: cm.ArtistDict = {'Alice': 'A', 'Ignored': 'IgnoredCharacter'}
    if factory == 'compact':
        mapping = await finish(cm.compact_make_artist_to_meta(db, definitions, phrases, exclude=['Ignored', 'Ignored']))
    elif factory == 'normal':
        mapping = await finish(
            cm.make_artist_to_meta(
                db, {'A': 'A', 'IgnoredCharacter': 'IgnoredCharacter'}, definitions, phrases, exclude=['Ignored']
            )
        )
    else:
        if isinstance(db, DBClient):
            mapping = cm.SyncArtistToMeta.create(db, definitions, phrases, exclude=['Ignored'])
        else:
            mapping = await cm.AsyncArtistToMeta.create(db, definitions, phrases, exclude=['Ignored'])
    assert mapping.excluded_artists == {'Ignored'}
    assert CSLArtistSample.from_json(excluded) not in mapping
    assert search.call_count == (1 if global_search else 2)
    for idx in range(2):
        await finish(mapping.apply(track(a, excluded, track_id=str(idx)), lambda _track, _reasons: pytest.fail()))
    await finish(db.commit())
    assert add.call_count == 2
    for call in cast(Sequence[Call], add.calls):
        assert [meta['value'] for meta in json.loads(call.request.content)['extraMetadatas']] == ['A']


@pytest.mark.parametrize('key_kind', ['name', 'tuple', 'artist_name'])
async def test_exclude_only_mapping_resolves_names_and_skips_groups(
    db: DBClient | AsyncDBClient,
    router: Router,
    key_kind: str,
) -> None:
    g = artist('Group', group=True, disambiguation='one')
    other = artist('Group', group=True, disambiguation='two')
    samples = [g] if key_kind == 'name' else [g, other]
    mock_search(router, samples)
    group_query = router.get(url__regex=r'/api/artist/[^/]+') % Response(500)
    mock_metadata(router, [{'id': 'stale', 'type': 2, 'key': 'Character', 'value': 'Old'}])
    delete = router.delete('/api/track/test-track/metadata/stale') % Response(200)
    keys: dict[str, cm.ArtistKey] = {
        'name': 'Group',
        'tuple': ('Group', 'one'),
        'artist_name': cm.ArtistName('Group', original_name='Group', disambiguation='one'),
    }
    mapping = await finish(cm.compact_make_artist_to_meta(db, {}, exclude=[keys[key_kind]]))
    assert mapping.excluded_artists == {str(g['id'])}
    assert not mapping.metadata
    await finish(mapping.apply(track(g), lambda _track, _reasons: pytest.fail()))
    await finish(db.commit())
    assert delete.called and not group_query.called


@pytest.mark.parametrize('all_excluded', [False, True])
@pytest.mark.parametrize('initial', [False, True])
async def test_group_members_respect_initial_and_callback_exclusions(
    db: DBClient | AsyncDBClient,
    router: Router,
    all_excluded: bool,
    initial: bool,
) -> None:
    a, excluded, g = artist('Alice'), artist('Ignored', group=True), artist('Group', group=True)
    mock_search(router, [a, excluded])
    members = [excluded] if all_excluded else [a, excluded]
    get_group = router.get('/api/artist/Group') % Response(200, json=group_details(g, members))
    nested = router.get('/api/artist/Ignored') % Response(200, json=group_details(excluded, []))
    mock_metadata(router)
    mapping = await finish(cm.compact_make_artist_to_meta(db, {'Alice': 'A'}, exclude=['Ignored'] if initial else []))
    if not initial:
        await finish(mapping.apply(track(excluded), lambda _track, _reasons: True))
    await finish(mapping.apply(track(g), lambda _track, _reasons: pytest.fail('Excluded member reported missing')))
    expected: Sequence[ExtraMetadata] = [] if all_excluded else mapping[CSLArtistSample.from_json(a)]
    assert mapping[CSLArtistSample.from_json(g)] == expected
    assert get_group.call_count == 1
    assert nested.call_count == int(not initial)
    assert len(db.queue) == int(not all_excluded)


@pytest.mark.parametrize('ambiguous', [False, True])
async def test_excluded_names_must_resolve_uniquely(
    db: DBClient | AsyncDBClient,
    router: Router,
    ambiguous: bool,
) -> None:
    mock_search(
        router, [artist('Ignored', disambiguation='one'), artist('Ignored', disambiguation='two')] if ambiguous else []
    )
    with pytest.raises(AMQCSLError, match='2 artists found' if ambiguous else 'Could not find artists: Ignored'):
        await finish(cm.compact_make_artist_to_meta(db, {}, exclude=['Ignored']))
    assert not db.queue


async def test_missing_exclusions_are_reported_with_missing_metadata_names(
    db: DBClient | AsyncDBClient,
    router: Router,
) -> None:
    search = mock_search(router, [])
    with pytest.raises(AMQCSLError) as error:
        await finish(cm.compact_make_artist_to_meta(db, {'Missing': 'A'}, exclude=['Ignored']))
    assert 'Missing' in str(error.value) and 'Ignored' in str(error.value)
    assert search.call_count == 2
