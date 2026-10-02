from collections.abc import Generator

import httpx
import pytest
import rich.repr
from respx import Router
from attrs import frozen

from amqcsl import AsyncDBClient, DBClient
from amqcsl.clients.bundles import (
    AsyncPageStrategy,
    IterArtistsBundle,
    MixedVendor,
    ParallelBundle,
    PageStrategy,
    SyncPageStrategy,
)
from amqcsl.clients.bundles._core import httpxClient
from amqcsl.objects import CSLArtistSample
from amqcsl.objects._json_types import JSONType


@frozen
class RoundBundle:
    idx: int
    rounds: int

    def vendor(self, client: httpxClient) -> MixedVendor[int]:
        for step in range(self.rounds):
            req = client.build_request('GET', f'/bundle/{self.idx}/{step}')
            if step % 2:
                responses = yield (request for request in [req, req])
                assert not isinstance(responses, httpx.Response)
                assert [res.json() for res in responses] == [self.idx, self.idx]
            else:
                response = yield req
                assert isinstance(response, httpx.Response)
                assert response.json() == self.idx
        return self.idx

    def __rich_repr__(self) -> rich.repr.Result:
        yield 'idx', self.idx


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['sync', 'async'])
async def test_parallel_iterable_inputs_preserve_result_order(
    mode: str,
    client: DBClient,
    aclient: AsyncDBClient,
    router: Router,
) -> None:
    for idx, rounds in [(10, 3), (20, 0), (30, 1), (40, 2)]:
        for step in range(rounds):
            _ = router.get(f'/bundle/{idx}/{step}') % httpx.Response(200, json=idx)
    consumed: list[int] = []

    def bundles() -> Generator[RoundBundle]:
        for idx, rounds in [(10, 3), (20, 0), (30, 1), (40, 2)]:
            consumed.append(idx)
            yield RoundBundle(idx, rounds)

    bundle = ParallelBundle(bundles())
    assert consumed == [10, 20, 30, 40]
    if mode == 'sync':
        assert client.process(bundle) == [10, 20, 30, 40]
    else:
        assert await aclient.process(bundle) == [10, 20, 30, 40]
    assert consumed == [10, 20, 30, 40]


def test_parallel_response_count_mismatch():
    with httpx.Client(base_url='https://example.test') as client:
        vendor = ParallelBundle([RoundBundle(10, 1)]).vendor(client)
        assert len([*next(vendor)]) == 1
        with pytest.raises(ValueError, match='Response count'):
            vendor.send([])


@pytest.mark.parametrize('strategy', [SyncPageStrategy, AsyncPageStrategy])
def test_page_collection_supports_both_scheduling_strategies(
    strategy: type[PageStrategy],
    client: DBClient,
    router: Router,
) -> None:
    samples: list[dict[str, JSONType]] = [
        {'id': str(idx), 'name': f'Artist {idx}', 'originalName': '', 'disambiguation': None, 'type': 1}
        for idx in range(3)
    ]
    for idx, sample in enumerate(samples):
        _ = router.get('/api/artists', params={'skip': idx, 'take': 1}) % httpx.Response(
            200,
            json={'count': 3, 'artists': [sample]},
        )
    query = IterArtistsBundle(1, 10, 1, strategy(), 'test')
    assert client.process(query.collect()) == [CSLArtistSample.from_json(sample) for sample in samples]
