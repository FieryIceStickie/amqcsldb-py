from __future__ import annotations

import asyncio
import inspect
import logging
from collections import defaultdict
from collections.abc import (
    AsyncGenerator,
    AsyncIterator,
    Awaitable,
    Callable,
    ItemsView,
    Iterable,
    Iterator,
    KeysView,
    Mapping,
    Sequence,
    ValuesView,
)
from enum import Enum, auto
from typing import Protocol, Self, cast, overload, override

import rich.repr
from attrs import define, field, frozen
from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from amqcsl import AsyncDBClient, DBClient
from amqcsl.clients.bundles._core import (
    Bundle,
    MixedVendor,
    MultiVendor,
    httpClient,
)
from amqcsl.clients.bundles._misc import (
    GetArtistBundle,
    GetMetadataBundle,
    TrackAddMetadataBundle,
    TrackDeleteMetadataBundle,
)
from amqcsl.clients.bundles._pages import AsyncPageStrategy, IterArtistsBundle
from amqcsl.clients.bundles._parallel import ParallelBundle, parallel_actions
from amqcsl.exceptions import AMQCSLError, QuitError
from amqcsl.objects._db_types import (
    CSLArtist,
    CSLArtistSample,
    CSLMetadata,
    CSLTrack,
    ExtraMetadata,
)

from ._workflow_utils import prompt

__all__ = [
    'INCOMPLETE_GROUP',
    'UNKNOWN_ARTIST',
    'ArtistDict',
    'ArtistKey',
    'ArtistName',
    'ArtistToMeta',
    'AsyncArtistToMeta',
    'AsyncShouldExclude',
    'CharacterDict',
    'ExcludeDecision',
    'Reason',
    'ShouldExclude',
    'SyncArtistToMeta',
    'async_prompt_should_exclude',
    'compact_make_artist_to_meta',
    'make_artist_to_meta',
    'prompt',
    'prompt_should_exclude',
]

logger = logging.getLogger('amqcsl.workflows.character_metadata')


# --- Types ---


class _Wildcard:
    """Wildcard that matches any object, for internal use in ArtistName"""

    instance: Self | None = None

    def __new__(cls) -> Self:
        if cls.instance is None:
            return super().__new__(cls)
        return cls.instance

    @override
    def __eq__(self, other: object) -> bool:
        return True


@frozen
class ArtistName:
    name: str
    original_name: str | None = None
    disambiguation: str | None = None

    @classmethod
    def from_key(cls, artist_key: ArtistKey) -> Self:
        match artist_key:
            case str(name):
                return cls(name)
            case (str(name), str(disam) | (None as disam)):
                return cls(name, disambiguation=disam)
            case ArtistName(name, orig_name, disam):
                return cls(name, orig_name, disam)
            case _:
                raise ValueError(f'Expected artist_key to be of type ArtistKey, received {artist_key!r}')

    def match(self, artist: CSLArtistSample) -> bool:
        orig_name = self.original_name if self.original_name is not None else _Wildcard()
        disam = self.disambiguation if self.disambiguation is not None else _Wildcard()
        return (self.name, orig_name, disam) == (
            artist.name,
            artist.original_name,
            artist.disambiguation,
        )

    @override
    def __str__(self) -> str:
        return (
            f'{self.name}'
            f'{f" <{self.original_name})" if self.original_name is not None else ""}'
            f'{f" ({self.disambiguation})" if self.disambiguation is not None else ""}'
        )


type ArtistKey = ArtistName | tuple[str, str | None] | str
type CharacterDict = Mapping[str, str]
type ArtistDict = Mapping[ArtistKey, str]
type MetadataBundle = TrackAddMetadataBundle | TrackDeleteMetadataBundle


@frozen
class _UnknownArtist:
    """The credited artist has no explicit metadata and is not a group."""


UNKNOWN_ARTIST = _UnknownArtist()


@frozen
class INCOMPLETE_GROUP:
    """Unresolved members, including nested groups and cycles."""

    artists: Sequence[CSLArtistSample]
    known_artists: Sequence[CSLArtistSample] = ()


@frozen
class Reason:
    """A failure for an artist credited on the track."""

    artist: CSLArtistSample
    reason: _UnknownArtist | INCOMPLETE_GROUP


class ExcludeDecision(Enum):
    """How to handle unresolved artists on a track."""

    EXCLUDE = auto()
    ERROR = auto()
    IGNORE = auto()


type ShouldExclude = Callable[[CSLTrack, Sequence[Reason]], ExcludeDecision]


type AsyncShouldExclude = Callable[[CSLTrack, Sequence[Reason]], ExcludeDecision | Awaitable[ExcludeDecision]]

_EXCLUSION_QUESTION = '[Y] Exclude  [N] Error  [I] Ignore track  [Q] Quit › '


def _display_exclusion(track: CSLTrack, artists: Sequence[Reason]) -> None:
    """Show the track and unresolved artists before asking for a decision."""
    details = Table.grid(padding=(0, 2), expand=True)
    details.add_column(style='dim', no_wrap=True)
    details.add_column(ratio=1)
    details.add_row('Track', Text(track.name if track.name is not None else track.original_name, style='bold'))
    details.add_row('ID', Text(track.id, style='dim'))
    details.add_row(
        'Artists', Text(', '.join(dict.fromkeys(credit.artist.name for credit in track.artist_credits)), style='cyan')
    )
    reasons: list[Text] = []
    for failure in artists:
        reason = Text('⚠ ', style='yellow')
        reason.append(failure.artist.name, style='cyan')
        match failure.reason:
            case _UnknownArtist():
                reason.append(' — unknown artist', style='yellow')
            case INCOMPLETE_GROUP(artists=members, known_artists=known):
                reason.append(' — incomplete group', style='yellow')
                if known:
                    reason.append('\n  Known members: ', style='dim')
                    reason.append(', '.join(dict.fromkeys(member.name for member in known)), style='green')
                if members:
                    reason.append('\n  Members without character metadata: ', style='dim')
                    reason.append(', '.join(dict.fromkeys(member.name for member in members)), style='cyan')
        reasons.append(reason)
    rich.print(Panel(Group(details, Text(''), *reasons), title='Unknown metadata found', title_align='left', width=100))


def _parse_exclusion(answer: str) -> ExcludeDecision | None:
    """Parse a decision, returning None for an unrecognized answer."""
    match answer.lower().strip():
        case 'y' | 'yes' | 'exclude':
            return ExcludeDecision.EXCLUDE
        case 'n' | 'no' | 'error':
            return ExcludeDecision.ERROR
        case 'i' | 'ignore':
            return ExcludeDecision.IGNORE
        case 'q' | 'quit':
            raise QuitError
        case _:
            rich.print('[yellow]Unknown answer. Choose Y, N, I, or Q.[/yellow]')
            return None


def prompt_should_exclude(track: CSLTrack, artists: Sequence[Reason]) -> ExcludeDecision:
    """Ask whether to exclude unresolved artists, raise an error, or ignore the track."""
    _display_exclusion(track, artists)
    while True:
        decision = _parse_exclusion(input(_EXCLUSION_QUESTION))
        if decision is not None:
            return decision


async def _read_exclusion() -> str:
    """Wait for terminal input to finish before propagating cancellation."""
    task = asyncio.create_task(asyncio.to_thread(input, _EXCLUSION_QUESTION))
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
        except BaseException:
            if cancelled:
                raise asyncio.CancelledError from None
            raise
    if cancelled:
        if not task.cancelled():
            task.exception()
        raise asyncio.CancelledError
    return task.result()


async def async_prompt_should_exclude(track: CSLTrack, artists: Sequence[Reason]) -> ExcludeDecision:
    """Ask for an exclusion decision without blocking async network requests."""
    _display_exclusion(track, artists)
    while True:
        decision = _parse_exclusion(await _read_exclusion())
        if decision is not None:
            return decision


class ArtistToMeta(Protocol):
    """Shared cached artist metadata for synchronous and asynchronous mappings."""

    metadata: dict[CSLArtistSample, Sequence[ExtraMetadata]]
    excluded_artists: set[str]

    def __getitem__(self, artist: CSLArtistSample) -> Sequence[ExtraMetadata]:
        return self.metadata[artist.to_sample()]

    def __iter__(self) -> Iterator[CSLArtistSample]:
        return iter(self.metadata)

    def __len__(self) -> int:
        return len(self.metadata)

    def __contains__(self, artist: object) -> bool:
        return isinstance(artist, CSLArtistSample) and artist.to_sample() in self.metadata

    @overload
    def get(
        self,
        artist: CSLArtistSample,
        default: None = None,
    ) -> Sequence[ExtraMetadata] | None: ...
    @overload
    def get[T](
        self,
        artist: CSLArtistSample,
        default: T,
    ) -> Sequence[ExtraMetadata] | T: ...

    def get[T](
        self,
        artist: CSLArtistSample,
        default: T | None = None,
    ) -> Sequence[ExtraMetadata] | T | None:
        return self.metadata.get(artist.to_sample(), default)

    def keys(self) -> KeysView[CSLArtistSample]:
        return self.metadata.keys()

    def values(self) -> ValuesView[Sequence[ExtraMetadata]]:
        return self.metadata.values()

    def items(self) -> ItemsView[CSLArtistSample, Sequence[ExtraMetadata]]:
        return self.metadata.items()


def _match_artist(
    artist_name: ArtistName,
    artists: set[CSLArtistSample],
) -> CSLArtistSample | None:
    matches = [artist for artist in artists if artist_name.match(artist)]
    if len(matches) > 1:
        for artist in matches:
            logger.error(artist)
        raise AMQCSLError(f'{len(matches)} artists found for {artist_name}')
    return matches[0] if matches else None


@frozen
class MakeArtistToMetaBundle(Bundle[tuple[dict[CSLArtistSample, Sequence[ExtraMetadata]], set[str]]]):
    artists: ArtistDict
    search_phrases: Sequence[str] = ()
    characters: CharacterDict | None = None
    sep: str = ' '
    max_batch_size: int = 100
    max_query_size: int = 1500
    exclude: Sequence[ArtistKey] = ()

    def _search(
        self,
        client: httpClient,
        phrases: Sequence[str],
    ) -> MultiVendor[list[list[CSLArtistSample]]]:
        bundles = (
            IterArtistsBundle(
                max_batch_size=self.max_batch_size,
                max_query_size=self.max_query_size,
                batch_size=min(50, self.max_batch_size),
                strategy=AsyncPageStrategy(),
                search_term=phrase,
            ).collect()
            for phrase in dict.fromkeys(phrases)
        )
        return (yield from ParallelBundle(bundles).vendor(client))

    @override
    def vendor(
        self,
        client: httpClient,
    ) -> MultiVendor[tuple[dict[CSLArtistSample, Sequence[ExtraMetadata]], set[str]]]:
        if self.search_phrases:
            logger.info('Searching phrases for artists')
        results = yield from self._search(client, self.search_phrases)
        discovered = {artist for result in results for artist in result}
        matched: dict[CSLArtistSample, ArtistKey] = {}
        missing: defaultdict[str, list[ArtistKey]] = defaultdict(list)

        def record(key: ArtistKey, artist: CSLArtistSample) -> None:
            if artist in matched:
                raise AMQCSLError(
                    f'Names {ArtistName.from_key(key)} and {ArtistName.from_key(matched[artist])} both match {artist}'
                )
            matched[artist] = key

        for key in dict.fromkeys([*self.artists, *self.exclude]):
            name = ArtistName.from_key(key)
            artist = _match_artist(name, discovered)
            if artist is None:
                missing[name.name].append(key)
            else:
                record(key, artist)

        if missing:
            logger.info('Searching for artists by name directly')
        results = yield from self._search(client, [*missing])
        not_found: list[ArtistName] = []
        for (name, keys), result in zip(missing.items(), results, strict=True):
            for key in keys:
                artist = _match_artist(ArtistName.from_key(key), {*result})
                if artist is None:
                    not_found.append(ArtistName.from_key(key))
                    continue
                record(key, artist)

        if not_found:
            raise AMQCSLError(f'Could not find artists: {", ".join(str(name) for name in not_found)}')

        excluded = {artist.id for artist, key in matched.items() if key in self.exclude}
        metadata: dict[CSLArtistSample, Sequence[ExtraMetadata]] = {
            artist: [
                ExtraMetadata(True, 'Character', value if self.characters is None else self.characters[value])
                for value in self.artists[key].split(self.sep)
            ]
            for artist, key in matched.items()
            if artist.id not in excluded
        }
        return metadata, excluded

    @override
    def __rich_repr__(self) -> rich.repr.Result:
        yield 'artists', self.artists
        yield 'search_phrases', self.search_phrases
        yield 'exclude', self.exclude, ()


@frozen
class _GroupGraphBundle(Bundle[list[CSLArtist]]):
    """Fetch uncached nested groups in parallel layers, visiting each ID once."""

    artists: Sequence[CSLArtistSample]
    metadata: dict[CSLArtistSample, Sequence[ExtraMetadata]]
    excluded_artists: set[str]

    @override
    def vendor(self, client: httpClient) -> MultiVendor[list[CSLArtist]]:
        pending = {artist.id: artist for artist in self.artists}
        fetched: dict[str, CSLArtist] = {}
        while pending:
            groups = yield from ParallelBundle(
                GetArtistBundle(artist)
                for artist in pending.values()
                if artist.id not in self.excluded_artists and artist.to_sample() not in self.metadata
            ).vendor(client)
            fetched.update((group.id, group) for group in groups)
            pending = {
                relation.artist.id: relation.artist
                for group in groups
                for relation in group.forward_relations
                if relation.type == 'GroupMember'
                and relation.artist.type == 'Group'
                and relation.artist.id not in fetched
                and relation.artist.id not in self.excluded_artists
                and relation.artist.to_sample() not in self.metadata
            }
        return [*fetched.values()]

    @override
    def __rich_repr__(self) -> rich.repr.Result:
        yield 'artists', self.artists


@frozen
class ApplyArtistToMetaBundle(Bundle[Bundle[None] | None]):
    track: CSLTrack
    metadata: dict[CSLArtistSample, Sequence[ExtraMetadata]]
    excluded_artists: set[str]
    should_exclude: ShouldExclude

    def _process_group(
        self,
        group: CSLArtist,
        groups: Mapping[str, CSLArtist],
        ancestors: set[str],
    ) -> Sequence[ExtraMetadata] | INCOMPLETE_GROUP:
        """Infer nested groups, reporting unresolved leaves and cycles without partial metadata."""
        if group.id in ancestors:
            return INCOMPLETE_GROUP([group.to_sample()])
        ancestors = {*ancestors, group.id}
        members = {
            relation.artist.id: relation.artist
            for relation in group.forward_relations
            if relation.type == 'GroupMember'
        }
        missing: dict[str, CSLArtistSample] = {}
        known: dict[str, CSLArtistSample] = {}
        metas: list[ExtraMetadata] = []
        for member in members.values():
            if member.id in self.excluded_artists:
                continue
            result = self.metadata.get(member.to_sample())
            if result is None:
                if member.type != 'Group':
                    missing[member.id] = member
                    continue
                result = self._process_group(groups[member.id], groups, ancestors)
            match result:
                case INCOMPLETE_GROUP(artists=artists, known_artists=resolved):
                    missing.update((artist.id, artist) for artist in artists or [member])
                    known.update((artist.id, artist) for artist in resolved)
                case _:
                    known[member.id] = member
                    metas.extend(result)
        if missing:
            return INCOMPLETE_GROUP([*missing.values()], [*known.values()])
        (*inferred,) = dict.fromkeys(metas)
        self.metadata[group.to_sample()] = inferred
        return inferred

    @property
    def _credited_artists(self) -> dict[str, CSLArtistSample]:
        return {credit.artist.id: credit.artist for credit in self.track.artist_credits}

    def group_queries(self) -> Bundle[list[CSLArtist]]:
        """Fetch uncached groups without modifying shared mapping state."""
        groups = [
            artist
            for artist in self._credited_artists.values()
            if self.track.type not in ('OffVocal', 'Instrumental')
            and artist.id not in self.excluded_artists
            and artist.to_sample() not in self.metadata
            and artist.type == 'Group'
        ]
        return _GroupGraphBundle(groups, self.metadata, self.excluded_artists)

    def prepare(self, fetched: Sequence[CSLArtist]) -> _CharacterMetadataBundle | None:
        """Update caches and decide whether to process the track, without making requests."""
        reasons, metas = self.analyze(fetched)
        return self.resolve(reasons, metas, self.should_exclude)

    def analyze(self, fetched: Sequence[CSLArtist]) -> tuple[list[Reason], set[ExtraMetadata]]:
        """Infer metadata and collect unresolved artists using current shared state."""
        if self.track.type in ('OffVocal', 'Instrumental'):
            return [], set()
        group_by_id = {group.id: group for group in fetched}
        reasons: list[Reason] = []
        metas: set[ExtraMetadata] = set()
        for artist in self._credited_artists.values():
            if artist.id in self.excluded_artists:
                continue
            key = artist.to_sample()
            if key in self.metadata:
                metas.update(self.metadata[key])
                continue
            if artist.type != 'Group':
                reasons.append(Reason(artist, UNKNOWN_ARTIST))
                continue
            group = group_by_id[artist.id]
            match self._process_group(group, group_by_id, set()):
                case INCOMPLETE_GROUP() as missing:
                    reasons.append(Reason(group, missing))
                case inferred:
                    metas.update(inferred)
        return reasons, metas

    def resolve(
        self,
        reasons: Sequence[Reason],
        metas: set[ExtraMetadata],
        should_exclude: ShouldExclude,
    ) -> _CharacterMetadataBundle | None:
        """Apply an exclusion decision and build the metadata operation."""
        if self.track.type in ('OffVocal', 'Instrumental'):
            return None
        if reasons:
            match should_exclude(self.track, reasons):
                case ExcludeDecision.EXCLUDE:
                    self.excluded_artists.update(reason.artist.id for reason in reasons)
                case ExcludeDecision.ERROR:
                    raise AMQCSLError(f'Cannot infer character metadata for {self.track.name}: {reasons!r}')
                case ExcludeDecision.IGNORE:
                    logger.info(f'Ignoring track {self.track.name}')
                    return None

        return _CharacterMetadataBundle(self.track, metas)

    @override
    def vendor(self, client: httpClient) -> MixedVendor[Bundle[None] | None]:
        fetched = yield from cast(MixedVendor[list[CSLArtist]], self.group_queries().vendor(client))
        prepared = self.prepare(fetched)
        if prepared is None:
            return None
        return (yield from prepared.vendor(client))

    @override
    def __rich_repr__(self) -> rich.repr.Result:
        yield 'track', self.track.simp


@frozen
class _CharacterMetadataBundle(Bundle[Bundle[None] | None]):
    """Fetch existing metadata and build queued edits after artist decisions are complete."""

    track: CSLTrack
    metas: set[ExtraMetadata]

    @override
    def vendor(self, client: httpClient) -> MixedVendor[Bundle[None] | None]:
        existing = yield from cast(MixedVendor[CSLMetadata | None], GetMetadataBundle(self.track).vendor(client))
        add = TrackAddMetadataBundle(self.track, self.metas, existing_meta=existing)
        bundles: list[MetadataBundle] = [add] if add else []
        if existing is not None:
            bundles.extend(
                TrackDeleteMetadataBundle(self.track, meta)
                for meta in existing.extra_metas
                if meta.key == 'Character' and ExtraMetadata.simplify(meta) not in self.metas
            )
        return parallel_actions(bundles) if bundles else None

    @override
    def __rich_repr__(self) -> rich.repr.Result:
        yield 'track', self.track.simp


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
        characters: CharacterDict | None = None,
        sep: str = ' ',
        exclude: Sequence[ArtistKey] = (),
    ) -> Self:
        metadata, excluded = client.process(
            MakeArtistToMetaBundle(
                artists,
                search_phrases,
                characters,
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
        characters: CharacterDict | None = None,
        sep: str = ' ',
        exclude: Sequence[ArtistKey] = (),
    ) -> Self:
        metadata, excluded = await client.process(
            MakeArtistToMetaBundle(
                artists,
                search_phrases,
                characters,
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
    ) -> _CharacterMetadataBundle | None:
        """Serialize decisions and recheck shared caches without holding locks across input."""
        async with self._lock:
            reasons, metas = bundle.analyze(fetched)
            if not reasons:
                return bundle.resolve(reasons, metas, bundle.should_exclude)
        async with self._prompt_lock:
            async with self._lock:
                reasons, metas = bundle.analyze(fetched)
            if reasons:
                result = should_exclude(bundle.track, reasons)
                decision = await result if inspect.isawaitable(result) else result
                async with self._lock:
                    reasons, metas = bundle.analyze(fetched)
                    return bundle.resolve(reasons, metas, lambda _track, _reasons: decision)
            return bundle.resolve(reasons, metas, bundle.should_exclude)

    async def _edit(
        self,
        track: CSLTrack,
        should_exclude: AsyncShouldExclude,
        decisions: asyncio.Queue[
            tuple[ApplyArtistToMetaBundle, Sequence[CSLArtist], asyncio.Future[_CharacterMetadataBundle | None]]
        ]
        | None = None,
    ) -> Bundle[None] | None:
        """Fetch track dependencies, obtain a decision, and build unqueued edits."""
        bundle = ApplyArtistToMetaBundle(
            track,
            self.metadata,
            self.excluded_artists,
            lambda _track, _reasons: ExcludeDecision.ERROR,
        )
        fetched = await self._client.process(bundle.group_queries())
        if decisions is None:
            prepared = await self._prepare(bundle, fetched, should_exclude)
        else:
            async with self._lock:
                reasons, metas = bundle.analyze(fetched)
                prepared = bundle.resolve(reasons, metas, bundle.should_exclude) if not reasons else None
            if reasons:
                future: asyncio.Future[_CharacterMetadataBundle | None] = asyncio.get_running_loop().create_future()
                await decisions.put((bundle, fetched, future))
                prepared = await future
        return await self._client.process(prepared) if prepared is not None else None

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
        decisions: asyncio.Queue[
            tuple[ApplyArtistToMetaBundle, Sequence[CSLArtist], asyncio.Future[_CharacterMetadataBundle | None]]
        ] = asyncio.Queue(limit)

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
def compact_make_artist_to_meta(
    client: DBClient,
    artists: ArtistDict,
    search_phrases: Sequence[str] = (),
    sep: str = ', ',
    exclude: Sequence[ArtistKey] = (),
) -> SyncArtistToMeta: ...
@overload
def compact_make_artist_to_meta(
    client: AsyncDBClient,
    artists: ArtistDict,
    search_phrases: Sequence[str] = (),
    sep: str = ', ',
    exclude: Sequence[ArtistKey] = (),
) -> Awaitable[AsyncArtistToMeta]: ...


def compact_make_artist_to_meta(
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
    if isinstance(client, DBClient):
        return SyncArtistToMeta.create(client, artists, search_phrases, sep=sep, exclude=exclude)
    return AsyncArtistToMeta.create(client, artists, search_phrases, sep=sep, exclude=exclude)


@overload
def make_artist_to_meta(
    client: DBClient,
    characters: CharacterDict,
    artists: ArtistDict,
    search_phrases: Sequence[str] = (),
    sep: str = ' ',
    exclude: Sequence[ArtistKey] = (),
) -> SyncArtistToMeta: ...
@overload
def make_artist_to_meta(
    client: AsyncDBClient,
    characters: CharacterDict,
    artists: ArtistDict,
    search_phrases: Sequence[str] = (),
    sep: str = ' ',
    exclude: Sequence[ArtistKey] = (),
) -> Awaitable[AsyncArtistToMeta]: ...


def make_artist_to_meta(
    client: DBClient | AsyncDBClient,
    characters: CharacterDict,
    artists: ArtistDict,
    search_phrases: Sequence[str] = (),
    sep: str = ' ',
    exclude: Sequence[ArtistKey] = (),
) -> SyncArtistToMeta | Awaitable[AsyncArtistToMeta]:
    """Create a mapping from character keys, with parallel global and fallback searches.

    Args:
        client: Client retained by the mapping for subsequent application.
        characters: Character keys mapped to full character names.
        artists: Artist names mapped to character keys separated by ``sep``.
        search_phrases: Global searches run before querying unmatched names.
        sep: Separator for character keys.
        exclude: Artist names to ignore, including when inferring group metadata.

    Returns:
        A sync mapping, or an awaitable yielding an async mapping.
    """
    if isinstance(client, DBClient):
        return SyncArtistToMeta.create(client, artists, search_phrases, characters, sep, exclude)
    return AsyncArtistToMeta.create(client, artists, search_phrases, characters, sep, exclude)
