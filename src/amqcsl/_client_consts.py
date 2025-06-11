import logging
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, override

import httpx
from attrs import frozen

from amqcsl.exceptions import QueryError
from amqcsl.objects._db_types import CSLExtraMetadata, CSLGroup, CSLSongArtistCredit, CSLTrack
from amqcsl.objects._json_types import AlbumAddBody, MetadataPostBody, TrackPutBody

if TYPE_CHECKING:
    from amqcsl import DBClient

DB_URL = 'https://amqbot.082640.xyz'
DEFAULT_SESSION_PATH = 'amq_session.txt'


logger = logging.getLogger('amqcsl.client')


@frozen
class QueueObj:
    req: httpx.Request

    def run(self, client: 'DBClient', queue: bool) -> None:
        if queue:
            client.enqueue(self)
            return
        res = client.client.send(self.req)
        try:
            res.raise_for_status()
        except httpx.HTTPStatusError:
            logger.exception(f'Request {self.req.method} {self.req.url} caused an error')
            raise


@frozen
class MetadataPost(QueueObj):
    track: CSLTrack
    body: MetadataPostBody
    id_to_name: Mapping[str, str]

    def __rich_repr__(self) -> Iterator[Any]:
        yield 'id', self.track.id
        yield 'name', self.track.name, None
        for cred in self.body['artistCredits']:
            yield f'{self.req.method} {cred["type"]} {self.id_to_name[cred["artistId"]]}'
        for meta in self.body['extraMetadatas']:
            yield f'{self.req.method} {"Artist" if meta["isArtist"] else "Song"} meta {meta["type"]} {meta["value"]}'


@frozen
class MetadataDelete(QueueObj):
    track: CSLTrack
    meta: CSLSongArtistCredit | CSLExtraMetadata

    def __rich_repr__(self) -> Iterator[Any]:
        yield 'id', self.track.id
        yield 'name', self.track.name, None
        match self.meta:
            case CSLSongArtistCredit():
                yield f'{self.req.method} {self.meta.type} {self.meta.artist.name}'
            case CSLExtraMetadata():
                yield f'{self.req.method} {self.meta.type} {self.meta.key} {self.meta.value}'


@frozen
class TrackEdit(QueueObj):
    track: CSLTrack
    body: TrackPutBody

    def __rich_repr__(self) -> Iterator[Any]:
        yield 'id', self.track.id
        yield 'name', self.track.name, None
        body = self.body
        if body['batchSongIds'] is not None:
            yield from body['batchSongIds']
        if body['artistCredits'] is not None:
            yield 'artist_credits_before', self.track.artist_credits
            yield 'artist_credits_after', body['artistCredits']
        if body['groupIds'] is not None:
            yield 'groups', body['groupIds']
        yield 'new_name', body['name'], None
        yield 'new_song', body['newSong'], None
        yield 'new_original_artist', body['originalArtist'], None
        yield 'new_original_name', body['originalName'], None
        yield 'new_song_id', body['songId'], None
        yield 'new_type', body['type'], None


@frozen
class AlbumAdd(QueueObj):
    groups: Iterable[CSLGroup]
    body: AlbumAddBody

    def __rich_repr__(self) -> Iterator[Any]:
        body = self.body
        yield 'name', body['album']
        yield 'original_name', body['originalAlbum']
        yield 'year', body['year']
        yield 'groups', [group.name for group in self.groups]
        yield 'tracks', body['tracks']


@frozen
class AudioAdd(QueueObj):
    track: CSLTrack
    path: Path
    mime_type: str

    def __rich_repr__(self) -> Iterator[Any]:
        yield 'id', self.track.id
        yield 'name', self.track.name, None
        yield 'audio_path', self.path.resolve()

    @override
    def run(self, client: 'DBClient', queue: bool) -> None:
        if queue:
            client.enqueue(self)
            return
        res = client.client.send(self.req)
        res.raise_for_status()
        match res.json():
            case {
                'sessionId': str(session_id),
                'key': str(key),
                'url': str(url),
            }:
                pass
            case _:
                logger.error(
                    f'Presigning upload of {self.track.name} returned unknown json', extra={'return_json': res.json()}
                )
                raise QueryError('Received unknown json when presigning upload')
        with open(self.path, 'rb') as file:
            res = client.client.post(
                url,
                params={'sessionId': session_id, 'key': key},
                files={'file': (self.path.name, file, self.mime_type)},
            )
        res.raise_for_status()
