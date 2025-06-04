import logging
import os

from dotenv import load_dotenv

import amqcsl
from amqcsl.workflows import character as cm

from .log import setup_logging

_ = load_dotenv()

yohane_songs = {
    'Genjitsu Mysterium',
    'GAME ON!',
    'Kimi no Tame Boku no Tame',
    'SILENT PAIN',
    'Futari de Hitotsu',
    'BLOOM OF COLORS',
    'Deep Blue',
    'SAKURA-saku KOKORO-saku',
    'Numazu Yassa Yoisa Uta',
    'Koukishin Journey',
    'Special Holidays',
    'Best wishes',
    'Tick-Tack, Tick-Tack',
    'Te・ki・na Music',
    'Forever U & I',
    'La la Yuuki no Uta',
    'Wonder sea breeze',
    'GIRLS!!',
    'Hey, dear my friends',
    'R・E・P',
    'Be as one!!!',
    'Far far away',
}

characters: cm.CharacterDict = {
    'chika': 'Chika Takami',
    'you': 'You Watanabe',
    'riko': 'Riko Sakurauchi',
    'yoshiko': 'Yoshiko Tsushima',
    'hanamaru': 'Hanamaru Kunikida',
    'ruby': 'Ruby Kurosawa',
    'dia': 'Dia Kurosawa',
    'mari': 'Mari Ohara',
    'kanan': 'Kanan Matsuura',
    'sarah': 'Sarah Kazuno',
    'leah': 'Leah Kazuno',
    'y_chika': 'Chika',
    'y_you': 'You',
    'y_riko': 'Riko',
    'y_yohane': 'Yohane',
    'y_hanamaru': 'Hanamaru',
    'y_ruby': 'Ruby',
    'y_dia': 'Dia',
    'y_mari': 'Mari',
    'y_kanan': 'Kanan',
    'lailaps': 'Lailaps',
}

artists: list[cm.ArtistDict] = [
    {
        'Anju Inami': 'chika',
        'Shuka Saitou': 'you',
        'Rikako Aida': 'riko',
        'Aika Kobayashi': 'yoshiko',
        'Kanako Takatsuki': 'hanamaru',
        'Ai Furihata': 'ruby',
        'Aina Suzuki': 'mari',
        'Nanaka Suwa': 'kanan',
        'Arisa Komiya': 'dia',
        'Saint Snow': 'sarah leah',
        'Saint Aqours Snow': 'chika you riko yoshiko hanamaru ruby mari kanan dia sarah leah',
        'Aqours': 'chika you riko yoshiko hanamaru ruby mari kanan dia',
        'Guilty Kiss': 'riko yoshiko mari',
        'CYaRon!': 'chika you ruby',
        'AZALEA': 'hanamaru kanan dia',
    },
    {
        'Anju Inami': 'y_chika',
        'Shuka Saitou': 'y_you',
        'Rikako Aida': 'y_riko',
        'Aika Kobayashi': 'y_yohane',
        'Kanako Takatsuki': 'y_hanamaru',
        'Ai Furihata': 'y_ruby',
        'Aina Suzuki': 'y_mari',
        'Nanaka Suwa': 'y_kanan',
        'Arisa Komiya': 'y_dia',
        'Youko Hikasa': 'lailaps',
        'Aqours': 'y_chika y_you y_riko y_yohane y_hanamaru y_ruby y_mari y_kanan y_dia',
    },
]


def main(logger: logging.Logger):
    with amqcsl.DBClient(
        username=os.getenv('AMQ_USERNAME'),
        password=os.getenv('AMQ_PASSWORD'),
        max_query_size=4000,
    ) as client:
        artists_to_meta = [
            cm.make_artist_to_meta(client, characters, artist_dict, ['Aqours']) for artist_dict in artists
        ]
        sunshine_group = client.groups['Love Live! Sunshine!!']
        for track in client.iter_tracks(groups=[sunshine_group], batch_size=100):
            if track.audio_id is None or track.song is None or track.name is None:
                logger.info(f'Skipping {track.original_name}')
                continue
            if track.original_simple_artist in {'加藤達也'}:
                continue
            is_yohane = track.song.name in yohane_songs
            logger.info(f'Checking {track.name} which is {"" if is_yohane else "not "}a yohane song')
            if 'Fourth Solo Concert Album' in track.album and 'Solo Ver.' not in track.name:
                (cred,) = track.artist_credits
                (character,) = artists_to_meta[is_yohane][cred.artist]
                client.track_edit(track, name=f'{track.name} ({character.value} Solo Ver.)', queue=True)
            elif 'Solo Ver.' in track.name:
                (cred,) = track.artist_credits
                (character,) = artists_to_meta[is_yohane][cred.artist]
                first, last = character.value.split(' ')
                if f'{last} {first}' in track.name:
                    client.track_edit(track, name=track.name.replace(f'{last} {first}', f'{first} {last}'), queue=True)

            meta = client.get_metadata(track)
            if (artist := cm.queue_character_metadata(client, track, artists_to_meta[is_yohane], meta)) is not None:
                if artist.name in {'AiScReam', 'YYY'}:
                    continue
                cm.prompt(track, msg=f'Unidentified artist {artist.name}, continue?', continue_on_empty=True)
                continue

        if cm.prompt(client.queue):
            client.commit()


if __name__ == '__main__':
    logger = logging.getLogger('example.sunshine')
    setup_logging()
    main(logger)
