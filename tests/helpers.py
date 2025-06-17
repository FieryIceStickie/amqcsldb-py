from pathlib import Path
import json

resources = Path(__file__).parent / 'resources'


def load(name: str):
    with open(resources / f'{name}.json', 'r') as file:
        return json.loads(file.read())
