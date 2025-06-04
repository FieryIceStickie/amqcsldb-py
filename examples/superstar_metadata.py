import logging
import os

from dotenv import load_dotenv
from log import setup_logging

import amqcsl
from amqcsl.workflows import character as cm

_ = load_dotenv()

characters: cm.CharacterDict = {
    'kanon': 'Kanon Shibuya',
    'keke': 'Keke Tang',
    'sumire': 'Sumire Heanna',
    'chisato': 'Chisato Arashi',
    'ren': 'Ren Hazuki',
    'kinako': 'Kinako Sakurakouji',
    'natsumi': 'Natsumi Onitsuka',
    'shiki': 'Shiki Wakana',
    'mei': 'Mei Yoneme',
    'margarete': 'Margarete Wien',
    'tomari': 'Tomari Onitsuka',
    'yuuna': 'Yuuna Hijirisawa',
    'mao': 'Mao Hiiragi',
}

# fmt: off
artists: cm.ArtistDict = {
    ('Liella!', 'Love Live! Superstar!! (11 members)'): 'kanon keke sumire chisato ren kinako natsumi shiki mei margarete tomari',
    ('Liella!', 'Love Live! Superstar!! (9 members)'): 'kanon keke sumire chisato ren kinako natsumi shiki mei',
    ('Liella!', 'Love Live! Superstar!! (8 members)'): 'keke sumire chisato ren kinako natsumi shiki mei',
    ('Liella!', 'Love Live! Superstar!! (6 members)'): 'kanon keke sumire chisato ren kinako',
    ('Liella!', 'Love Live! Superstar!! (5 members)'): 'kanon keke sumire chisato ren',
    'Sayuri Date': 'kanon',
    'Liyuu': 'keke',
    'Nako Misaki': 'chisato',
    'Naomi Payton': 'sumire',
    'Nagisa Aoyama': 'ren',
    'Nozomi Suzuhara': 'kinako',
    'Aya Emori': 'natsumi',
    'Wakana Ookuma': 'shiki',
    'Akane Yabushima': 'mei',
    'Yuina': 'margarete',
    'Sakura Sakakura': 'tomari',
    'CatChu!': 'kanon sumire mei',
    'KALEIDOSCORE': 'keke ren margarete',
    '5yncri5e!': 'chisato kinako natsumi shiki tomari',
    'Sunny Passion': 'yuuna mao',
}
# fmt: on


def main(logger: logging.Logger):
    with amqcsl.DBClient(
        username=os.getenv('AMQ_USERNAME'),
        password=os.getenv('AMQ_PASSWORD'),
    ) as client:
        artist_to_meta = cm.make_artist_to_meta(
            client,
            characters,
            artists,
            ['Liella!'],
        )
        superstar_group = client.groups['Love Live! Superstar!!']
        for track in client.iter_tracks(groups=[superstar_group], batch_size=100):
            if track.audio_id is None:
                continue
            if track.original_simple_artist in {'藤澤慶昌', '杉並児童合唱団'}:
                continue
            meta = client.get_metadata(track)
            if (artist := cm.queue_character_metadata(client, track, artist_to_meta, meta)) is not None:
                cm.prompt(track, msg=f'Unidentified artist {artist.name}, continue?', continue_on_empty=True)
                continue

        if cm.prompt(client.queue):
            client.commit()


if __name__ == '__main__':
    logger = logging.getLogger('example.superstar')
    setup_logging()
    main(logger)
