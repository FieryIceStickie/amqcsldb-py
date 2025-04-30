from typing import TypedDict

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
