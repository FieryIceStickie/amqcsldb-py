from collections.abc import Generator
from typing import cast

import niquests
import pytest
import rich.repr
from attrs import frozen
from helpers import finish, mock_response
from niquests_mock import MockRouter as Router

from amqcsl import AsyncDBClient, DBClient
from amqcsl.bundles import (
    AsyncPageStrategy,
    IterArtistsBundle,
    MixedVendor,
    PageStrategy,
    ParallelBundle,
    SyncPageStrategy,
)
from amqcsl.bundles._core import SingleVendor, httpClient
from amqcsl.clients._http_utils import build_request
from amqcsl.objects import (
    CSLArtistSample,
    from_json,
)
from amqcsl.objects._json_types import JSONArtistSample, JSONType


@frozen
class RoundBundle:
    idx: int
    rounds: int

    def vendor(self, client: httpClient) -> MixedVendor[int]:
        for step in range(self.rounds):
            req = build_request(client, 'GET', f'/bundle/{self.idx}/{step}')
            if step % 2:
                responses = yield (request for request in [req, req])
                assert not isinstance(responses, niquests.Response)
                assert [res.json() for res in responses] == [self.idx, self.idx]
            else:
                response = yield req
                assert isinstance(response, niquests.Response)
                assert response.json() == self.idx
        return self.idx

    def __rich_repr__(self) -> rich.repr.Result:
        yield 'idx', self.idx


@pytest.mark.asyncio
async def test_parallel_iterable_inputs_preserve_result_order(
    client: DBClient | AsyncDBClient,
    router: Router,
) -> None:
    for idx, rounds in [(10, 3), (20, 0), (30, 1), (40, 2)]:
        for step in range(rounds):
            _ = router.get(path=f'/bundle/{idx}/{step}').mock(return_value=mock_response(200, json=idx))
    consumed: list[int] = []

    def bundles() -> Generator[RoundBundle]:
        for idx, rounds in [(10, 3), (20, 0), (30, 1), (40, 2)]:
            consumed.append(idx)
            yield RoundBundle(idx, rounds)

    bundle = ParallelBundle(bundles())
    assert consumed == [10, 20, 30, 40]
    assert await finish(client.process(bundle)) == [10, 20, 30, 40]
    assert consumed == [10, 20, 30, 40]


def test_parallel_response_count_mismatch():
    with niquests.Session(base_url='https://example.test') as client:
        vendor = ParallelBundle([RoundBundle(10, 1)]).vendor(client)
        assert len([*next(vendor)]) == 1
        with pytest.raises(ValueError, match='Response count'):
            vendor.send([])


@pytest.mark.parametrize('fail', [False, True])
def test_parallel_closes_unfinished_child_vendors(fail: bool) -> None:
    closed: list[int] = []

    def child(client: httpClient, idx: int) -> SingleVendor[int]:
        try:
            yield build_request(client, 'GET', f'/bundle/{idx}')
            return idx
        finally:
            closed.append(idx)

    @frozen
    class HeldVendorBundle:
        held: SingleVendor[int]

        def vendor(self, client: httpClient) -> SingleVendor[int]:
            return self.held

        def __rich_repr__(self) -> rich.repr.Result:
            yield from ()

    with niquests.Session(base_url='https://example.test') as client:
        children = [child(client, idx) for idx in range(2)]
        vendor = ParallelBundle([HeldVendorBundle(held) for held in children]).vendor(client)
        assert len([*next(vendor)]) == 2
        if fail:
            with pytest.raises(ValueError, match='Response count'):
                vendor.send([])
        else:
            vendor.close()
        assert closed == [0, 1]


@pytest.mark.parametrize('strategy', [SyncPageStrategy, AsyncPageStrategy])
@pytest.mark.asyncio
async def test_page_collection_supports_both_scheduling_strategies(
    strategy: type[PageStrategy],
    client: DBClient | AsyncDBClient,
    router: Router,
) -> None:
    samples: list[dict[str, JSONType]] = [
        {'id': str(idx), 'name': f'Artist {idx}', 'originalName': '', 'disambiguation': None, 'type': 1}
        for idx in range(3)
    ]
    for idx, sample in enumerate(samples):
        _ = router.get('/api/artists', params={'skip': idx, 'take': 1}).mock(
            return_value=mock_response(
                200,
                json={'count': 3, 'artists': [sample]},
            )
        )
    query = IterArtistsBundle(1, 10, 1, strategy(), 'test')
    assert await finish(client.process(query.collect())) == [
        from_json(cast(JSONArtistSample, sample), CSLArtistSample) for sample in samples
    ]
