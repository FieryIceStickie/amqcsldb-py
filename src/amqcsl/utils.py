import logging
from collections.abc import Mapping, Sequence
from itertools import chain
from operator import attrgetter
from typing import Any

from rich.pretty import pprint

from amqcsl.exceptions import AMQCSLError, QuitError
from amqcsl.objects import (
    CSLArtistSample,
    CSLMetadata,
    CSLTrack,
    CSLTrackArtistCredit,
    ExtraMetadata,
    TrackPutArtistCredit,
)

from ._client import DBClient

logger = logging.getLogger('amqcsl.utils')

type PreMetaDict = Mapping[tuple[str, str | None] | str, Sequence[ExtraMetadata]]
type MetaDict = Mapping[CSLArtistSample, Sequence[ExtraMetadata]]


def conv_artist_dict[T](
    client: DBClient,
    artist_to_meta: Mapping[tuple[str, str | None] | str, T],
    search_phrases: Sequence[str],
) -> dict[CSLArtistSample, T]:
    normalized = {
        (key, None) if isinstance(key, str) else key: v
        for key, v in artist_to_meta.items()
    }
    rtn = {
        artist: v
        for artist in chain.from_iterable(map(client.iter_artists, search_phrases))
        if (v := normalized.get((artist.name, artist.disambiguation))) is not None
    }
    lost = normalized.keys() - {(artist.name, artist.disambiguation) for artist in rtn}
    if lost:
        raise AMQCSLError(f'Could not find artists {", ".join([name for name, _ in lost])}')
    return rtn


def queue_metadata(
    client: DBClient,
    track: CSLTrack,
    artist_to_meta: Mapping[CSLArtistSample, Sequence[ExtraMetadata]],
    meta: CSLMetadata | None,
) -> CSLArtistSample | None:
    metas: list[ExtraMetadata] = []
    for cred in track.artist_credits:
        new_metas = artist_to_meta.get(cred.artist)
        if new_metas is None:
            return cred.artist
        metas += new_metas
    client.add_track_metadata(track, *metas, existing_meta=meta, queue=True)


def conv_artist_credits(creds: Sequence[CSLTrackArtistCredit]) -> list[TrackPutArtistCredit]:
    return [TrackPutArtistCredit.simplify(cred) for cred in sorted(creds, key=attrgetter('position'))]


def prompt(*objs: Any, pretty: bool = True, **kwargs: Any) -> bool:
    print_func = pprint if pretty else print
    for obj in objs:
        print_func(obj, **kwargs)
    while inp := input('Accept Y/N? (Q to quit)'):
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
