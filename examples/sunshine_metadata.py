# TODO: update this to use newer things
import logging
import os
from collections.abc import Sequence
from typing import Mapping

from dotenv import load_dotenv

import amqcsl
from amqcsl.objects import ExtraMetadata
from amqcsl.utils import ArtistKey, conv_artist_dict, prompt, queue_character_metadata

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
chika = ExtraMetadata(True, 'Character', 'Chika Takami')
you = ExtraMetadata(True, 'Character', 'You Watanabe')
riko = ExtraMetadata(True, 'Character', 'Riko Sakurauchi')
yoshiko = ExtraMetadata(True, 'Character', 'Yoshiko Tsushima')
hanamaru = ExtraMetadata(True, 'Character', 'Hanamaru Kunikida')
ruby = ExtraMetadata(True, 'Character', 'Ruby Kurosawa')
dia = ExtraMetadata(True, 'Character', 'Dia Kurosawa')
mari = ExtraMetadata(True, 'Character', 'Mari Ohara')
kanan = ExtraMetadata(True, 'Character', 'Kanan Matsuura')
sarah = ExtraMetadata(True, 'Character', 'Sarah Kazuno')
leah = ExtraMetadata(True, 'Character', 'Leah Kazuno')

y_chika = ExtraMetadata(True, 'Character', 'Chika')
y_you = ExtraMetadata(True, 'Character', 'You')
y_riko = ExtraMetadata(True, 'Character', 'Riko')
y_yohane = ExtraMetadata(True, 'Character', 'Yohane')
y_hanamaru = ExtraMetadata(True, 'Character', 'Hanamaru')
y_ruby = ExtraMetadata(True, 'Character', 'Ruby')
y_dia = ExtraMetadata(True, 'Character', 'Dia')
y_mari = ExtraMetadata(True, 'Character', 'Mari')
y_kanan = ExtraMetadata(True, 'Character', 'Kanan')
lailaps = ExtraMetadata(True, 'Character', 'Lailaps')

# fmt: off
normal_artists: Mapping[ArtistKey, Sequence[ExtraMetadata]] = {
    'Anju Inami': [chika],
    'Shuka Saitou': [you],
    'Rikako Aida': [riko],
    'Aika Kobayashi': [yoshiko],
    'Kanako Takatsuki': [hanamaru],
    'Ai Furihata': [ruby],
    'Aina Suzuki': [mari],
    'Nanaka Suwa': [kanan],
    'Arisa Komiya': [dia],
    ('Saint Snow', 'Love Live! Sunshine!!'): [sarah, leah],
    ('Saint Aqours Snow', 'Love Live! Sunshine!!'): [chika, you, riko, yoshiko, hanamaru, ruby, mari, kanan, dia, sarah, leah],
    ('Aqours', 'Love Live! Sunshine!!'): [chika, you, riko, yoshiko, hanamaru, ruby, mari, kanan, dia],
    ('Guilty Kiss', 'Love Live!'): [riko, yoshiko, mari],
    ('CYaRon!', 'Love Live!'): [chika, you, ruby],
    ('AZALEA', 'Love Live!'): [hanamaru, kanan, dia],
}
yohane_artists: Mapping[ArtistKey, Sequence[ExtraMetadata]] = {
    'Anju Inami': [y_chika],
    'Shuka Saitou': [y_you],
    'Rikako Aida': [y_riko],
    'Aika Kobayashi': [y_yohane],
    'Kanako Takatsuki': [y_hanamaru],
    'Ai Furihata': [y_ruby],
    'Aina Suzuki': [y_mari],
    'Nanaka Suwa': [y_kanan],
    'Arisa Komiya': [y_dia],
    'Youko Hikasa': [lailaps],
    ('Aqours', 'Love Live! Sunshine!!'): [y_chika, y_you, y_riko, y_yohane, y_hanamaru, y_ruby, y_mari, y_kanan, y_dia],
}
# fmt: on


def main(logger: logging.Logger):
    with amqcsl.DBClient(
        username=os.getenv('AMQ_USERNAME'),
        password=os.getenv('AMQ_PASSWORD'),
        max_query_size=4000,
    ) as client:
        sunshine_to_meta = conv_artist_dict(client, normal_artists, ['Aqours', 'Guilty Kiss', 'CYaRon!', 'AZALEA'])
        yohane_to_meta = conv_artist_dict(client, yohane_artists, ['Aqours', 'Youko Hikasa'])

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
                (character,) = sunshine_to_meta[cred.artist]
                client.track_edit(track, name=f'{track.name} ({character.value} Solo Ver.)', queue=True)
            elif 'Solo Ver.' in track.name:
                (cred,) = track.artist_credits
                (character,) = sunshine_to_meta[cred.artist]
                first, last = character.value.split(' ')
                if f'{last} {first}' in track.name:
                    client.track_edit(track, name=track.name.replace(f'{last} {first}', f'{first} {last}'), queue=True)

            meta = client.get_metadata(track)
            artist_to_meta = yohane_to_meta if is_yohane else sunshine_to_meta
            if (artist := queue_character_metadata(client, track, artist_to_meta, meta)) is not None:
                if artist.name in {'AiScReam', 'YYY'}:
                    continue
                logger.info(f'Unidentified artist {artist.name} {artist.disambiguation}')
                prompt(track, msg=f'Unidentified artist {artist.name} {artist.disambiguation}, continue?')
                continue

        if prompt(client.queue):
            client.commit()


if __name__ == '__main__':
    logger = logging.getLogger('example.sunshine')
    setup_logging()
    main(logger)
