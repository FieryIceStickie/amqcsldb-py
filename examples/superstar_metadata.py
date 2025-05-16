import logging
import os

from dotenv import load_dotenv
from log import setup_logging

import amqcsl
from amqcsl.objects import ExtraMetadata
from amqcsl.utils import PreMetaDict, conv_artist_dict, prompt, queue_metadata

_ = load_dotenv()


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

# fmt: off
artist_dict: PreMetaDict = {
    ('Liella!', 'Love Live! Superstar!! (11 members)'): [kanon, keke, sumire, chisato, ren, kinako, natsumi, shiki, mei, margarete, tomari],
    ('Liella!', 'Love Live! Superstar!! (9 members)'): [kanon, keke, sumire, chisato, ren, kinako, natsumi, shiki, mei],
    ('Liella!', 'Love Live! Superstar!! (8 members)'): [keke, sumire, chisato, ren, kinako, natsumi, shiki, mei],
    ('Liella!', 'Love Live! Superstar!! (6 members)'): [kanon, keke, sumire, chisato, ren, kinako],
    ('Liella!', 'Love Live! Superstar!! (5 members)'): [kanon, keke, sumire, chisato, ren],
    'Sayuri Date': [kanon],
    'Liyuu': [keke],
    'Nako Misaki': [chisato],
    'Naomi Payton': [sumire],
    'Nagisa Aoyama': [ren],
    'Nozomi Suzuhara': [kinako],
    'Aya Emori': [natsumi],
    'Wakana Ookuma': [shiki],
    'Akane Yabushima': [mei],
    'Yuina': [margarete],
    'Sakura Sakakura': [tomari],
    ('CatChu!', 'Love Live!'): [kanon, sumire, mei],
    ('KALEIDOSCORE', 'Love Live!'): [keke, ren, margarete],
    ('5yncri5e!', 'Love Live!'): [chisato, kinako, natsumi, shiki, tomari],
    ('Sunny Passion', 'Love Live! Superstar!!'): [yuuna, mao],
}
# fmt: on


def main(logger: logging.Logger):
    with amqcsl.DBClient(
        username=os.getenv('USERNAME'),
        password=os.getenv('PASSWORD'),
    ) as client:
        artist_to_meta = conv_artist_dict(
            client,
            artist_dict,
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


if __name__ == '__main__':
    logger = logging.getLogger('example.superstar')
    setup_logging()
    main(logger)
