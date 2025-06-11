from typing import TypedDict

type JSONType = str | int | float | bool | None | dict[str, JSONType] | list[JSONType]


# --- DB Mirrors ---


class JSONSongSample(TypedDict):
    id: str
    name: str
    disambiguation: str | None
    createdAt: str


class JSONArtistSample(TypedDict):
    id: str
    name: str
    originalName: str
    disambiguation: str | None
    type: int


class JSONExtraMetadata(TypedDict):
    id: str
    type: int
    key: str
    value: str


class JSONSongArtistCredit(TypedDict):
    id: str
    type: str
    artist: JSONArtistSample


class JSONSongRelation(TypedDict):
    id: str
    type: int
    artist: JSONArtistSample


class JSONTrackArtistCredit(TypedDict):
    artist: JSONArtistSample
    name: str
    joinPhrase: str
    position: int


class JSONTrackLink(TypedDict):
    id: str
    name: str | None
    artists: list[JSONTrackArtistCredit]


class JSONList(TypedDict):
    id: str
    name: str
    count: int


class JSONGroup(TypedDict):
    id: str
    name: str


class JSONArtist(JSONArtistSample):
    forwardRelations: list[JSONSongRelation]
    reverseRelations: list[JSONSongRelation]
    linkedAmqSongs: list[JSONTrackLink]
    linkedTracks: list[JSONTrackLink]


class JSONSong(JSONSongSample):
    artistCredits: list[JSONSongArtistCredit]
    extraMetas: list[JSONExtraMetadata]


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


class JSONMetadata(TypedDict):
    override: bool
    artistCredits: list[JSONSongArtistCredit]
    extraMetas: list[JSONExtraMetadata]
    totalCount: int
    fields: list[str]


# --- Requests ---

# --- Queries ---


class Query(TypedDict):
    skip: int
    take: int


class QuerySong(TypedDict):
    searchTerm: str
    skip: int
    take: int
    orderBy: str
    filter: str


class QueryArtist(TypedDict):
    searchTerm: str
    skip: int
    take: int
    orderBy: str
    filter: str


class QueryTrack(TypedDict):
    activeListId: str | None
    filter: str
    groupFilters: list[str]
    orderBy: str
    quickFilters: list[int]
    searchTerm: str
    skip: int
    take: int


# --- Metadata ---


class MetadataPostArtistCredit(TypedDict):
    artistId: str
    credit: str | None
    type: str


class MetadataPostExtraMetadata(TypedDict):
    isArtist: bool
    type: str
    value: str


class MetadataPostBody(TypedDict):
    artistCredits: list[MetadataPostArtistCredit]
    extraMetadatas: list[MetadataPostExtraMetadata]
    id: str
    override: bool | None


# --- Track ---


class JSONTrackPutArtistCredit(TypedDict):
    artistId: str
    joinPhrase: str
    name: str
    position: int


class TrackNewSong(TypedDict):
    name: str
    disambiguation: str | None


class TrackPutBody(TypedDict):
    artistCredits: list[JSONTrackPutArtistCredit] | None
    batchSongIds: list[str] | None
    groupIds: list[str] | None
    id: str
    name: str | None
    newSong: TrackNewSong | None
    originalArtist: str | None
    originalName: str | None
    songId: str | None
    type: int | None


# --- Album ---


class JSONAlbumTrack(TypedDict):
    discNumber: int
    name: str
    originalArtist: str
    originalName: str
    trackNumber: int
    trackTotal: int


class AlbumAddBody(TypedDict):
    album: str
    discTotal: int
    groupIds: list[str]
    originalAlbum: str
    tracks: list[JSONAlbumTrack]
    year: int
