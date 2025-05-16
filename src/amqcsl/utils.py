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

type MetaDict = dict[CSLArtistSample, ExtraMetadata]


def conv_artist_dict[T](
    client: DBClient,
    artist_to_meta: Mapping[tuple[str, str | None], T],
    search_phrases: Sequence[str],
) -> dict[CSLArtistSample, T]:
    return {
        artist: v
        for artist in chain.from_iterable(map(client.iter_artists, search_phrases))
        if (v := artist_to_meta.get((artist.name, artist.disambiguation))) is not None
    }


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
    return None


def conv_artist_credits(creds: Sequence[CSLTrackArtistCredit]) -> list[TrackPutArtistCredit]:
    return [TrackPutArtistCredit.simplify(cred) for cred in sorted(creds, key=attrgetter('position'))]


def prompt(*objs: Any, pretty: bool = True, **kwargs: Any) -> bool:
    print_func = pprint if pretty else print
    for obj in objs:
        print_func(obj, **kwargs)
    while inp := input('Accept Y/N? '):
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
