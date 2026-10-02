from collections.abc import AsyncIterable, AsyncIterator, Awaitable, Iterable, Iterator
from typing import cast

from pathlib import Path
import json

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
