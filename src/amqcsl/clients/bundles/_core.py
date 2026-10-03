from collections.abc import Generator, Iterable
from typing import Protocol, override

import niquests
import rich.repr
from attrs import frozen

type httpClient = niquests.Session | niquests.AsyncSession  # noqa: PYI042 -- use the maintainer-requested alias spelling

type SingleVendor[R] = Generator[niquests.PreparedRequest, niquests.Response, R]
type MultiVendor[R] = Generator[Iterable[niquests.PreparedRequest], Iterable[niquests.Response], R]
type MixedVendor[R] = Generator[
    niquests.PreparedRequest | Iterable[niquests.PreparedRequest], niquests.Response | Iterable[niquests.Response], R
]
type Vendor[R] = SingleVendor[R] | MultiVendor[R] | MixedVendor[R]


def materialize[T](items: Iterable[T]) -> list[T]:
    """Snapshot iterable inputs that request builders may read more than once."""
    return [*items]


class Bundle[R](Protocol):
    # httpClient provides session state for preparing requests
    # Do not use to send actual requests, since it should work for both sync
    # and async clients
    def vendor(self, client: httpClient) -> Vendor[R]: ...
    def __rich_repr__(self) -> rich.repr.Result: ...


@frozen
class Items[T]:
    """An explicit stream event whose values are consumed before requesting more work."""

    values: Iterable[T]


type StreamingVendor[T] = Generator[
    niquests.PreparedRequest | Iterable[niquests.PreparedRequest] | Items[T],
    niquests.Response | Iterable[niquests.Response] | None,
    None,
]


class StreamingBundle[T](Protocol):
    """HTTP work that also yields items incrementally through Items events."""

    def vendor(self, client: httpClient) -> StreamingVendor[T]: ...
    def __rich_repr__(self) -> rich.repr.Result: ...

    def collect(self) -> Bundle[list[T]]:
        """Adapt this stream to an ordinary bundle returning all its items."""
        return CollectBundle(self)


@frozen
class CollectBundle[T](Bundle[list[T]]):
    """Collect any streaming bundle while forwarding its ordinary HTTP requests."""

    stream: StreamingBundle[T]

    @override
    def vendor(self, client: httpClient) -> MixedVendor[list[T]]:
        vendor = self.stream.vendor(client)
        items: list[T] = []
        reply: niquests.Response | Iterable[niquests.Response] | None = None
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
