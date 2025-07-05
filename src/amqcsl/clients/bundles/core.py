from collections.abc import Generator, Iterator
from typing import Any, Iterable, Protocol

import httpx

type httpxClient = httpx.Client | httpx.AsyncClient
# Due to limitations of Python's type system, vendors will need to be one or the other
type SingleVendor[R] = Generator[httpx.Request, httpx.Response, R]
type MultiVendor[R] = Generator[Iterable[httpx.Request], Iterable[httpx.Response], R]
type Vendor[R] = SingleVendor[R] | MultiVendor[R]

type RichReprRtn = Iterator[str | tuple[str, Any] | tuple[str, Any, Any]]


class Bundle[R](Protocol):
    # httpxClient is used for build_request and other client methods
    # Do not use to send actual requests, since it should work for both sync
    # and async clients
    def vendor(self, client: httpxClient) -> Vendor[R]: ...
    def __rich_repr__(self) -> RichReprRtn: ...
