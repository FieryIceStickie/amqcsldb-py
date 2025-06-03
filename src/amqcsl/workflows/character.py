from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from itertools import chain
from typing import Self, override

from attrs import frozen

from amqcsl._client import DBClient
from amqcsl.exceptions import AMQCSLError
from amqcsl.objects._db_types import (
    CSLArtistSample,
    CSLMetadata,
    CSLTrack,
    ExtraMetadata,
)
from amqcsl.workflows._workflow_utils import prompt

__all__ = [
    'ArtistName',
    'ArtistKey',
    'CharacterDict',
    'ArtistDict',
    'ArtistToMeta',
    'compact_make_artist_to_meta',
    'make_artist_to_meta',
    'conv_artists',
    'match_artist',
    'queue_character_metadata',
    'prompt',
]

logger = logging.getLogger('amqcsl.workflows.character_metadata')


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


def compact_make_artist_to_meta(
    client: DBClient,
    artists: ArtistDict,
    search_phrases: Sequence[str] = (),
    sep: str = ', ',
) -> ArtistToMeta:
    """Make the artist to metadata dict with a compact artist dict

    Args:
        client: DBClient
        artists: ArtistDict, values should be character names separated by sep
        search_phrases: List of search phrases to be passed to iter_artists
        sep: Separator for artist values

    Returns:
        ArtistToMeta
    """
    artist_objs = conv_artists(client, artists, search_phrases)
    return {artist_objs[k]: [ExtraMetadata(True, 'Character', c) for c in v.split(sep)] for k, v in artists.items()}


def make_artist_to_meta(
    client: DBClient,
    characters: CharacterDict,
    artists: ArtistDict,
    search_phrases: Sequence[str] = (),
    sep: str = ' ',
) -> ArtistToMeta:
    """Make the artist to metadata dict

    Args:
        client: DBClient
        characters: CharacterDict
        artists: ArtistDict, values should be keys of characters separated by sep
        search_phrases: List of search phrases to be passed to iter_artists
        sep: Separator for artist values

    Returns:
        ArtistToMeta
    """
    metas = {k: ExtraMetadata(True, 'Character', v) for k, v in characters.items()}
    artist_objs = conv_artists(client, artists, search_phrases)
    return {artist_objs[k]: [metas[c] for c in v.split(sep)] for k, v in artists.items()}


def conv_artists(
    client: DBClient,
    artist_keys: Iterable[ArtistKey],
    search_phrases: Sequence[str] = (),
) -> dict[ArtistKey, CSLArtistSample]:
    """Converts a dict {ArtistKey: T} into {artist: T}

    Args: client: DBClient
        artists: Input dict
        search_phrases: List of search phrases to be passed to iter_artists

    Returns:
        Output dict

    Raises:
        AMQCSLError: If search phrases is not enough to fill dict with artists
    """
    rtn: dict[ArtistKey, CSLArtistSample] = {}
    seen: dict[CSLArtistSample, ArtistKey] = {}
    not_found: defaultdict[str, list[ArtistKey]] = defaultdict(list)

    if search_phrases:
        logger.info('Searching phrases for artists')
    artists = {*chain.from_iterable(map(client.iter_artists, search_phrases))}
    for key in artist_keys:
        artist_name = ArtistName.from_key(key)
        artist = match_artist(artist_name, artists)
        if artist is None:
            not_found[artist_name.name].append(key)
        elif artist in seen:
            raise AMQCSLError(f'Names {artist_name} and {ArtistName.from_key(seen[artist])} both match {artist}')
        else:
            rtn[key] = artist
            seen[artist] = key

    if not_found:
        logger.info('Searching for artists by name directly')
    for name, keys in not_found.items():
        artists = {*client.iter_artists(name)}
        for key in keys:
            artist_name = ArtistName.from_key(key)
            artist = match_artist(artist_name, artists)
            if artist is None:
                raise AMQCSLError(f'Could not find artist {artist_name}')
            elif artist in seen:
                raise AMQCSLError(f'Names {artist_name} and {ArtistName.from_key(seen[artist])} both match {artist}')
            else:
                rtn[key] = artist
                seen[artist] = key
    return rtn


def match_artist(artist_name: ArtistName, artists: set[CSLArtistSample]) -> CSLArtistSample | None:
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


def queue_character_metadata(
    client: DBClient,
    track: CSLTrack,
    artist_to_meta: ArtistToMeta,
    meta: CSLMetadata | None,
) -> CSLArtistSample | None:
    """Queue character metadata changes
    This function will clear any existing character metadata (including any that are song metadata)
    and add all metadata according to artist_to_meta

    Args:
        client: DBClient
        track: Track to be edited
        artist_to_meta: {artist: [metas]}
        meta: Existing metadata of the track

    Returns:
        An artist if it isn't in artist_to_meta
        None otherwise
    """
    # Add character metadata if not already exists
    metas: set[ExtraMetadata] = set()
    for cred in track.artist_credits:
        new_metas = artist_to_meta.get(cred.artist)
        if new_metas is None:
            return cred.artist
        metas.update(new_metas)
    logger.info(f'Adding {len(metas)} new metadata to {track.name}')
    client.track_add_metadata(track, *metas, existing_meta=meta, queue=True)

    if meta is None:
        return
    # Remove existing character metadata
    curr = {ExtraMetadata.simplify(m): m for m in meta.extra_metas if m.key == 'Character'}
    for m in curr.keys() - metas:
        client.track_remove_metadata(track, curr[m], queue=True)
