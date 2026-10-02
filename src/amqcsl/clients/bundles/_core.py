from collections.abc import Generator
from typing import Iterable, Protocol, override

import httpx
import rich.repr
from attrs import frozen

type httpxClient = httpx.Client | httpx.AsyncClient

type SingleVendor[R] = Generator[httpx.Request, httpx.Response, R]
type MultiVendor[R] = Generator[Iterable[httpx.Request], Iterable[httpx.Response], R]
type MixedVendor[R] = Generator[httpx.Request | Iterable[httpx.Request], httpx.Response | Iterable[httpx.Response], R]
type Vendor[R] = SingleVendor[R] | MultiVendor[R] | MixedVendor[R]


def materialize[T](items: Iterable[T]) -> list[T]:
    """Snapshot iterable inputs that request builders may read more than once."""
    return [*items]


class Bundle[R](Protocol):
    # httpxClient is used for build_request and other client methods
    # Do not use to send actual requests, since it should work for both sync
    # and async clients
    def vendor(self, client: httpxClient) -> Vendor[R]: ...
    def __rich_repr__(self) -> rich.repr.Result: ...


@frozen
class Items[T]:
    """An explicit stream event whose values are consumed before requesting more work."""

    values: Iterable[T]


type StreamingVendor[T] = Generator[
    httpx.Request | Iterable[httpx.Request] | Items[T],
    httpx.Response | Iterable[httpx.Response] | None,
    None,
]


class StreamingBundle[T](Protocol):
    """HTTP work that also yields items incrementally through Items events."""

    def vendor(self, client: httpxClient) -> StreamingVendor[T]: ...
    def __rich_repr__(self) -> rich.repr.Result: ...

    def collect(self) -> Bundle[list[T]]:
        """Adapt this stream to an ordinary bundle returning all its items."""
        return CollectBundle(self)


@frozen
class CollectBundle[T](Bundle[list[T]]):
    """Collect any streaming bundle while forwarding its ordinary HTTP requests."""

    stream: StreamingBundle[T]

    @override
    def vendor(self, client: httpxClient) -> MixedVendor[list[T]]:
        vendor = self.stream.vendor(client)
        items: list[T] = []
        reply: httpx.Response | Iterable[httpx.Response] | None = None
        try:
            while True:
                try:
                    event = vendor.send(reply)
                except StopIteration:
                    return items
                match event:
                    case Items(values=values):
                        items.extend(values)
                        reply = None
                    case _:
                        reply = yield event
        finally:
            vendor.close()

    @override
    def __rich_repr__(self) -> rich.repr.Result:
        yield 'stream', self.stream
