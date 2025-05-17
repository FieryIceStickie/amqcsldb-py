import logging
import os
from operator import itemgetter

from dotenv import load_dotenv
from log import setup_logging

import amqcsl
from amqcsl.objects import ExtraMetadata

_ = load_dotenv()


def main(logger: logging.Logger):
    with amqcsl.DBClient(
        username=os.getenv('USERNAME'),
        password=os.getenv('PASSWORD'),
    ) as client:
        terraria_group, video_games_group = itemgetter('Terraria', 'Video Games')(client.groups)
        for track in client.iter_tracks('Terraria'):
            if track.original_simple_artist != 'Scott Lloyd Shelly':
                logger.info(f'Skipping {track.name} by {track.original_simple_artist}')
                continue
            logger.info(f'Updating {track.name}')
            client.track_edit(track, groups=[terraria_group, video_games_group])
            meta = client.get_metadata(track)
            if meta is not None and 'Game' in meta.fields:
                logger.info(f'Track {track.name} already has Game metadata')
                continue
            logger.info(f'Adding metadata to {track.name}')
            client.track_metadata_add(track, ExtraMetadata(False, 'Game', 'Terraria'))


if __name__ == '__main__':
    logger = logging.getLogger('example.terraria')
    setup_logging()
    main(logger)
