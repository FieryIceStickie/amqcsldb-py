import logging
from collections.abc import Mapping, Sequence
from itertools import chain
from typing import Any

from rich.pretty import pprint

from amqcsl.exceptions import QuitError
from amqcsl.objects import (
    CSLArtistSample,
    CSLMetadata,
    CSLTrack,
    ExtraMetadata,
)

from ._client import DBClient

logger = logging.getLogger('amqcsl.utils')

type ArtistKey = tuple[str, str | None] | str
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
    pre_meta_dict = {
        k: [ExtraMetadata(True, 'Character', c) for c in v.split(sep)]
        for k, v in artists.items()
    }
    return conv_artist_dict(client, pre_meta_dict, search_phrases)


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
    pre_meta_dict = {
        k: [metas[c] for c in v.split(sep)]
        for k, v in artists.items()
    }
    return conv_artist_dict(client, pre_meta_dict, search_phrases)


def conv_artist_dict[T](
    client: DBClient,
    artists: Mapping[ArtistKey, T],
    search_phrases: Sequence[str] = (),
) -> Mapping[CSLArtistSample, T]:
    """Converts a dict {(name, disam) | name: T} into {artist: T} 

    Args: client: DBClient
        artists: Input dict
        search_phrases: List of search phrases to be passed to iter_artists

    Returns:
        Output dict

    Raises:
        AMQCSLError: If search phrases is not enough to fill dict with artists
    """
    normalized = {(key, None) if isinstance(key, str) else key: v for key, v in artists.items()}
    rtn = {
        artist: v
        for artist in chain.from_iterable(map(client.iter_artists, search_phrases))
        if (v := normalized.get((artist.name, artist.disambiguation))) is not None
    }
    lost = normalized.keys() - {(artist.name, artist.disambiguation) for artist in rtn}
    for name, disam in lost:
        found = [artist for artist in client.iter_artists(name) if artist.name == name]
        if not found:
            raise KeyError(f'Could not find artist {name} ({disam})')
        if disam is None and len(found) == 1:
            rtn[found.pop()] = normalized[name, disam]
            continue
        found = [artist for artist in found if artist.disambiguation == disam]
        if not found:
            raise KeyError(f'Could not find artist {name} ({disam})')
        elif len(found) != 1:
            raise KeyError(f'More than one {name} with disambiguation {disam}')
        rtn[found.pop()] = normalized[name, disam]
    return rtn


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
    client.track_metadata_add(track, *metas, existing_meta=meta, queue=True)

    if meta is None:
        return
    # Remove existing character metadata
    curr = {ExtraMetadata.simplify(m): m for m in meta.extra_metas if m.key == 'Character'}
    for m in curr.keys() - metas:
        client.track_metadata_remove(track, curr[m], queue=True)


def prompt(*objs: Any, msg: str = 'Accept?', pretty: bool = True, **kwargs: Any) -> bool:
    """Prompt the user for a Yes or No answer, or to quit the script

    Args:
        *objs: Objects to print
        **kwargs: Kwargs to pass to the print function
        msg: Message to prompt user with, defaults to 'Accept?'
        pretty: Whether to pretty print with rich.pretty.pprint

    Returns:
        User's choice

    Raises:
        QuitError: If the user chooses to quit
    """
    print_func = pprint if pretty else print
    for obj in objs:
        print_func(obj, **kwargs)
    while inp := input(f'{msg} Y(es) N(o) Q(uit): '):
        match inp.lower().strip():
            case 'y' | 'yes':
                return True
            case 'n' | 'no':
                return False
            case 'q' | 'quit':
                raise QuitError
            case _:
                continue
    return True
