import asyncio
import json
from typing import cast

import pytest
from helpers import load
from httpx import Request, Response
from respx import Router

from amqcsl import AsyncDBClient, DBClient
from amqcsl.exceptions import QueryError
from amqcsl.objects._json_types import JSONType

type Query = tuple[str, list[dict[str, JSONType]]]


@pytest.fixture(params=['tracks', 'songs', 'artists'])
def query(request: pytest.FixtureRequest) -> Query:
    kind = request.param
    return kind, cast(list[dict[str, JSONType]], load(f'idolypride/{kind}'))[:5]


def mock_pages(
    router: Router,
    kind: str,
    samples: list[dict[str, JSONType]],
) -> list[int]:
    calls: list[int] = []

    def page(req: Request) -> Response:
        params = json.loads(req.content) if kind == 'tracks' else req.url.params
        skip, take = int(params['skip']), int(params['take'])
        calls.append(skip)
        return Response(200, json={'count': len(samples), kind: samples[skip : skip + take]})

    route = router.post(f'/api/{kind}') if kind == 'tracks' else router.get(f'/api/{kind}')
    route.mock(side_effect=page)
    return calls


@pytest.mark.parametrize('client', ['sync'], indirect=True)
def test_sync_pagination_stays_lazy_and_yields_final_page(
    client: DBClient | AsyncDBClient,
    router: Router,
    query: Query,
) -> None:
    assert isinstance(client, DBClient)
    kind, samples = query
    calls = mock_pages(router, kind, samples)
    items = getattr(client, f'iter_{kind}')('test', batch_size=2)
    assert not calls
    assert next(items).id == samples[0]['id']
    assert calls == [0]
    assert next(items).id == samples[1]['id']
    assert calls == [0]
    assert [item.id for item in items] == [sample['id'] for sample in samples[2:]]
    assert calls == [0, 2, 4]


@pytest.mark.parametrize('client', ['sync'], indirect=True)
def test_sync_pagination_can_stop_after_first_item(
    client: DBClient | AsyncDBClient,
    router: Router,
    query: Query,
) -> None:
    assert isinstance(client, DBClient)
    kind, samples = query
    calls = mock_pages(router, kind, samples)
    items = getattr(client, f'iter_{kind}')('test', batch_size=2)
    assert next(items).id == samples[0]['id']
    items.close()
    assert calls == [0]


@pytest.mark.asyncio
@pytest.mark.parametrize('client', ['async'], indirect=True)
async def test_async_pagination_first_page_then_parallel_ordered_pages(
    client: DBClient | AsyncDBClient,
    router: Router,
    query: Query,
) -> None:
    assert isinstance(client, AsyncDBClient)
    kind, samples = query
    calls: list[int] = []
    both_started = asyncio.Event()
    last_finished = asyncio.Event()

    async def page(req: Request) -> Response:
        params = json.loads(req.content) if kind == 'tracks' else req.url.params
        skip, take = int(params['skip']), int(params['take'])
        calls.append(skip)
        if skip:
            if 2 in calls and 4 in calls:
                both_started.set()
            await asyncio.wait_for(both_started.wait(), timeout=2)
            if skip == 2:
                await asyncio.wait_for(last_finished.wait(), timeout=2)
            else:
                last_finished.set()
        return Response(200, json={'count': len(samples), kind: samples[skip : skip + take]})

    route = router.post(f'/api/{kind}') if kind == 'tracks' else router.get(f'/api/{kind}')
    route.mock(side_effect=page)
    items = getattr(client, f'iter_{kind}')('test', batch_size=2)
    assert not calls
    assert (await anext(items)).id == samples[0]['id']
    assert (await anext(items)).id == samples[1]['id']
    assert calls == [0]
    assert [item.id async for item in items] == [sample['id'] for sample in samples[2:]]
    assert calls == [0, 2, 4]


@pytest.mark.asyncio
@pytest.mark.parametrize('client', ['async'], indirect=True)
async def test_async_pagination_can_stop_after_first_item(
    client: DBClient | AsyncDBClient,
    router: Router,
    query: Query,
) -> None:
    assert isinstance(client, AsyncDBClient)
    kind, samples = query
    calls = mock_pages(router, kind, samples)
    items = getattr(client, f'iter_{kind}')('test', batch_size=2)
    assert (await anext(items)).id == samples[0]['id']
    await items.aclose()
    assert calls == [0]


@pytest.mark.parametrize('client', ['sync'], indirect=True)
def test_empty_sync_page_finishes(
    client: DBClient | AsyncDBClient,
    router: Router,
    query: Query,
) -> None:
    assert isinstance(client, DBClient)
    kind, _ = query
    calls = mock_pages(router, kind, [])
    assert [*getattr(client, f'iter_{kind}')('test')] == []
    assert calls == [0]


@pytest.mark.asyncio
@pytest.mark.parametrize('client', ['async'], indirect=True)
async def test_empty_async_page_finishes(
    client: DBClient | AsyncDBClient,
    router: Router,
    query: Query,
) -> None:
    assert isinstance(client, AsyncDBClient)
    kind, _ = query
    calls = mock_pages(router, kind, [])
    assert [item async for item in getattr(client, f'iter_{kind}')('test')] == []
    assert calls == [0]


@pytest.mark.parametrize('client', ['sync'], indirect=True)
def test_sync_query_limit_fails_before_next_page(
    client: DBClient | AsyncDBClient,
    router: Router,
    query: Query,
) -> None:
    assert isinstance(client, DBClient)
    kind, samples = query
    client.max_query_size = 1
    calls = mock_pages(router, kind, samples)
    with pytest.raises(QueryError, match='max query size'):
        next(getattr(client, f'iter_{kind}')('test', batch_size=1))
    assert calls == [0]


@pytest.mark.asyncio
@pytest.mark.parametrize('client', ['async'], indirect=True)
async def test_async_query_limit_fails_before_next_page(
    client: DBClient | AsyncDBClient,
    router: Router,
    query: Query,
) -> None:
    assert isinstance(client, AsyncDBClient)
    kind, samples = query
    client.max_query_size = 1
    calls = mock_pages(router, kind, samples)
    with pytest.raises(QueryError, match='max query size'):
        await anext(getattr(client, f'iter_{kind}')('test', batch_size=1))
    assert calls == [0]


@pytest.mark.asyncio
@pytest.mark.parametrize('client', ['async'], indirect=True)
async def test_async_page_request_failure_cancels_other_requests(
    client: DBClient | AsyncDBClient,
    router: Router,
) -> None:
    assert isinstance(client, AsyncDBClient)
    from httpx import ConnectError

    samples = load('idolypride/artists')[:5]
    sibling_started = asyncio.Event()
    sibling_cancelled = asyncio.Event()

    async def page(req: Request) -> Response:
        skip = int(req.url.params['skip'])
        if skip == 0:
            return Response(200, json={'count': 5, 'artists': samples[:2]})
        if skip == 2:
            await sibling_started.wait()
            raise ConnectError('Failed page request', request=req)
        sibling_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            sibling_cancelled.set()
            raise
        pytest.fail('Sibling request unexpectedly completed')

    router.get('/api/artists').mock(side_effect=page)
    items = client.iter_artists('test', batch_size=2)
    await anext(items)
    await anext(items)
    with pytest.raises(ExceptionGroup) as error:
        await asyncio.wait_for(anext(items), timeout=2)
    assert any(isinstance(exception, ConnectError) for exception in error.value.exceptions)
    assert sibling_cancelled.is_set()
