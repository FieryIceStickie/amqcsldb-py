from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence
from enum import Enum, auto
from typing import Self, overload, override

from attrs import frozen

from amqcsl.objects._db_types import CSLArtistSample, CSLTrack, ExtraMetadata


class _Wildcard:
    """Wildcard that matches any object, for internal use in ArtistName"""

    instance: Self | None = None

    def __new__(cls) -> Self:
        if cls.instance is None:
            return super().__new__(cls)
        return cls.instance

    @override
    def __eq__(self, other: object) -> bool:
        return True


@frozen
class ArtistName:
    name: str
    original_name: str | None = None
    disambiguation: str | None = None

    @classmethod
    def from_key(cls, artist_key: ArtistKey) -> Self:
        match artist_key:
            case str(name):
                return cls(name)
            case (str(name), str(disam) | (None as disam)):
                return cls(name, disambiguation=disam)
            case ArtistName(name, orig_name, disam):
                return cls(name, orig_name, disam)
            case _:
                raise ValueError(f'Expected artist_key to be of type ArtistKey, received {artist_key!r}')

    def match(self, artist: CSLArtistSample) -> bool:
        orig_name = self.original_name if self.original_name is not None else _Wildcard()
        disam = self.disambiguation if self.disambiguation is not None else _Wildcard()
        return (self.name, orig_name, disam) == (
            artist.name,
            artist.original_name,
            artist.disambiguation,
        )

    @override
    def __str__(self) -> str:
        return (
            f'{self.name}'
            f'{f" <{self.original_name})" if self.original_name is not None else ""}'
            f'{f" ({self.disambiguation})" if self.disambiguation is not None else ""}'
        )


type ArtistKey = ArtistName | tuple[str, str | None] | str
type ArtistDict = Mapping[ArtistKey, str]


@frozen
class _UnknownArtist:
    """The credited artist has no explicit metadata and is not a group."""


UNKNOWN_ARTIST = _UnknownArtist()


@frozen
class INCOMPLETE_GROUP:
    """Unresolved members, including nested groups and cycles."""

    artists: Sequence[CSLArtistSample]
    known_artists: Sequence[CSLArtistSample] = ()


@frozen
class Reason:
    """A failure for an artist credited on the track."""

    artist: CSLArtistSample
    reason: _UnknownArtist | INCOMPLETE_GROUP


class ExcludeDecision(Enum):
    """How to handle unresolved artists on a track."""

    EXCLUDE = auto()
    ERROR = auto()
    IGNORE = auto()


type ShouldExclude = Callable[[CSLTrack, Sequence[Reason]], ExcludeDecision]


type AsyncShouldExclude = Callable[[CSLTrack, Sequence[Reason]], ExcludeDecision | Awaitable[ExcludeDecision]]


class ArtistToMeta(Mapping[CSLArtistSample, Sequence[ExtraMetadata]]):
    """Shared cached artist metadata for synchronous and asynchronous mappings."""

    metadata: dict[CSLArtistSample, Sequence[ExtraMetadata]]
    excluded_artists: set[str]

    def __getitem__(self, artist: CSLArtistSample) -> Sequence[ExtraMetadata]:
        return self.metadata[artist.to_sample()]

    def __iter__(self) -> Iterator[CSLArtistSample]:
        return iter(self.metadata)

    def __len__(self) -> int:
        return len(self.metadata)

    def __contains__(self, artist: object) -> bool:
        return isinstance(artist, CSLArtistSample) and artist.to_sample() in self.metadata

    @overload
    def get(
        self,
        artist: CSLArtistSample,
        default: None = None,
    ) -> Sequence[ExtraMetadata] | None: ...
    @overload
    def get(
        self,
        artist: CSLArtistSample,
        default: Sequence[ExtraMetadata],
    ) -> Sequence[ExtraMetadata]: ...
    @overload
    def get[T](
        self,
        artist: CSLArtistSample,
        default: T,
    ) -> Sequence[ExtraMetadata] | T: ...

    def get[T](
        self,
        artist: CSLArtistSample,
        default: T | None = None,
    ) -> Sequence[ExtraMetadata] | T | None:
        return self.metadata.get(artist.to_sample(), default)
