from collections.abc import Iterable
from typing import cast, override

import httpx
import rich.repr
from attrs import field, frozen

from ._core import Bundle, MultiVendor, Vendor, httpxClient, materialize

type Replies = dict[int, httpx.Response | list[httpx.Response] | None]
type RequestSpans = dict[int, tuple[int, int, bool]]


@frozen
class ParallelBundle[R](Bundle[list[R]]):
    """Run child vendors together, including vendors with multiple request rounds."""

    bundles: list[Bundle[R]] = field(converter=materialize)

    @staticmethod
    def _batch(
        vendors: dict[int, Vendor[R]],
        replies: Replies,
        results: dict[int, R],
    ) -> tuple[list[httpx.Request], RequestSpans]:
        """Advance active vendors and remember where each vendor's responses belong."""
        requests: list[httpx.Request] = []
        spans: RequestSpans = {}
        for idx, vendor in [*vendors.items()]:
            try:
                outgoing = vendor.send(replies[idx])  # type: ignore[reportArgumentType]
            except StopIteration as done:
                results[idx] = cast(R, done.value)
                del vendors[idx]
                continue
            start = len(requests)
            single = isinstance(outgoing, httpx.Request)
            if isinstance(outgoing, httpx.Request):
                requests.append(outgoing)
            else:
                requests.extend(outgoing)
            spans[idx] = (start, len(requests), single)
        return requests, spans

    @override
    def vendor(self, client: httpxClient) -> MultiVendor[list[R]]:
        vendors = {idx: bundle.vendor(client) for idx, bundle in enumerate(self.bundles)}
        replies: Replies = dict.fromkeys(vendors)
        results: dict[int, R] = {}
        while vendors:
            requests, spans = self._batch(vendors, replies, results)
            responses = [*(yield requests)] if requests else []
            if len(responses) != len(requests):
                raise ValueError('Response count does not match request count')
            for idx, (start, end, single) in spans.items():
                replies[idx] = responses[start] if single else responses[start:end]
        # Completion order can differ from input order.
        return [results[idx] for idx in sorted(results)]

    @override
    def __rich_repr__(self) -> rich.repr.Result:
        yield 'bundles', self.bundles


@frozen
class _ParallelActionsBundle(Bundle[None]):
    parallel: ParallelBundle[None]

    @property
    def bundles(self) -> list[Bundle[None]]:
        return self.parallel.bundles

    @override
    def vendor(self, client: httpxClient) -> MultiVendor[None]:
        yield from self.parallel.vendor(client)

    @override
    def __rich_repr__(self) -> rich.repr.Result:
        yield 'bundles', self.bundles


def parallel_actions(bundles: Iterable[Bundle[None]]) -> Bundle[None]:
    """Compose queued actions in parallel, discarding their individual return values."""
    return _ParallelActionsBundle(ParallelBundle(bundles))
