from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Awaitable, Generator, Iterable, Mapping, Sequence
from itertools import chain
from typing import Self, overload, override

import rich.repr
from attrs import define, field, frozen
from attrs.validators import deep_iterable, deep_mapping, instance_of, optional

from amqcsl import AsyncDBClient, DBClient
from amqcsl.clients.bundles._core import (
    Bundle,
    MultiVendor,
    httpxClient,
)
from amqcsl.clients.bundles._misc import (
    TrackAddMetadataBundle,
    TrackDeleteMetadataBundle,
)
from amqcsl.exceptions import AMQCSLError
from amqcsl.objects._db_types import (
    CSLArtistSample,
    CSLMetadata,
    CSLTrack,
    ExtraMetadata,
)

from ._workflow_utils import prompt

__all__ = [
    'ArtistName',
    'ArtistKey',
    'CharacterDict',
    'ArtistDict',
    'ArtistToMeta',
    'compact_make_artist_to_meta',
    'make_artist_to_meta',
    'queue_character_metadata',
    'prompt',
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
type ArtistToMeta = Mapping[CSLArtistSample, Sequence[ExtraMetadata]]


# --- Helpers ---


def _conv_artists(
    artist_keys: Iterable[ArtistKey],
    search_phrases: Sequence[str],
) -> Generator[Iterable[str], dict[str, Iterable[CSLArtistSample]], dict[ArtistKey, CSLArtistSample]]:
    """Converts an iterable of ArtistKey into {ArtistKey: T}

    Args:
        artist_keys: Iterable of artist keys
        search_phrases: List of search phrases to be passed to iter_artists

    Yields:
        [search_phrase]

    Receives:
        {search_phrase: client.iter_artists(search_phrase)}

    Returns:
        {ArtistKey: T}

    Raises:
        AMQCSLError: Ambiguities in artist query/Couldn't find artist
    """
    rtn: dict[ArtistKey, CSLArtistSample] = {}
    seen: dict[CSLArtistSample, ArtistKey] = {}
    not_found: defaultdict[str, list[ArtistKey]] = defaultdict(list)

    if search_phrases:
        logger.info('Searching phrases for artists')
    search_results = yield search_phrases
    artists = {*chain.from_iterable(search_results.values())}
    for key in artist_keys:
        artist_name = ArtistName.from_key(key)
        artist = _match_artist(artist_name, artists)
        if artist is None:
            not_found[artist_name.name].append(key)
        elif artist in seen:
            raise AMQCSLError(f'Names {artist_name} and {ArtistName.from_key(seen[artist])} both match {artist}')
        else:
            rtn[key] = artist
            seen[artist] = key

    if not_found:
        logger.info('Searching for artists by name directly')
    search_results = yield not_found
    for name, keys in not_found.items():
        artists = {*search_results[name]}
        for key in keys:
            artist_name = ArtistName.from_key(key)
            artist = _match_artist(artist_name, artists)
            if artist is None:
                raise AMQCSLError(f'Could not find artist {artist_name}')
            elif artist in seen:
                raise AMQCSLError(f'Names {artist_name} and {ArtistName.from_key(seen[artist])} both match {artist}')
            else:
                rtn[key] = artist
                seen[artist] = key
    return rtn


def _match_artist(artist_name: ArtistName, artists: set[CSLArtistSample]) -> CSLArtistSample | None:
    """Match an ArtistName with an artist

    Args:
        artist_name: ArtistName
        artists: Set of artists

    Returns:
        CSLArtistSample if an artist matches, otherwise None

    Raises:
        AMQCSLError: If multiple artists match
    """
    match [artist for artist in artists if artist_name.match(artist)]:
        case []:
            return None
        case [artist]:
            return artist
        case matching_artists:
            for artist in matching_artists:
                logger.error(artist)
            raise AMQCSLError(f'{len(matching_artists)} artists found for {artist_name}')


def _sync_conv_artists(
    client: DBClient,
    artist_keys: Iterable[ArtistKey],
    search_phrases: Sequence[str] = (),
) -> dict[ArtistKey, CSLArtistSample]:
    g = _conv_artists(artist_keys, search_phrases)
    phrase_to_artists = None
    while True:
        try:
            res = g.send(phrase_to_artists)  # type: ignore[reportArgumentType]
        except StopIteration as e:
            return e.value
        phrase_to_artists = {phrase: client.iter_artists(phrase) for phrase in res}


async def _async_conv_artists(
    client: AsyncDBClient,
    artist_keys: Iterable[ArtistKey],
    search_phrases: Sequence[str] = (),
) -> dict[ArtistKey, CSLArtistSample]:
    g = _conv_artists(artist_keys, search_phrases)
    phrase_to_artists = None
    while True:
        try:
            res = g.send(phrase_to_artists)  # type: ignore[reportArgumentType]
        except StopIteration as e:
            return e.value
        async with asyncio.TaskGroup() as tg:
            tasks = {phrase: tg.create_task(client.iter_artists(phrase)) for phrase in res}
        phrase_to_artists = {phrase: task.result() for phrase, task in tasks.items()}


# --- Exports ---


@overload
def compact_make_artist_to_meta(
    client: DBClient,
    artists: ArtistDict,
    search_phrases: Sequence[str] = (),
    sep: str = ', ',
) -> ArtistToMeta: ...
@overload
def compact_make_artist_to_meta(
    client: AsyncDBClient,
    artists: ArtistDict,
    search_phrases: Sequence[str] = (),
    sep: str = ', ',
) -> Awaitable[ArtistToMeta]: ...


def compact_make_artist_to_meta(
    client: DBClient | AsyncDBClient,
    artists: ArtistDict,
    search_phrases: Sequence[str] = (),
    sep: str = ', ',
) -> ArtistToMeta | Awaitable[ArtistToMeta]:
    """Make the artist to metadata dict with a compact artist dict

    Args:
        client: (Async)DBClient
        artists: ArtistDict, values should be character names separated by sep
        search_phrases: List of search phrases to be passed to iter_artists
        sep: Separator for artist values

    Returns:
        ArtistToMeta
    """
    match client:
        case DBClient():
            artist_objs = _sync_conv_artists(client, artists, search_phrases)
            return {
                artist_objs[k]: [ExtraMetadata(True, 'Character', c) for c in v.split(sep)]  #
                for k, v in artists.items()
            }
        case AsyncDBClient():

            async def rtn():
                artist_objs = await _async_conv_artists(client, artists, search_phrases)
                return {
                    artist_objs[k]: [ExtraMetadata(True, 'Character', c) for c in v.split(sep)]  #
                    for k, v in artists.items()
                }

            return rtn()


def make_artist_to_meta(
    client: DBClient,
    characters: CharacterDict,
    artists: ArtistDict,
    search_phrases: Sequence[str] = (),
    sep: str = ' ',
) -> ArtistToMeta:
    """Make the artist to metadata dict

    Args:
        client: (Async)DBClient
        characters: CharacterDict
        artists: ArtistDict, values should be keys of characters separated by sep
        search_phrases: List of search phrases to be passed to iter_artists
        sep: Separator for artist values

    Returns:
        ArtistToMeta
    """
    metas = {k: ExtraMetadata(True, 'Character', v) for k, v in characters.items()}
    match client:
        case DBClient():
            artist_objs = _sync_conv_artists(client, artists, search_phrases)
            return {artist_objs[k]: [metas[c] for c in v.split(sep)] for k, v in artists.items()}
        case AsyncDBClient():

            async def rtn():
                artist_objs = await _async_conv_artists(client, artists, search_phrases)
                return {artist_objs[k]: [metas[c] for c in v.split(sep)] for k, v in artists.items()}

            return rtn()


type MetadataBundle = TrackAddMetadataBundle | TrackDeleteMetadataBundle


@define
class QueueCharacterMetadataBundle(Bundle[None]):
    track: CSLTrack = field(validator=instance_of(CSLTrack))
    artist_to_meta: ArtistToMeta = field(
        validator=deep_mapping(
            key_validator=instance_of(CSLArtistSample),
            value_validator=deep_iterable(instance_of(ExtraMetadata)),  # type: ignore[reportUnknownArgumentType]
        ),
    )
    meta: CSLMetadata | None = field(default=None, validator=optional(instance_of(CSLMetadata)))

    unknown_artists: list[CSLArtistSample] = field(factory=list[CSLArtistSample], init=False)
    bundles: list[MetadataBundle] = field(factory=list[MetadataBundle], init=False)

    def __attrs_post_init__(self) -> None:
        # Add character metadata if not already exists
        metas: set[ExtraMetadata] = set()
        for cred in self.track.artist_credits:
            new_metas = self.artist_to_meta.get(cred.artist)
            if new_metas is None:
                self.unknown_artists.append(cred.artist)
            else:
                metas.update(new_metas)
        bundle = TrackAddMetadataBundle(self.track, metas, existing_meta=self.meta)
        self.bundles.append(bundle)
        if self.unknown_artists:
            _ = prompt(
                self.track,
                msg=f'Unidentified artists {", ".join(artist.name for artist in self.unknown_artists)}. Continue?',
                continue_on_empty=True,
            )
            return

        if self.meta is None:
            return
        # Remove existing character metadata
        curr = {ExtraMetadata.simplify(m): m for m in self.meta.extra_metas if m.key == 'Character'}
        unknown_metas = curr.keys() - metas
        for m in unknown_metas:
            bundle = TrackDeleteMetadataBundle(self.track, curr[m])
            self.bundles.append(bundle)

    @override
    def vendor(self, client: httpxClient) -> MultiVendor[None]:
        vendors = [bundle.vendor(client) for bundle in self.bundles]
        reqs = [next(vd) for vd in vendors]
        resps = yield reqs
        for res, vd in zip(resps, vendors):
            vd.send(res)

    @override
    def __rich_repr__(self) -> rich.repr.Result:
        yield 'bundles', self.bundles


def queue_character_metadata(
    client: DBClient | AsyncDBClient,
    track: CSLTrack,
    artist_to_meta: ArtistToMeta,
    meta: CSLMetadata | None,
) -> None:
    """Queue character metadata changes
    This function will clear any existing character metadata (including any that are song metadata)
    and add all metadata according to artist_to_meta

    Args:
        client: DBClient
        track: Track to be edited
        artist_to_meta: {artist: [metas]}
        meta: Existing metadata of the track
    """
    bundle = QueueCharacterMetadataBundle(track, artist_to_meta, meta)
    if not any(bundle.bundles):
        return
    client.enqueue(bundle)
