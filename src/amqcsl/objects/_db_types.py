import datetime as dt
from typing import override

import rich.repr
from attrs import frozen

from ._obj_consts import (
    ARTIST_TYPE,
    EXTRA_METADATA_TYPE,
    SONG_RELATION_TYPE,
    TRACK_TYPE,
    ArtistType,
    ExtraMetadataType,
    SongRelationType,
    TrackType,
)

# --- DB Mirrors ---


@frozen
class CSLSongSample:
    id: str
    name: str
    disambiguation: str | None
    str_created_at: str

    @property
    def created_at(self) -> dt.datetime:
        return dt.datetime.fromisoformat(self.str_created_at)


@frozen
class CSLArtistSample:
    id: str
    name: str
    original_name: str
    disambiguation: str | None
    type_id: int

    @property
    def type(self) -> ArtistType:
        return ARTIST_TYPE[self.type_id]

    def to_sample(self) -> 'CSLArtistSample':
        """Return a hashable sample, stripping full artist relations when called on CSLArtist."""
        return CSLArtistSample(self.id, self.name, self.original_name, self.disambiguation, self.type_id)


@frozen
class CSLExtraMetadata:
    id: str
    type_id: int
    key: str
    value: str

    @property
    def type(self) -> ExtraMetadataType:
        return EXTRA_METADATA_TYPE[self.type_id]

    @override
    def __str__(self) -> str:
        return f'{self.type} {self.key} {self.value}'

    def __rich_repr__(self) -> rich.repr.RichReprResult:
        yield 'id', self.id
        yield 'type', self.type
        yield 'key', self.key
        yield 'value', self.value


@frozen
class CSLSongArtistCredit:
    id: str
    type: str
    artist: CSLArtistSample

    @override
    def __str__(self) -> str:
        return f'{self.type} {self.artist}'


@frozen
class CSLSongRelation:
    id: str
    type_id: int
    artist: CSLArtistSample

    @property
    def type(self) -> SongRelationType:
        return SONG_RELATION_TYPE[self.type_id]


@frozen
class CSLTrackRef:
    id: str


@frozen
class CSLTrackArtistCredit:
    artist: CSLArtistSample
    name: str
    join_phrase: str
    position: int


@frozen
class CSLTrackLink(CSLTrackRef):
    name: str | None
    artists: list[CSLTrackArtistCredit]

    def ref(self) -> CSLTrackRef:
        return CSLTrackRef(self.id)


@frozen
class CSLList:
    id: str
    name: str
    count: int


@frozen
class CSLGroup:
    id: str
    name: str


@frozen
class CSLArtist(CSLArtistSample):
    forward_relations: list[CSLSongRelation]
    reverse_relations: list[CSLSongRelation]
    linked_amq_songs: list[CSLTrackLink]
    linked_tracks: list[CSLTrackLink]


@frozen
class CSLSong(CSLSongSample):
    artist_credits: list[CSLSongArtistCredit]
    extra_metas: list[CSLExtraMetadata]


@frozen
class SimpleCSLTrack(CSLTrackRef):
    name: str | None
    original_simple_artist: str


@frozen
class CSLTrack(CSLTrackRef):
    name: str | None
    original_name: str
    original_simple_artist: str
    original_album: str | None
    album: str
    track_number: int
    track_total: int
    disc_number: int
    disc_total: int
    year: int | None
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
    def type(self) -> TrackType:
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

    @property
    def audio_url(self) -> str:
        return f'https://amqbot.082640.xyz/files/{self.audio_name}'

    @property
    def simp(self) -> SimpleCSLTrack:
        return SimpleCSLTrack(self.id, self.name, self.original_simple_artist)


@frozen
class CSLMetadata:
    override: bool
    artist_credits: list[CSLSongArtistCredit]
    extra_metas: list[CSLExtraMetadata]
    total_count: int
    fields: list[str]


# --- Edits ---
# Classes for users to use to edit the DB

# This is for making requests to edit existing metadata
type Metadata = ArtistCredit | ExtraMetadata


@frozen
class ArtistCredit:
    artist: CSLArtistSample
    type: str
    credit: str | None = None

    @classmethod
    def simplify(cls, cred: CSLSongArtistCredit):
        return cls(artist=cred.artist, type=cred.type, credit=None)


@frozen
class ExtraMetadata:
    is_artist: bool
    type: str
    value: str

    @classmethod
    def simplify(cls, meta: CSLExtraMetadata):
        return cls(is_artist=meta.type == 'Artist', type=meta.key, value=meta.value)


@frozen
class NewSong:
    name: str
    disambiguation: str | None = None


@frozen
class TrackPutArtistCredit:
    artist: CSLArtistSample
    join_phrase: str = ''
    _name: str | None = None

    @property
    def name(self):
        return self.artist.name if self._name is None else self._name

    @classmethod
    def simplify(cls, cred: CSLTrackArtistCredit):
        return cls(cred.artist, cred.join_phrase, cred.name)


@frozen
class AlbumTrack:
    name: str
    original_name: str
    original_artist: str
