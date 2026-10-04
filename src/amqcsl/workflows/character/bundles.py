from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import cast, override

import rich.repr
from attrs import evolve, frozen

from amqcsl.clients.bundles._core import Bundle, MixedVendor, MultiVendor, httpClient
from amqcsl.clients.bundles._misc import (
    GetArtistBundle,
    GetMetadataBundle,
    TrackAddMetadataBundle,
    TrackDeleteMetadataBundle,
)
from amqcsl.clients.bundles._pages import AsyncPageStrategy, IterArtistsBundle
from amqcsl.clients.bundles._parallel import ParallelBundle, parallel_actions
from amqcsl.exceptions import AMQCSLError
from amqcsl.objects._db_types import CSLArtist, CSLArtistSample, CSLMetadata, CSLTrack, ExtraMetadata

from .types import (
    INCOMPLETE_GROUP,
    UNKNOWN_ARTIST,
    ArtistDict,
    ArtistKey,
    ArtistName,
    ExcludeDecision,
    Reason,
    ShouldExclude,
)

logger = logging.getLogger('amqcsl.workflows.character_metadata')

type MetadataBundle = TrackAddMetadataBundle | TrackDeleteMetadataBundle


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
    sep: str = ', '
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
            artist: [ExtraMetadata(True, 'Character', value) for value in self.artists[key].split(self.sep)]
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
    existing_metadata: CSLMetadata | None = None

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

    def prepare(self, fetched: Sequence[CSLArtist]) -> Bundle[None] | None:
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
    ) -> Bundle[None] | None:
        """Apply an exclusion decision and build the metadata operation."""
        if self.track.type in ('OffVocal', 'Instrumental'):
            return None
        if reasons:
            match should_exclude(self.track, reasons, self.existing_metadata):
                case ExcludeDecision.EXCLUDE:
                    self.excluded_artists.update(reason.artist.id for reason in reasons)
                case ExcludeDecision.ERROR:
                    raise AMQCSLError(f'Cannot infer character metadata for {self.track.name}: {reasons!r}')
                case ExcludeDecision.IGNORE:
                    logger.info(f'Ignoring track {self.track.name}')
                    return None

        return _character_edits(self.track, metas, self.existing_metadata)

    @override
    def vendor(self, client: httpClient) -> MixedVendor[Bundle[None] | None]:
        if self.track.type in ('OffVocal', 'Instrumental'):
            return None
        fetched = yield from cast(MixedVendor[list[CSLArtist]], self.group_queries().vendor(client))
        existing = yield from cast(MixedVendor[CSLMetadata | None], GetMetadataBundle(self.track).vendor(client))
        prepared = evolve(self, existing_metadata=existing).prepare(fetched)
        return prepared

    @override
    def __rich_repr__(self) -> rich.repr.Result:
        yield 'track', self.track.simp


def _character_edits(
    track: CSLTrack,
    metas: set[ExtraMetadata],
    existing_metadata: CSLMetadata | None,
) -> Bundle[None] | None:
    """Build unqueued edits using metadata fetched before artist decisions."""
    add = TrackAddMetadataBundle(track, metas, existing_meta=existing_metadata)
    bundles: list[MetadataBundle] = [add] if add else []
    if existing_metadata is not None:
        bundles.extend(
            TrackDeleteMetadataBundle(track, meta)
            for meta in existing_metadata.extra_metas
            if meta.key == 'Character' and ExtraMetadata.simplify(meta) not in metas
        )
    return parallel_actions(bundles) if bundles else None
