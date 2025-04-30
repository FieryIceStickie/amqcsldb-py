import os

from dotenv import load_dotenv
from log import setup_logging

import amqcsl

_ = load_dotenv()


def main():
    with amqcsl.DBClient(
        username=os.getenv('USERNAME'),
        password=os.getenv('PASSWORD'),
    ) as client:
        print(client)


if __name__ == '__main__':
    setup_logging()
    main()

