import json
import logging.config
from os import PathLike
from pathlib import Path

DEFAULT_LOG_CONFIG_PATH = Path(__file__).parent / 'log_config.json'


def setup_logging(config_path: str | PathLike[str] = DEFAULT_LOG_CONFIG_PATH):
    with open(config_path, 'r') as file:
        config = json.load(file)
    logging.config.dictConfig(config)
