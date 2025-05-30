import logging
import os

from dotenv import load_dotenv
from log import setup_logging

import amqcsl
from amqcsl.objects import ExtraMetadata

_ = load_dotenv()


def main(logger: logging.Logger):
    with amqcsl.DBClient(
        username=os.getenv('AMQ_USERNAME'),
        password=os.getenv('AMQ_PASSWORD'),
    ) as client:
        hollow_knight_group = client.groups['Hollow Knight']
        for track in client.iter_tracks(groups=[hollow_knight_group]):
            meta = client.get_metadata(track)
            if meta is not None and 'Game' in meta.fields:
                logger.info(f'Track {track.name} already has Game metadata')
                continue
            logger.info(f'Adding metadata to {track.name}')
            client.track_metadata_add(track, ExtraMetadata(False, 'Game', 'Hollow Knight'))


if __name__ == '__main__':
    logger = logging.getLogger('example.hollow_knight')
    setup_logging()
    main(logger)
