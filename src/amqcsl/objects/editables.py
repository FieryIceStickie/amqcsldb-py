from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any, Literal, overload

from attrs import define, frozen

from .obj_utils import REVERSE_TRACK_TYPE, TRACK_TYPE
from .objects import (
    CSLArtistSample,
    CSLExtraMetadata,
    CSLGroup,
    CSLMetadata,
    CSLSongArtistCredit,
    CSLSongSample,
    CSLTrack,
    CSLTrackArtistCredit,
)

type Editable = CSLTrack | CSLMetadata
type EditObj = TrackEdit | MetadataEdit


logger = logging.getLogger('object')


@overload
def Edit(obj: CSLTrack) -> TrackEdit: ...


@overload
def Edit(obj: CSLMetadata) -> MetadataEdit: ...


def Edit(obj: Editable) -> EditObj:
    match obj:
        case CSLTrack():
            return TrackEdit(
                obj,
                artist_credits=[*obj.artist_credits],
                groups=[*obj.groups],
                name=obj.name,
                original_artist=obj.original_simple_artist,
                original_name=obj.original_name,
                song=obj.song,
                type_id=obj.type_id,
            )
        case CSLMetadata():
            return MetadataEdit(
                obj,
                override=obj.override,
                artist_credits=[SimpleArtistCredit.simplify(cred) for cred in obj.artist_credits],
                extra_metas=[SimpleExtraMeta.simplify(meta) for meta in obj.extra_metas],
            )
    raise ValueError('Object is not editable')


@define
class TrackEdit:
    orig: CSLTrack
    artist_credits: list[CSLTrackArtistCredit]
    groups: list[CSLGroup]
    name: str | None
    original_artist: str
    original_name: str
    song: CSLSongSample | None
    type_id: int

    @property
    def type(self) -> str:
        return TRACK_TYPE[self.type_id]

    @type.setter
    def type(self, new_type: str) -> None:
        self.type_id = REVERSE_TRACK_TYPE[new_type]

    def __rich_repr__(self) -> Iterator[tuple[Any, ...]]:
        yield 'id', self.orig.id
        yield 'artist_credits', self.artist_credits, self.orig.artist_credits
        yield 'groups', self.groups, self.orig.groups
        yield 'name', self.name, self.orig.name
        yield 'original_artist', self.original_artist, self.orig.original_simple_artist
        yield 'original_name', self.original_name, self.orig.original_name
        yield 'song', self.song and self.song.name, self.orig.song and self.orig.song.name
        yield 'type', self.type, self.orig.type


@frozen
class SimpleArtistCredit:
    artist: CSLArtistSample
    type: str
    credit: str | None = None

    @classmethod
    def simplify(cls, obj: CSLSongArtistCredit):
        return cls(artist=obj.artist, type=obj.type, credit=None)


@frozen
class SimpleExtraMeta:
    id: str
    is_artist: bool
    type: str
    value: str

    @classmethod
    def simplify(cls, obj: CSLExtraMetadata):
        return cls(id=obj.id, is_artist=obj.type == 'Artist', type=obj.key, value=obj.value)


@define
class MetadataEdit:
    orig: CSLMetadata
    override: bool
    artist_credits: list[SimpleArtistCredit]
    extra_metas: list[SimpleExtraMeta]

    def __rich_repr__(self) -> Iterator[tuple[Any, ...]]:
        yield 'override', self.override, self.orig.override
        yield 'artist_credits', self.artist_credits, self.orig.artist_credits
        yield 'extra_metas', self.extra_metas, self.orig.extra_metas
