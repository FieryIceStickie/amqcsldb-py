import logging
import os
from itertools import chain

from dotenv import load_dotenv
from log import setup_logging

import amqcsl
from amqcsl import prompt
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
    kanon = ExtraMetadata(True, 'Character', 'Kanon Shibuya')
    keke = ExtraMetadata(True, 'Character', 'Keke Tang')
    sumire = ExtraMetadata(True, 'Character', 'Sumire Heanna')
    chisato = ExtraMetadata(True, 'Character', 'Chisato Arashi')
    ren = ExtraMetadata(True, 'Character', 'Ren Hazuki')
    kinako = ExtraMetadata(True, 'Character', 'Kinako Sakurakouji')
    natsumi = ExtraMetadata(True, 'Character', 'Natsumi Onitsuka')
    shiki = ExtraMetadata(True, 'Character', 'Shiki Wakana')
    mei = ExtraMetadata(True, 'Character', 'Mei Yoneme')
    margarete = ExtraMetadata(True, 'Character', 'Margarete Wien')
    tomari = ExtraMetadata(True, 'Character', 'Tomari Onitsuka')
    yuuna = ExtraMetadata(True, 'Character', 'Yuuna Hijirisawa')
    mao = ExtraMetadata(True, 'Character', 'Mao Hiiragi')
    with amqcsl.DBClient(
        username=os.getenv('USERNAME'),
        password=os.getenv('PASSWORD'),
    ) as client:
        artist_to_meta = conv_artist_dict(
            client,
            {
                ('Liella!', 'Love Live! Superstar!! (11 members)'): [
                    kanon,
                    keke,
                    sumire,
                    chisato,
                    ren,
                    kinako,
                    natsumi,
                    shiki,
                    mei,
                    margarete,
                    tomari,
                ],
                ('Liella!', 'Love Live! Superstar!! (9 members)'): [
                    kanon,
                    keke,
                    sumire,
                    chisato,
                    ren,
                    kinako,
                    natsumi,
                    shiki,
                    mei,
                ],
                ('Liella!', 'Love Live! Superstar!! (8 members)'): [
                    keke,
                    sumire,
                    chisato,
                    ren,
                    kinako,
                    natsumi,
                    shiki,
                    mei,
                ],
                ('Liella!', 'Love Live! Superstar!! (6 members)'): [kanon, keke, sumire, chisato, ren, kinako],
                ('Liella!', 'Love Live! Superstar!! (5 members)'): [kanon, keke, sumire, chisato, ren],
                ('Sayuri Date', None): [kanon],
                ('Liyuu', None): [keke],
                ('Nako Misaki', None): [chisato],
                ('Naomi Payton', None): [sumire],
                ('Nagisa Aoyama', None): [ren],
                ('Nozomi Suzuhara', None): [kinako],
                ('Aya Emori', None): [natsumi],
                ('Wakana Ookuma', None): [shiki],
                ('Akane Yabushima', None): [mei],
                ('Yuina', None): [margarete],
                ('Sakura Sakakura', None): [tomari],
                ('CatChu!', 'Love Live!'): [kanon, sumire, mei],
                ('KALEIDOSCORE', 'Love Live!'): [keke, ren, margarete],
                ('5yncri5e!', 'Love Live!'): [chisato, kinako, natsumi, shiki, tomari],
                ('Sunny Passion', 'Love Live! Superstar!!'): [yuuna, mao],
            },
            ['Liella!', '5yncri5e!', 'CatChu!', 'KALEIDOSCORE', 'Sunny Passion'],
        )
        superstar_group = client.groups['Love Live! Superstar!!']
        for track in client.iter_tracks(groups=[superstar_group], batch_size=100):
            if track.audio_id is None:
                continue
            if track.original_simple_artist in {'藤澤慶昌', '杉並児童合唱団'}:
                continue
            meta = client.get_metadata(track)
            if (artist := queue_metadata(client, track, artist_to_meta, meta)) is not None:
                logger.info(f'Unidentified artist {artist.name} {artist.disambiguation}')
                prompt(track)
                continue
            if meta:
                for m in meta.extra_metas:
                    if m.value in {'Tang Keke', 'Wien Margarete'}:
                        logger.info(f'Removing metadata {m.key} {m.value} from {track.name}')
                        client.remove_track_metadata(track, m, queue=True)

        if prompt(client.queue):
            client.commit()


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
    client.add_track_metadata(track, *metas, existing_meta=meta, queue=True)
    return None


if __name__ == '__main__':
    logger = logging.getLogger('example.superstar')
    setup_logging()
    main(logger)
