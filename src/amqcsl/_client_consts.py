from collections.abc import Iterator
from typing import Any

import httpx
from rich.pretty import pprint

from .exceptions import QuitError
from .objects import CSLExtraMetadata, CSLSongArtistCredit, CSLTrackSample, MetadataPostBody

DB_URL = 'https://amqbot.082640.xyz'
DEFAULT_SESSION_PATH = 'amq_session.txt'


def prompt(*objs: Any, pretty: bool = True, **kwargs: Any) -> bool:
    print_func = pprint if pretty else print
    for obj in objs:
        print_func(obj, **kwargs)
    while inp := input('Accept Y/N? '):
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


type QueueObj = MetadataPost | MetadataDelete


class MetadataPost:
    req: httpx.Request
    track: CSLTrackSample
    body: MetadataPostBody

    def __rich_repr__(self) -> Iterator[Any]:
        yield f'{self.track.name}'
        yield 'disambiguation', getattr(self.track, 'disambiguation', ''), ''
        for cred in self.body['artistCredits']:
            yield f'{cred["type"]}'
        for meta in self.body['extraMetadatas']:
            yield f'{meta["type"]} {meta["value"]}'


class MetadataDelete:
    req: httpx.Request
    track: CSLTrackSample
    meta: CSLSongArtistCredit | CSLExtraMetadata
