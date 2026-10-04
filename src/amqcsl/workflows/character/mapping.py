from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Iterable, Iterator, Sequence
from typing import Self, overload

from attrs import define, evolve, field

from amqcsl import AsyncDBClient, DBClient
from amqcsl.clients.bundles._core import Bundle
from amqcsl.clients.bundles._misc import GetMetadataBundle
from amqcsl.objects._db_types import CSLArtist, CSLArtistSample, CSLTrack, ExtraMetadata

from .bundles import ApplyArtistToMetaBundle, MakeArtistToMetaBundle
from .prompts import async_prompt_should_exclude, prompt_should_exclude
from .types import ArtistDict, ArtistKey, ArtistToMeta, AsyncShouldExclude, ExcludeDecision, ShouldExclude

type _ExclusionRequest = tuple[
    ApplyArtistToMetaBundle,
    Sequence[CSLArtist],
    asyncio.Future[Bundle[None] | None],
]


@define
class SyncArtistToMeta(ArtistToMeta):
    """Artist mapping that prepares metadata changes synchronously."""

    _client: DBClient = field(repr=False, eq=False)
    metadata: dict[CSLArtistSample, Sequence[ExtraMetadata]]
    excluded_artists: set[str] = field(factory=set[str])

    @classmethod
    def create(
        cls,
        client: DBClient,
        artists: ArtistDict,
        search_phrases: Sequence[str] = (),
        sep: str = ', ',
        exclude: Sequence[ArtistKey] = (),
    ) -> Self:
        metadata, excluded = client.process(
            MakeArtistToMetaBundle(
                artists,
                search_phrases,
                sep,
                client.max_batch_size,
                client.max_query_size,
                exclude,
            )
        )
        return cls(client, metadata, excluded)

    def apply(
        self,
        track: CSLTrack,
        should_exclude: ShouldExclude = prompt_should_exclude,
    ) -> Bundle[None] | None:
        """Return prepared track edits without enqueueing or committing them."""
        return self._client.process(
            ApplyArtistToMetaBundle(
                track,
                self.metadata,
                self.excluded_artists,
                should_exclude,
            )
        )

    def iter_edits(
        self,
        tracks: Iterable[CSLTrack],
        should_exclude: ShouldExclude = prompt_should_exclude,
    ) -> Iterator[Bundle[None]]:
        """Yield track edits without enqueueing or committing them."""
        for track in tracks:
            bundle = self.apply(track, should_exclude)
            if bundle is not None:
                yield bundle


@define
class AsyncArtistToMeta(ArtistToMeta):
    """Artist mapping that prepares metadata changes asynchronously."""

    _client: AsyncDBClient = field(repr=False, eq=False)
    metadata: dict[CSLArtistSample, Sequence[ExtraMetadata]]
    excluded_artists: set[str] = field(factory=set[str])
    _lock: asyncio.Lock = field(factory=asyncio.Lock, init=False, repr=False, eq=False)
    _prompt_lock: asyncio.Lock = field(factory=asyncio.Lock, init=False, repr=False, eq=False)

    @classmethod
    async def create(
        cls,
        client: AsyncDBClient,
        artists: ArtistDict,
        search_phrases: Sequence[str] = (),
        sep: str = ', ',
        exclude: Sequence[ArtistKey] = (),
    ) -> Self:
        metadata, excluded = await client.process(
            MakeArtistToMetaBundle(
                artists,
                search_phrases,
                sep,
                client.max_batch_size,
                client.max_query_size,
                exclude,
            )
        )
        return cls(client, metadata, excluded)

    async def _prepare(
        self,
        bundle: ApplyArtistToMetaBundle,
        fetched: Sequence[CSLArtist],
        should_exclude: AsyncShouldExclude,
    ) -> Bundle[None] | None:
        """Serialize decisions and recheck shared caches without holding locks across input."""
        async with self._lock:
            reasons, metas = bundle.analyze(fetched)
            if not reasons:
                return bundle.resolve(reasons, metas, bundle.should_exclude)
        async with self._prompt_lock:
            async with self._lock:
                reasons, metas = bundle.analyze(fetched)
            if reasons:
                result = should_exclude(bundle.track, reasons, bundle.existing_metadata)
                decision = await result if inspect.isawaitable(result) else result
                async with self._lock:
                    reasons, metas = bundle.analyze(fetched)
                    return bundle.resolve(reasons, metas, lambda _track, _reasons, _existing: decision)
            return bundle.resolve(reasons, metas, bundle.should_exclude)

    async def _edit(
        self,
        track: CSLTrack,
        should_exclude: AsyncShouldExclude,
        decisions: asyncio.Queue[_ExclusionRequest] | None = None,
    ) -> Bundle[None] | None:
        """Fetch track dependencies, obtain a decision, and build unqueued edits."""
        if track.type in ('OffVocal', 'Instrumental'):
            return None
        bundle = ApplyArtistToMetaBundle(
            track,
            self.metadata,
            self.excluded_artists,
            lambda _track, _reasons, _existing: ExcludeDecision.ERROR,
        )
        fetched = await self._client.process(bundle.group_queries())
        existing = await self._client.process(GetMetadataBundle(track))
        bundle = evolve(bundle, existing_metadata=existing)
        if decisions is None:
            prepared = await self._prepare(bundle, fetched, should_exclude)
        else:
            async with self._lock:
                reasons, metas = bundle.analyze(fetched)
                prepared = bundle.resolve(reasons, metas, bundle.should_exclude) if not reasons else None
            if reasons:
                future: asyncio.Future[Bundle[None] | None] = asyncio.get_running_loop().create_future()
                await decisions.put((bundle, fetched, future))
                prepared = await future
        return prepared

    async def apply(
        self,
        track: CSLTrack,
        should_exclude: AsyncShouldExclude = async_prompt_should_exclude,
    ) -> Bundle[None] | None:
        """Return prepared track edits without enqueueing or committing them."""
        return await self._edit(track, should_exclude)

    async def iter_edits(
        self,
        tracks: AsyncIterator[CSLTrack],
        should_exclude: AsyncShouldExclude = async_prompt_should_exclude,
    ) -> AsyncGenerator[Bundle[None], None]:
        """Yield completed edits with bounded workers; close the iterator to stop processing."""
        limit = self._client.max_request_count
        pending: asyncio.Queue[CSLTrack | None] = asyncio.Queue(limit)
        completed: asyncio.Queue[Bundle[None] | None] = asyncio.Queue(limit)
        decisions: asyncio.Queue[_ExclusionRequest] = asyncio.Queue(limit)

        async def produce() -> None:
            async for track in tracks:
                await pending.put(track)
            # Send one stop signal per worker after all tracks have been queued.
            for _ in range(limit):
                await pending.put(None)

        async def decide() -> None:
            while True:
                bundle, fetched, future = await decisions.get()
                prepared = await self._prepare(bundle, fetched, should_exclude)
                if not future.done():
                    future.set_result(prepared)

        async def work() -> None:
            while (track := await pending.get()) is not None:
                edit = await self._edit(track, should_exclude, decisions)
                if edit is not None:
                    await completed.put(edit)

        async def run() -> None:
            try:
                async with asyncio.TaskGroup() as group:
                    consumer = group.create_task(decide())
                    group.create_task(produce())
                    workers = [group.create_task(work()) for _ in range(limit)]
                    await asyncio.gather(*workers)
                    consumer.cancel()
            finally:
                await completed.put(None)

        runner = asyncio.create_task(run())
        try:
            while (edit := await completed.get()) is not None:
                yield edit
            await runner
        finally:
            runner.cancel()
            # Free output capacity so cleanup cannot stall behind an abandoned iterator.
            while not completed.empty():
                completed.get_nowait()
            await asyncio.gather(runner, return_exceptions=True)


@overload
def make_artist_to_meta(
    client: DBClient,
    artists: ArtistDict,
    search_phrases: Sequence[str] = (),
    sep: str = ', ',
    exclude: Sequence[ArtistKey] = (),
) -> SyncArtistToMeta: ...
@overload
def make_artist_to_meta(
    client: AsyncDBClient,
    artists: ArtistDict,
    search_phrases: Sequence[str] = (),
    sep: str = ', ',
    exclude: Sequence[ArtistKey] = (),
) -> Awaitable[AsyncArtistToMeta]: ...


def make_artist_to_meta(
    client: DBClient | AsyncDBClient,
    artists: ArtistDict,
    search_phrases: Sequence[str] = (),
    sep: str = ', ',
    exclude: Sequence[ArtistKey] = (),
) -> SyncArtistToMeta | Awaitable[AsyncArtistToMeta]:
    """Create a mapping from character names, with parallel global and fallback searches.

    Args:
        client: Client retained by the mapping for subsequent application.
        artists: Artist names mapped to character names separated by ``sep``.
        search_phrases: Global searches run before querying unmatched names.
        sep: Separator for character names.
        exclude: Artist names to ignore, including when inferring group metadata.

    Returns:
        A sync mapping, or an awaitable yielding an async mapping.
    """
    match client:
        case DBClient():
            return SyncArtistToMeta.create(client, artists, search_phrases, sep=sep, exclude=exclude)
        case AsyncDBClient():
            return AsyncArtistToMeta.create(client, artists, search_phrases, sep=sep, exclude=exclude)
        case _:
            raise TypeError(f'Expected DBClient or AsyncDBClient, received {type(client).__name__}')
