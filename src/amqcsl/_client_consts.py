from collections.abc import Iterator, Mapping
from typing import Any

import httpx
from attrs import define
from rich.pretty import pprint

from amqcsl.objects._db_types import CSLTrack

from .exceptions import QuitError
from .objects import CSLExtraMetadata, CSLSongArtistCredit, CSLTrackSample
from .objects._json_types import MetadataPostBody, TrackPutBody

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


@define
class QueueObj:
    req: httpx.Request

@define
class MetadataPost(QueueObj):
    track: CSLTrackSample
    body: MetadataPostBody
    id_to_name: Mapping[str, str]

    def __rich_repr__(self) -> Iterator[Any]:
        if name := self.track.name:
            name = self.track.name
            if disam := getattr(self.track, 'disambiguation'):
                name += f' ({disam})'
            yield name
        for cred in self.body['artistCredits']:
            yield f'{self.req.method} {cred["type"]} {self.id_to_name[cred["artistId"]]}'
        for meta in self.body['extraMetadatas']:
            yield f'{self.req.method} {meta["type"]} {meta["value"]}'


@define
class MetadataDelete(QueueObj):
    track: CSLTrackSample
    meta: CSLSongArtistCredit | CSLExtraMetadata

    def __rich_repr__(self) -> Iterator[Any]:
        if name := self.track.name:
            name = self.track.name
            if disam := getattr(self.track, 'disambiguation'):
                name += f' ({disam})'
            yield name
        match self.meta:
            case CSLSongArtistCredit():
                yield f'{self.req.method} {self.meta.type} {self.meta.artist.name}'
            case CSLExtraMetadata():
                yield f'{self.req.method} {self.meta.type} {self.meta.value}'


@define
class TrackEdit(QueueObj):
    track: CSLTrack
    body: TrackPutBody

    def __rich_repr__(self) -> Iterator[Any]:
        if name := self.track.name:
            name = self.track.name
            if disam := getattr(self.track, 'disambiguation'):
                name += f' ({disam})'
            yield name
        body = self.body
        if body['batchSongIds'] is not None:
            yield from body['batchSongIds']
        if body['artistCredits'] is not None:
            yield 'artist_credits_before', self.track.artist_credits
            yield 'artist_credits_after', body['artistCredits']
        if body['groupIds'] is not None:
            yield 'groups', body['groupIds']
        yield 'name', body['name'], None
        yield 'new_song', body['newSong'], None
        yield 'original_artist', body['newSong'], None
        yield 'original_artist', body['originalArtist'], None
        yield 'song_id', body['songId'], None
        yield 'type', body['type'], None


