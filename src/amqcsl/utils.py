import logging
from collections.abc import Mapping, Sequence
from itertools import chain
from operator import attrgetter

from amqcsl.objects._db_types import CSLTrackArtistCredit, TrackPutArtistCredit

from ._client import DBClient
from .objects import CSLArtistSample, CSLMetadata, CSLTrack, ExtraMetadata

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
