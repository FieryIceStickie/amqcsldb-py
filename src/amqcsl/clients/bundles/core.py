from collections.abc import Generator, Iterator
from typing import Any, Iterable, Protocol

import httpx

type httpxClient = httpx.Client | httpx.AsyncClient
type RichReprRtn = Iterator[str | tuple[str, Any] | tuple[str, Any, Any]]

type SingleVendor[R] = Generator[httpx.Request, httpx.Response, R]
type MultiVendor[R] = Generator[Iterable[httpx.Request], Iterable[httpx.Response], R]
type Vendor[R] = SingleVendor[R] | MultiVendor[R]


class Bundle[R](Protocol):
    # httpxClient is used for build_request and other client methods
    # Do not use to send actual requests, since it should work for both sync
    # and async clients
    def vendor(self, client: httpxClient) -> Vendor[R]: ...
    def __rich_repr__(self) -> RichReprRtn: ...
