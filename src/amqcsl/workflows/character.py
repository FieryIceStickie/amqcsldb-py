from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable, ItemsView, Iterator, KeysView, Mapping, Sequence, ValuesView
from typing import Protocol, Self, cast, overload, override

import rich.repr
from attrs import define, field, frozen

from amqcsl import AsyncDBClient, DBClient
from amqcsl.clients.bundles._core import (
    Bundle,
    MixedVendor,
    MultiVendor,
    httpxClient,
)
from amqcsl.clients.bundles._misc import (
    GetArtistBundle,
    GetMetadataBundle,
    TrackAddMetadataBundle,
    TrackDeleteMetadataBundle,
)
from amqcsl.clients.bundles._pages import AsyncPageStrategy, IterArtistsBundle
from amqcsl.clients.bundles._parallel import ParallelBundle, parallel_actions
from amqcsl.exceptions import AMQCSLError
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
    'CharacterDict',
    'Reason',
    'ShouldExclude',
    'SyncArtistToMeta',
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
    """Members without cached metadata. An empty list means no members exist."""

    artists: Sequence[CSLArtistSample]


@frozen
class Reason:
    """A failure for an artist credited on the track."""

    artist: CSLArtistSample
    reason: _UnknownArtist | INCOMPLETE_GROUP


type ShouldExclude = Callable[[CSLTrack, Sequence[Reason]], bool]


def prompt_should_exclude(track: CSLTrack, artists: Sequence[Reason]) -> bool:
    return prompt(track, artists, msg='Exclude these artists?')


class ArtistToMeta[R](Protocol):
    """Cached artist metadata and track application, synchronous or asynchronous."""

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

    def apply(
        self,
        track: CSLTrack,
        should_exclude: ShouldExclude = prompt_should_exclude,
    ) -> R:
        """Infer character metadata and queue additions and deletions for a track.

        Args:
            track: Track whose character metadata should be updated.
            should_exclude: Called once with all unresolved artists. True caches their
                exclusions; False raises AMQCSLError without queueing track changes.

        Commit queued changes through the client used to create this mapping.
        """
        ...


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
        client: httpxClient,
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
        client: httpxClient,
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
class ApplyArtistToMetaBundle(Bundle[Bundle[None] | None]):
    track: CSLTrack
    metadata: dict[CSLArtistSample, Sequence[ExtraMetadata]]
    excluded_artists: set[str]
    should_exclude: ShouldExclude

    def _process_group(self, group: CSLArtist) -> Sequence[ExtraMetadata] | Reason:
        """Infer a complete group from cached member metadata without recursive queries."""
        members = {
            relation.artist.id: relation.artist
            for relation in group.forward_relations
            if relation.type == 'GroupMember'
        }
        active_members = [member for member in members.values() if member.id not in self.excluded_artists]
        missing = [member for member in active_members if member.to_sample() not in self.metadata]
        if missing or not members:
            return Reason(group, INCOMPLETE_GROUP(missing))
        (*inferred,) = dict.fromkeys(meta for member in active_members for meta in self.metadata[member.to_sample()])
        self.metadata[group.to_sample()] = inferred
        return inferred

    @override
    def vendor(self, client: httpxClient) -> MixedVendor[Bundle[None] | None]:
        if self.track.type == 'OffVocal':
            return None
        credited = {credit.artist.id: credit.artist for credit in self.track.artist_credits}
        groups = [
            artist
            for artist in credited.values()
            if artist.id not in self.excluded_artists
            and artist.to_sample() not in self.metadata
            and artist.type == 'Group'
        ]
        fetched = yield from cast(
            MixedVendor[list[CSLArtist]],
            ParallelBundle(GetArtistBundle(artist) for artist in groups).vendor(client),
        )
        group_by_id = {group.id: group for group in fetched}
        reasons: list[Reason] = []
        metas: set[ExtraMetadata] = set()
        for artist in credited.values():
            if artist.id in self.excluded_artists:
                continue
            key = artist.to_sample()
            if key in self.metadata:
                metas.update(self.metadata[key])
                continue
            if artist.type != 'Group':
                reasons.append(Reason(artist, UNKNOWN_ARTIST))
                continue
            result = self._process_group(group_by_id[artist.id])
            if isinstance(result, Reason):
                reasons.append(result)
            else:
                metas.update(result)
        if reasons:
            if not self.should_exclude(self.track, reasons):
                raise AMQCSLError(f'Cannot infer character metadata for {self.track.name}: {reasons!r}')
            self.excluded_artists.update(reason.artist.id for reason in reasons)

        existing = yield from cast(MixedVendor[CSLMetadata | None], GetMetadataBundle(self.track).vendor(client))
        add = TrackAddMetadataBundle(self.track, metas, existing_meta=existing)
        bundles: list[MetadataBundle] = [add] if add else []
        if existing is not None:
            bundles.extend(
                TrackDeleteMetadataBundle(self.track, meta)
                for meta in existing.extra_metas
                if meta.key == 'Character' and ExtraMetadata.simplify(meta) not in metas
            )
        return parallel_actions(bundles) if bundles else None

    @override
    def __rich_repr__(self) -> rich.repr.Result:
        yield 'track', self.track.simp


@define
class SyncArtistToMeta(ArtistToMeta[None]):
    """Artist mapping that queues metadata changes synchronously."""

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
    ) -> None:
        bundle = self._client.process(
            ApplyArtistToMetaBundle(
                track,
                self.metadata,
                self.excluded_artists,
                should_exclude,
            )
        )
        if bundle is not None:
            self._client.enqueue(bundle)


@define
class AsyncArtistToMeta(ArtistToMeta[Awaitable[None]]):
    """Artist mapping that queues metadata changes asynchronously."""

    _client: AsyncDBClient = field(repr=False, eq=False)
    metadata: dict[CSLArtistSample, Sequence[ExtraMetadata]]
    excluded_artists: set[str] = field(factory=set[str])
    _lock: asyncio.Lock = field(factory=asyncio.Lock, init=False, repr=False, eq=False)

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

    async def apply(
        self,
        track: CSLTrack,
        should_exclude: ShouldExclude = prompt_should_exclude,
    ) -> None:
        # Track tasks may run concurrently; share cache/exclusion decisions exactly once.
        async with self._lock:
            bundle = await self._client.process(
                ApplyArtistToMetaBundle(
                    track,
                    self.metadata,
                    self.excluded_artists,
                    should_exclude,
                )
            )
            if bundle is not None:
                self._client.enqueue(bundle)


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
