import json
from collections.abc import AsyncIterable, AsyncIterator, Awaitable, Callable, Iterable, Iterator
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qsl, urlsplit

from niquests import PreparedRequest, Request, Response
from niquests_mock import build_response

resources = Path(__file__).parent / 'resources'


def load(name: str):
    with open(resources / f'{name}.json', 'r') as file:
        return json.loads(file.read())


async def finish[T](result: T | Awaitable[T]) -> T:
    """Await an async client result or return its synchronous counterpart."""
    return await cast(Awaitable[T], result) if isinstance(result, Awaitable) else cast(T, result)


async def collect[T](items: Iterable[T] | AsyncIterable[T]) -> list[T]:
    """Consume either public iterator form for API result assertions."""
    if isinstance(items, AsyncIterable):
        return [item async for item in items]
    return [*items]


async def first[T](items: Iterator[T] | AsyncIterator[T]) -> T:
    """Retrieve the first result without collecting the rest of the query."""
    if isinstance(items, AsyncIterator):
        return await anext(items)
    return next(items)


def mock_response(status_code: int = 200, **kwargs: Any) -> Response:
    """Build a niquests response for route registration or callback results."""
    return build_response(Request('GET', 'https://example.test').prepare(), status_code=status_code, **kwargs)


def query_params(request: PreparedRequest) -> dict[str, str]:
    """Expose prepared request query values for endpoint callback assertions."""
    return dict(parse_qsl(urlsplit(request_url(request)).query, keep_blank_values=True))


def json_fields(expected: dict[str, Any]) -> Callable[[Any], bool]:
    """Match selected JSON fields, including indexed array values."""

    def matches(actual: Any) -> bool:
        for path, value in expected.items():
            current = actual
            for key in path.split('__'):
                current = current[int(key)] if key.isdigit() else current[key]
            if current != value:
                return False
        return True

    return matches


def request_url(request: PreparedRequest) -> str:
    """Return the concrete URL of a prepared request."""
    assert request.url is not None
    return request.url


def request_body(request: PreparedRequest) -> bytes:
    """Return the buffered JSON or multipart body used in these tests."""
    body = request.body
    if isinstance(body, str):
        return body.encode('utf-8')
    assert isinstance(body, bytes)
    return body
