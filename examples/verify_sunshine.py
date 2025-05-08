import logging
import os
from itertools import chain

from dotenv import load_dotenv
from log import setup_logging
from rich.pretty import pprint

import amqcsl
from amqcsl.objects import CSLArtistSample, CSLMetadata, CSLTrack, ExtraMetadata

_ = load_dotenv()


def conv_artist_dict[T](
    client: amqcsl.DBClient,
    artist_to_meta: dict[tuple[str, str | None], T],
    search_phrases: list[str],
) -> dict[CSLArtistSample, T]:
    return {
        artist: v
        for artist in chain.from_iterable(map(client.iter_artists, search_phrases))
        if (v := artist_to_meta.get((artist.name, artist.disambiguation))) is not None
    }


def main(logger: logging.Logger):
    with amqcsl.DBClient(
        username=os.getenv('USERNAME'),
        password=os.getenv('PASSWORD'),
    ) as client:
        for song in client.iter_songs('Cho Tokimeki Sendenbu'):
            song = client.get_song(song)
            if not song.artist_credits:
                pprint(song)


def queue_metadata(
    client: amqcsl.DBClient,
    track: CSLTrack,
    artist_to_meta: dict[CSLArtistSample, list[ExtraMetadata]],
    meta: CSLMetadata | None,
) -> CSLArtistSample | None:
    metas: list[ExtraMetadata] = []
    for cred in track.artist_credits:
        new_metas = artist_to_meta.get(cred.artist)
        if new_metas is None:
            return cred.artist
        metas += new_metas
    client.add_track_metadata(track, *metas, existing_meta=meta)
    return None


if __name__ == '__main__':
    logger = logging.getLogger('example.superstar')
    setup_logging()
    main(logger)
