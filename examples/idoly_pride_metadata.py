import logging
import os

from dotenv import load_dotenv
from log import setup_logging

import amqcsl
from amqcsl.utils import ArtistDict, ArtistName, compact_make_artist_to_meta, prompt, queue_character_metadata

_ = load_dotenv()

artists: ArtistDict = {
    'Mirai Tachibana': 'Kotono Nagase',
    'Kokona Natsume': 'Nagisa Ibuki',
    'Koharu Miyazawa': 'Saki Shiraishi',
    'Kanata Aikawa': 'Suzu Narumiya',
    'Moka Hinata': 'Mei Hayasaka',
    'Mai Kanno': 'Sakura Kawasaki',
    'Yukina Shutou': 'Shizuku Hyoudou',
    'Kanon Takao': 'Chisa Shiraishi',
    'Moeko Yuuki': 'Rei Ichinose',
    'Nao Sasaki': 'Haruko Saeki',
    'Sora Amamiya': 'Rui Tendou',
    'Momo Asakura': 'Yuu Suzumura',
    'Shiina Natsukawa': 'Sumire Okuyama',
    'Haruka Tomatsu': 'Rio Kanzaki',
    'Ayahi Takagaki': 'Aoi Igawa',
    'Minako Kotobuki': 'Ai Komiyama',
    'Aki Toyosaki': 'Kokoro Akazaki',
    'Sayaka Kanda': 'Mana Nagase',
    ArtistName('Lynn', original_name='Lynn'): 'fran',
    'Aimi Tanaka': 'kana',
    'Rie Murakawa': 'miho',
    'Sunny Peace': 'Sakura Kawasaki, Shizuku Hyoudou, Chisa Shiraishi, Rei Ichinose, Haruko Saeki',
    'Tsuki no Tempest': 'Kotono Nagase, Nagisa Ibuki, Saki Shiraishi, Suzu Narumiya, Mei Hayasaka',
    'TRINITYAiLE': 'Rui Tendou, Yuu Suzumura, Sumire Okuyama',
    ('LizNoir', 'Idoly Pride'): 'Rio Kanzaki, Aoi Igawa, Ai Komiyama, Kokoro Akazaki',
    ('LizNoir', 'Idoly Pride (Anime)'): 'Rio Kanzaki, Aoi Igawa',
    'IIIX': 'fran, kana, miho',
    'Hoshimi Production': 'Sakura Kawasaki, Shizuku Hyoudou, Chisa Shiraishi, Rei Ichinose, Haruko Saeki, Kotono Nagase, Nagisa Ibuki, Saki Shiraishi, Suzu Narumiya, Mei Hayasaka',
    'Sweet Rouge': 'Rui Tendou, Sumire Okuyama, Chisa Shiraishi, Nagisa Ibuki',
    'SOH-dan': 'Saki Shiraishi, Chisa Shiraishi, Rei Ichinose, Mei Hayasaka',
    'Pajapa!': 'Suzu Narumiya, Shizuku Hyoudou, Chisa Shiraishi, Sumire Okuyama, Kokoro Akazaki',
    'spring battler': 'Kouhei Makino, Shinji Saegusa, Kyouichi Asakura',
}


def main(logger: logging.Logger):
    with amqcsl.DBClient(
        username=os.getenv('AMQ_USERNAME'),
        password=os.getenv('AMQ_PASSWORD'),
    ) as client:
        artist_to_meta = compact_make_artist_to_meta(
            client,
            artists,
            ['Hoshimi Production'],
        )
        ip_group = client.groups['IDOLY PRIDE']
        for track in client.iter_tracks(groups=[ip_group]):
            meta = client.get_metadata(track)
            if (artist := queue_character_metadata(client, track, artist_to_meta, meta)) is not None:
                prompt(track, msg=f'Unidentified artist {artist.name}, continue?')

        if prompt(client.queue):
            client.commit()


if __name__ == '__main__':
    logger = logging.getLogger(__name__)
    setup_logging()
    main(logger)
