# These tests exercise the internal streaming driver directly.
# pyright: reportPrivateUsage=false
from collections.abc import Iterator

import niquests
import pytest
import rich.repr
from attrs import frozen
from helpers import collect, finish, first, mock_response
from niquests_mock import MockRouter as Router

from amqcsl import AsyncDBClient, DBClient
from amqcsl.clients._http_utils import build_request
from amqcsl.clients.bundles import Items, ParallelBundle, StreamingBundle, StreamingVendor
from amqcsl.clients.bundles._core import httpClient
from amqcsl.exceptions import QueryError


@frozen
class NumberStream(StreamingBundle[int]):
    trace: list[str]

    def vendor(self, client: httpClient) -> StreamingVendor[int]:
        def first_items() -> Iterator[int]:
            self.trace.append('converted')
            yield 1

        try:
            self.trace.append('started')
            reply = yield Items(first_items())
            assert reply is None
            response = yield build_request(client, 'GET', '/stream/one')
            assert isinstance(response, niquests.Response)
            reply = yield Items([response.json()])
            assert reply is None
            responses = yield (build_request(client, 'GET', f'/stream/{idx}') for idx in [3, 4])
            assert responses is not None and not isinstance(responses, niquests.Response)
            reply = yield Items(response.json() for response in responses)
            assert reply is None
            # Final item events require no special draining at generator completion.
            yield Items([5])
        finally:
            self.trace.append('closed')

    def __rich_repr__(self) -> rich.repr.Result:
        yield 'trace', self.trace


def routes(router: Router) -> None:
    for name, value in [('one', 2), ('3', 3), ('4', 4)]:
        _ = router.get(path=f'/stream/{name}').mock(return_value=mock_response(200, json=value))


pytestmark = pytest.mark.asyncio


async def test_stream_handles_items_single_requests_batches_and_tail(
    client: DBClient | AsyncDBClient,
    router: Router,
) -> None:
    routes(router)
    trace: list[str] = []
    items = client._process_stream(NumberStream(trace))
    assert not trace
    assert await first(items) == 1
    assert trace == ['started', 'converted']
    assert await collect(items) == [2, 3, 4, 5]
    assert trace[-1] == 'closed'


async def test_any_stream_can_be_collected_in_parallel(
    client: DBClient | AsyncDBClient,
    router: Router,
) -> None:
    routes(router)
    streams = [NumberStream([]), NumberStream([])]
    bundle = ParallelBundle(stream.collect() for stream in streams)
    assert await finish(client.process(bundle)) == [[1, 2, 3, 4, 5], [1, 2, 3, 4, 5]]
    assert all(stream.trace[-1] == 'closed' for stream in streams)


async def test_early_stream_close_does_not_request_more(
    client: DBClient | AsyncDBClient,
    router: Router,
) -> None:
    route = router.get(path='/stream/one').mock(return_value=mock_response(500))
    trace: list[str] = []
    match client:
        case DBClient():
            items = client._process_stream(NumberStream(trace))
            assert next(items) == 1
            items.close()
        case AsyncDBClient():
            async_items = client._process_stream(NumberStream(trace))
            assert await anext(async_items) == 1
            await async_items.aclose()
    assert trace[-1] == 'closed'
    assert not route.called


async def test_ordinary_process_rejects_item_events(
    client: DBClient | AsyncDBClient,
) -> None:
    stream = NumberStream([])
    with pytest.raises(TypeError, match='collect'):
        match client:
            case DBClient():
                client.process(stream)  # pyright: ignore[reportArgumentType] -- exercise runtime misuse guard
            case AsyncDBClient():
                await client.process(stream)  # pyright: ignore[reportArgumentType] -- exercise runtime misuse guard
    assert stream.trace[-1] == 'closed'


async def test_collection_closes_stream_when_item_conversion_fails(
    client: DBClient | AsyncDBClient,
) -> None:
    class BrokenStream(NumberStream):
        def vendor(self, client: httpClient) -> StreamingVendor[int]:
            try:

                def broken_items() -> Iterator[int]:
                    yield 1
                    raise QueryError('Bad item')

                yield Items(broken_items())
            finally:
                self.trace.append('closed')

    stream = BrokenStream([])
    with pytest.raises(QueryError, match='Bad item'):
        await finish(client.process(stream.collect()))
    assert stream.trace[-1] == 'closed'
