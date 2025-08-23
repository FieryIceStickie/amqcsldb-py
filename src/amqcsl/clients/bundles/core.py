from collections.abc import Generator
from typing import Iterable, Protocol

import httpx
import rich.repr

type httpxClient = httpx.Client | httpx.AsyncClient

type SingleVendor[R] = Generator[httpx.Request, httpx.Response, R]
type MultiVendor[R] = Generator[Iterable[httpx.Request], Iterable[httpx.Response], R]
type Vendor[R] = SingleVendor[R] | MultiVendor[R]


class Bundle[R](Protocol):
    # httpxClient is used for build_request and other client methods
    # Do not use to send actual requests, since it should work for both sync
    # and async clients
    def vendor(self, client: httpxClient) -> Vendor[R]: ...
    def __rich_repr__(self) -> rich.repr.Result: ...
