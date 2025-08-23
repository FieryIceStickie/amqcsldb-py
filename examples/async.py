import asyncio
import logging
import os

from dotenv import load_dotenv
from log import setup_logging
from rich.pretty import pprint

import amqcsl
from amqcsl.objects import CSLTrack

_ = load_dotenv()


async def main(logger: logging.Logger):
    async with amqcsl.AsyncDBClient(
        username=os.getenv('AMQ_USERNAME'),
        password=os.getenv('AMQ_PASSWORD'),
    ) as client:
        times = await client.iter_tracks('Shuka Saitou', func=process_track)
    pprint([*times])


async def process_track(client: amqcsl.AsyncDBClient, track: CSLTrack):
    await client.get_metadata(track)


if __name__ == '__main__':
    logger = logging.getLogger('Async')
    setup_logging()
    asyncio.run(main(logger))
