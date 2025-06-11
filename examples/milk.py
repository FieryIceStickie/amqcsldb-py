import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from log import setup_logging
from rich.pretty import pprint

import amqcsl

_ = load_dotenv()


def main(logger: logging.Logger):
    with amqcsl.DBClient(
        username=os.getenv('AMQ_USERNAME'),
        password=os.getenv('AMQ_PASSWORD'),
    ) as client:
        milk = next(
            track
            for track in client.iter_tracks('Milk', groups=[client.groups['Link! Like! Love Live!']])
            if len(track.artist_credits) == 1
        )
        dir = Path('~/Desktop/hasu_albums/').expanduser()
        audio = (
            dir
            / '[2025.06.04] ラブライブ！蓮ノ空女学院スクールアイドルクラブ 102nd Graduation Album ～Star Sign Memories～ Fujishima Megumi [FLAC]'
            / 'Disc 1'
            / '06. ミルク.flac'
        )
        pprint(audio.name)
        client.add_audio(milk, audio)


if __name__ == '__main__':
    logger = logging.getLogger('INSERT SCRIPT NAME HERE')
    setup_logging()
    main(logger)
