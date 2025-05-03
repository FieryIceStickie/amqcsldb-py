from __future__ import annotations

import datetime as dt
import logging
from operator import attrgetter
from typing import cast

from attrs import define

from amqcsl.exceptions import QueryError

from .obj_utils import ARTIST_TYPE, EXTRA_METADATA_TYPE, SONG_RELATION_TYPE, TRACK_TYPE

__all__ = ['CSLList']


type JSONType = str | int | float | bool | None | dict[str, JSONType] | list[JSONType]

logger = logging.getLogger('object')


# --- List ---


@define
class CSLList:
    id: str
    name: str
    count: int

    @classmethod
    def from_json(cls, data: JSONType):
        match data:
            case {
                'id': str(id),
                'name': str(name),
                'count': int(count),
            }:
                return cls(
                    id=id,
                    name=name,
                    count=count,
                )
            case _:
                logger.info('Invalid json when parsing CSLList', extra={'json': data})
                raise QueryError('Invalid json when parsing CSLList')


# --- Group ---


@define
class CSLGroup:
    id: str
    name: str

    @classmethod
    def from_json(cls, data: JSONType):
        match data:
            case {
                'id': str(id),
                'name': str(name),
            }:
                return cls(
                    id=id,
                    name=name,
                )
            case _:
                logger.info('Invalid json when parsing CSLGroup', extra={'json': data})
                raise QueryError('Invalid json when parsing CSLGroup')


# --- Metadata ---


@define
class CSLMetadata:
    override: bool
    artist_credits: list[CSLSongArtistCredit]
    extra_metas: list[CSLExtraMetadata]
    total_count: int
    fields: list[str]

    @classmethod
    def from_json(cls, data: JSONType):
        match data:
            case {
                'override': bool(override),
                'artistCredits': [*artist_credits],
                'extraMetas': [*extra_metas],
                'totalCount': int(total_count),
                'fields': [*fields],
            }:
                artist_credits = [*map(CSLSongArtistCredit.from_json, artist_credits)]
                extra_metas = [*map(CSLExtraMetadata.from_json, extra_metas)]
                if not all(isinstance(item, str) for item in fields):
                    logger.info(
                        'Invalid json when parsing CSLMetadata: Metadata fields has non-string value',
                        extra={'json': data},
                    )
                    raise QueryError('Invalid json when parsing CSLMetadata: Metadata fields has non-string value')
                fields = cast(list[str], fields)
                return cls(
                    override=override,
                    artist_credits=artist_credits,
                    extra_metas=extra_metas,
                    total_count=total_count,
                    fields=fields,
                )
            case _:
                logger.info('Invalid json when parsing CSLMetadata', extra={'json': data})
                raise QueryError('Invalid json when parsing CSLMetadata')


@define
class CSLExtraMetadata:
    id: str
    type_id: int
    key: str
    value: str

    @property
    def type(self) -> str:
        return EXTRA_METADATA_TYPE[self.type_id]

    @classmethod
    def from_json(cls, data: JSONType):
        match data:
            case {
                'id': str(id),
                'type': int(type_id),
                'key': str(key),
                'value': str(value),
            }:
                return cls(
                    id=id,
                    type_id=type_id,
                    key=key,
                    value=value,
                )
            case _:
                logger.info('Invalid json when parsing CSLExtraMetadata', extra={'json': data})
                raise QueryError('Invalid json when parsing CSLExtraMetadata')


# --- Artist ---


@define
class CSLArtistSample:
    id: str
    name: str
    original_name: str
    disambiguation: str | None
    type_id: int

    @property
    def type(self) -> str:
        return ARTIST_TYPE[self.type_id]

    @classmethod
    def from_json(cls, data: JSONType):
        match data:
            case {
                'id': str(id),
                'name': str(name),
                'originalName': str(original_name),
                'disambiguation': str(disambiguation) | (None as disambiguation),
                'type': int(type_id),
            }:
                return cls(
                    id=id,
                    name=name,
                    original_name=original_name,
                    disambiguation=disambiguation,
                    type_id=type_id,
                )
            case _:
                logger.info('Invalid json when parsing CSLArtistSample', extra={'json': data})
                raise QueryError('Invalid json when parsing CSLArtistSample')


@define
class CSLArtist(CSLArtistSample):
    forward_relations: list[CSLSongRelation]
    reverse_relations: list[CSLSongRelation]
    linked_amq_songs: list[CSLTrackLink]
    linked_tracks: list[CSLTrackLink]

    @classmethod
    def from_json(cls, data: JSONType):
        match data:
            case {
                'id': str(id),
                'name': str(name),
                'originalName': str(original_name),
                'disambiguation': str(disambiguation) | (None as disambiguation),
                'type': int(type_id),
                'forwardRelations': [*forward_relations],
                'reverseRelations': [*reverse_relations],
                'linkedAMQSongs': [*linked_amq_songs],
                'linkedTracks': [*linked_tracks],
            }:
                forward_relations = [*map(CSLSongRelation.from_json, forward_relations)]
                reverse_relations = [*map(CSLSongRelation.from_json, reverse_relations)]
                linked_amq_songs = [*map(CSLTrackLink.from_json, linked_amq_songs)]
                linked_tracks = [*map(CSLTrackLink.from_json, linked_tracks)]
                return cls(
                    id=id,
                    name=name,
                    original_name=original_name,
                    disambiguation=disambiguation,
                    type_id=type_id,
                    forward_relations=forward_relations,
                    reverse_relations=reverse_relations,
                    linked_amq_songs=linked_amq_songs,
                    linked_tracks=linked_tracks,
                )
            case _:
                logger.info('Invalid json when parsing CSLArtist', extra={'json': data})
                raise QueryError('Invalid json when parsing CSLArtist')


@define
class CSLSongRelation:
    id: str
    type_id: int
    artist: CSLArtistSample

    @property
    def type(self):
        return SONG_RELATION_TYPE[self.type_id]

    @classmethod
    def from_json(cls, data: JSONType):
        match data:
            case {
                'id': str(id),
                'type': int(type_id),
                'artist': artist,
            }:
                artist = CSLArtistSample.from_json(artist)
                return cls(
                    id=id,
                    type_id=type_id,
                    artist=artist,
                )
            case _:
                logger.info('Invalid json when parsing CSLSongRelation', extra={'json': data})
                raise QueryError('Invalid json when parsing CSLSongRelation')


@define
class CSLTrackSample:
    id: str
    name: str | None


@define
class CSLTrackLink(CSLTrackSample):
    artists: list[CSLTrackArtistCredit]

    @classmethod
    def from_json(cls, data: JSONType):
        match data:
            case {
                'id': str(id),
                'name': str(name),
                'artists': [*artists],
            }:
                artists = [*map(CSLTrackArtistCredit.from_json, artists)]
                return cls(
                    id=id,
                    name=name,
                    artists=artists,
                )
            case _:
                logger.info('Invalid json when parsing CSLTrackLink', extra={'json': data})
                raise QueryError('Invalid json when parsing CSLTrackLink')


# --- Song ---


@define
class CSLSongSample:
    id: str
    name: str
    disambiguation: str
    str_created_at: str

    @property
    def created_at(self) -> dt.datetime:
        return dt.datetime.fromisoformat(self.str_created_at)

    @classmethod
    def from_json(cls, data: JSONType):
        match data:
            case {
                'id': str(id),
                'name': str(name),
                'disambiguation': str(disambiguation),
                'createdAt': str(str_created_at),
            }:
                return cls(
                    id=id,
                    name=name,
                    disambiguation=disambiguation,
                    str_created_at=str_created_at,
                )
            case _:
                logger.info('Invalid json when parsing CSLSongSample', extra={'json': data})
                raise QueryError('Invalid json when parsing CSLSongSample')


@define
class CSLSong(CSLSongSample):
    artist_credits: list[CSLSongArtistCredit]
    extra_metas: list[CSLExtraMetadata]

    @classmethod
    def from_json(cls, data: JSONType):
        match data:
            case {
                'id': str(id),
                'name': str(name),
                'disambiguation': str(disambiguation),
                'createdAt': str(str_created_at),
                'artistCredits': [*artist_credits],
                'extraMetas': [*extra_metas],
            }:
                artist_credits = [*map(CSLSongArtistCredit.from_json, artist_credits)]
                extra_metas = [*map(CSLExtraMetadata.from_json, extra_metas)]
                return cls(
                    id=id,
                    name=name,
                    disambiguation=disambiguation,
                    str_created_at=str_created_at,
                    artist_credits=artist_credits,
                    extra_metas=extra_metas,
                )
            case _:
                logger.info('Invalid json when parsing CSLSong', extra={'json': data})
                raise QueryError('Invalid json when parsing CSLSong')


@define
class CSLSongArtistCredit:
    id: str
    type: str
    artist: CSLArtistSample

    @classmethod
    def from_json(cls, data: JSONType):
        match data:
            case {
                'id': str(id),
                'type': str(type_id),
                'artist': artist,
            }:
                artist = CSLArtistSample.from_json(artist)
                return cls(
                    id=id,
                    type=type_id,
                    artist=artist,
                )
            case _:
                logger.info('Invalid json when parsing CSLSongArtistCredit', extra={'json': data})
                raise QueryError('Invalid json when parsing CSLSongArtistCredit')


# --- Track ---


@define
class CSLTrack(CSLTrackSample):
    original_name: str
    original_simple_artist: str
    original_album: str
    album: str
    track_number: int
    track_total: int
    disc_number: int
    disc_total: int
    year: int
    song: CSLSongSample | None
    artist_credits: list[CSLTrackArtistCredit]
    groups: list[CSLGroup]
    audio_id: str | None
    audio_name: str | None
    disabled: bool
    type_id: int
    str_created_at: str
    str_updated_at: str
    in_list: bool

    @property
    def type(self) -> str:
        return TRACK_TYPE[self.type_id]

    @property
    def created_at(self) -> dt.datetime:
        return dt.datetime.fromisoformat(self.str_created_at)

    @property
    def updated_at(self) -> dt.datetime:
        return dt.datetime.fromisoformat(self.str_updated_at)

    @property
    def str_artist_credits(self) -> str:
        return ''.join([f'{credit.name}{credit.join_phrase}' for credit in self.artist_credits])

    @classmethod
    def from_json(cls, data: JSONType):
        match data:
            case {
                'id': str(id),
                'name': str(name) | (None as name),
                'originalName': str(original_name),
                'originalSimpleArtist': str(original_simple_artist),
                'originalAlbum': str(original_album),
                'album': str(album),
                'trackNumber': int(track_number),
                'trackTotal': int(track_total),
                'discNumber': int(disc_number),
                'discTotal': int(disc_total),
                'year': int(year),
                'song': song,
                'artistCredits': [*artist_credits],
                'groups': [*groups],
                'audioId': str(audio_id) | (None as audio_id),
                'audioName': str(audio_name) | (None as audio_name),
                'disabled': bool(disabled),
                'type': int(type_id),
                'createdAt': str(str_created_at),
                'updatedAt': str(str_updated_at),
                'inList': bool(in_list),
            }:
                song = None if song is None else CSLSongSample.from_json(song)
                artist_credits = [*map(CSLTrackArtistCredit.from_json, artist_credits)]
                artist_credits.sort(key=attrgetter('position'))
                groups = [*map(CSLGroup.from_json, groups)]
                return cls(
                    id=id,
                    name=name,
                    original_name=original_name,
                    original_simple_artist=original_simple_artist,
                    original_album=original_album,
                    album=album,
                    track_number=track_number,
                    track_total=track_total,
                    disc_number=disc_number,
                    disc_total=disc_total,
                    year=year,
                    song=song,
                    artist_credits=artist_credits,
                    groups=groups,
                    audio_id=audio_id,
                    audio_name=audio_name,
                    disabled=disabled,
                    type_id=type_id,
                    str_created_at=str_created_at,
                    str_updated_at=str_updated_at,
                    in_list=in_list,
                )
            case _:
                logger.info('Invalid json when parsing CSLTrack', extra={'json': data})
                raise QueryError('Invalid json when parsing CSLTrack')


@define
class CSLTrackArtistCredit:
    artist: CSLArtistSample
    name: str
    join_phrase: str
    position: int

    @classmethod
    def from_json(cls, data: JSONType):
        match data:
            case {
                'artist': artist,
                'name': str(name),
                'joinPhrase': str(join_phrase),
                'position': int(position),
            }:
                artist = CSLArtistSample.from_json(artist)
                return cls(
                    artist=artist,
                    name=name,
                    join_phrase=join_phrase,
                    position=position,
                )
            case _:
                logger.info('Invalid json when parsing CSLTrackArtistCredit', extra={'json': data})
                raise QueryError('Invalid json when parsing CSLTrackArtistCredit')
