from typing import Any, TypedDict
from rich.pretty import pprint

from .exceptions import QuitError

DB_URL = 'https://amqbot.082640.xyz'
DEFAULT_SESSION_PATH = 'amq_session.txt'


class SongQueryParams(TypedDict):
    searchTerm: str
    skip: int
    take: int
    orderBy: str
    filter: str


class ArtistQueryParams(TypedDict):
    searchTerm: str
    skip: int
    take: int
    orderBy: str
    filter: str


class TrackQueryBody(TypedDict):
    activeListId: str | None
    filter: str
    groupFilters: list[str]
    orderBy: str
    quickFilters: list[int]
    searchTerm: str
    skip: int
    take: int


class Query(TypedDict):
    skip: int
    take: int

def prompt(obj: Any, *, pretty: bool = True) -> bool:
    if pretty:
        pprint(obj)
    else:
        print(obj)
    while inp := input('Accept Y/N?'):
        match inp.lower().strip():
            case 'y' | 'yes':
                return True
            case 'n' | 'no':
                return False
            case 'q' | 'quit':
                raise QuitError
            case _:
                continue
    raise RuntimeError('Broke out of prompt loop')
