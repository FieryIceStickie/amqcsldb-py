from __future__ import annotations

from typing import TypedDict

type JSONType = str | int | float | bool | None | dict[str, JSONType] | list[JSONType]


# --- List ---


class JSONList(TypedDict):
    id: str
    name: str
    count: int


# --- Group ---


class JSONGroup(TypedDict):
    id: str
    name: str


# --- Metadata ---


class JSONMetadata(TypedDict):
    override: bool
    artistCredits: list[JSONSongArtistCredit]
    extraMetas: list[JSONExtraMetadata]
    totalCount: int
    fields: list[str]


class JSONExtraMetadata(TypedDict):
    id: str
    type: int
    key: str
    value: str


# --- Artist ---


class JSONArtistSample(TypedDict):
    id: str
    name: str
    originalName: str
    disambiguation: str | None
    type: int


class JSONArtist(JSONArtistSample):
    forwardRelations: list[JSONSongRelation]
    reverseRelations: list[JSONSongRelation]
    linkedAmqSongs: list[JSONTrackLink]
    linkedTracks: list[JSONTrackLink]


class JSONSongRelation(TypedDict):
    id: str
    type: int
    artist: JSONArtistSample


class JSONTrackLink(TypedDict):
    id: str
    name: str | None
    artists: list[JSONTrackArtistCredit]


# --- Song ---


class JSONSongSample(TypedDict):
    id: str
    name: str
    disambiguation: str
    createdAt: str


class JSONSong(JSONSongSample):
    artistCredits: list[JSONSongArtistCredit]
    extraMetas: list[JSONExtraMetadata]


class JSONSongArtistCredit(TypedDict):
    id: str
    type: str
    artist: JSONArtistSample


# --- Track ---


class JSONTrack(TypedDict):
    id: str
    name: str | None
    originalName: str
    originalSimpleArtist: str
    originalAlbum: str | None
    album: str
    trackNumber: int
    trackTotal: int
    discNumber: int
    discTotal: int
    year: int | None
    song: JSONSongSample | None
    artistCredits: list[JSONTrackArtistCredit]
    groups: list[JSONGroup]
    audioId: str | None
    audioName: str | None
    disabled: bool
    type: int
    createdAt: str
    updatedAt: str
    inList: bool


class JSONTrackArtistCredit(TypedDict):
    artist: JSONArtistSample
    name: str
    joinPhrase: str
    position: int


# --- Requests ---


class Query(TypedDict):
    skip: int
    take: int


class QueryParamsSong(TypedDict):
    searchTerm: str
    skip: int
    take: int
    orderBy: str
    filter: str


class QueryParamsArtist(TypedDict):
    searchTerm: str
    skip: int
    take: int
    orderBy: str
    filter: str


class QueryBodyTrack(TypedDict):
    activeListId: str | None
    filter: str
    groupFilters: list[str]
    orderBy: str
    quickFilters: list[int]
    searchTerm: str
    skip: int
    take: int


class MetadataPostBody(TypedDict):
    artistCredits: list[MetadataPostArtistCredit]
    extraMetadatas: list[MetadataPostExtraMetadata]
    id: str
    override: bool | None


class MetadataPostArtistCredit(TypedDict):
    artistId: str
    credit: str | None
    type: str


class MetadataPostExtraMetadata(TypedDict):
    isArtist: bool
    type: str
    value: str

class TrackPutBody(TypedDict):
    artistCredits: list[JSONTrackPutArtistCredit] | None
    batchSongIds: list[str] | None
    groupIds: list[str] | None
    id: str
    name: str | None
    newSong: JSONSongSample | None
    originalArtist: str | None
    originalName: str | None
    songId: str | None
    type: int | None


class JSONTrackPutArtistCredit(TypedDict):
    artistId: str
    joinPhrase: str
    name: str
    position: int
