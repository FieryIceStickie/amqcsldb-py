import logging
from collections.abc import Callable
from operator import attrgetter
from typing import Any, cast, get_origin, overload

from attrs import fields
from cattrs import Converter
from cattrs.cols import list_structure_factory
from cattrs.errors import ClassValidationError, IterableValidationError
from cattrs.gen import make_dict_structure_fn, make_dict_unstructure_fn, override

from amqcsl.exceptions import QueryError

from ._db_types import (
    AlbumTrack,
    ArtistCredit,
    CSLArtist,
    CSLArtistSample,
    CSLExtraMetadata,
    CSLGroup,
    CSLList,
    CSLMetadata,
    CSLSong,
    CSLSongArtistCredit,
    CSLSongRelation,
    CSLSongSample,
    CSLTrack,
    CSLTrackArtistCredit,
    CSLTrackLink,
    ExtraMetadata,
    NewSong,
    TrackPutArtistCredit,
)
from ._json_types import (
    JSONAlbumTrack,
    JSONArtist,
    JSONArtistSample,
    JSONExtraMetadata,
    JSONGroup,
    JSONList,
    JSONMetadata,
    JSONSong,
    JSONSongArtistCredit,
    JSONSongRelation,
    JSONSongSample,
    JSONTrack,
    JSONTrackArtistCredit,
    JSONTrackLink,
    JSONTrackPutArtistCredit,
    MetadataPostArtistCredit,
    MetadataPostExtraMetadata,
    TrackNewSong,
)

logger = logging.getLogger('amqcsl.object')

type DatabaseObject = (
    CSLGroup
    | CSLList
    | CSLArtistSample
    | CSLSongSample
    | CSLExtraMetadata
    | CSLSongArtistCredit
    | CSLSongRelation
    | CSLTrackArtistCredit
    | CSLTrackLink
    | CSLArtist
    | CSLSong
    | CSLTrack
    | CSLMetadata
)
type DatabaseJSON = (
    JSONGroup
    | JSONList
    | JSONArtistSample
    | JSONSongSample
    | JSONExtraMetadata
    | JSONSongArtistCredit
    | JSONSongRelation
    | JSONTrackArtistCredit
    | JSONTrackLink
    | JSONArtist
    | JSONSong
    | JSONTrack
    | JSONMetadata
)

type EditObject = ArtistCredit | ExtraMetadata | NewSong | TrackPutArtistCredit | AlbumTrack
type EditJSON = (
    MetadataPostArtistCredit | MetadataPostExtraMetadata | TrackNewSong | JSONTrackPutArtistCredit | JSONAlbumTrack
)


_converter = Converter()
_object_types: tuple[type[DatabaseObject], ...] = (
    CSLGroup,
    CSLList,
    CSLArtistSample,
    CSLSongSample,
    CSLExtraMetadata,
    CSLSongArtistCredit,
    CSLSongRelation,
    CSLTrackArtistCredit,
    CSLTrackLink,
    CSLArtist,
    CSLSong,
    CSLTrack,
    CSLMetadata,
)
_edit_types: tuple[type[EditObject], ...] = (ArtistCredit, ExtraMetadata, NewSong, TrackPutArtistCredit, AlbumTrack)
_context_fields: dict[type[DatabaseObject | EditObject], dict[str, str]] = {
    TrackPutArtistCredit: {'position': 'position'},
    AlbumTrack: {'disc_number': 'discNumber', 'track_number': 'trackNumber', 'track_total': 'trackTotal'},
}

_field_names = {
    'type_id': 'type',
    'str_created_at': 'createdAt',
    'str_updated_at': 'updatedAt',
    'linked_amq_songs': 'linkedAMQSongs',
}


def _json_key(name: str) -> str:
    """Map Python fields to database keys, including its acronym spellings."""
    if name in _field_names:
        return _field_names[name]
    first, *rest = name.split('_')
    return first + ''.join(part.capitalize() for part in rest)


def _structure_primitive[T](value: object, target: type[T]) -> T:
    """Preserve the existing parsers' type checks without coercing values."""
    if not isinstance(value, target):
        raise TypeError(f'Expected {target.__name__}, got {type(value).__name__}')
    return value


def _list_hook(target: Any) -> Callable[[Any, Any], Any]:
    """Reject strings and mappings where the database specifies arrays."""
    convert = list_structure_factory(target, _converter)

    def structure(value: object, target: Any) -> Any:
        if not isinstance(value, (list, tuple)):
            raise TypeError('Expected a JSON array')
        return convert(value, target)

    return structure


for _primitive in (str, int, bool):
    _converter.register_structure_hook(_primitive, _structure_primitive)
_converter.register_structure_hook_factory(lambda target: get_origin(target) is list, _list_hook)


def _register[T: DatabaseObject](target: type[T]) -> None:
    """Generate database conversions and preserve track credit ordering."""
    overrides: dict[str, Any] = {field.name: override(rename=_json_key(field.name)) for field in fields(target)}
    convert = make_dict_structure_fn(target, _converter, **overrides)

    def structure(value: object, target: type[T]) -> T:
        if not isinstance(value, dict):
            raise TypeError('Expected a JSON object')
        result = convert(cast(dict[str, Any], value), target)
        if isinstance(result, CSLTrack):
            result.artist_credits.sort(key=attrgetter('position'))
        return result

    _converter.register_structure_hook(target, structure)
    _converter.register_unstructure_hook(target, make_dict_unstructure_fn(target, _converter, **overrides))


for _object_type in _object_types:
    _register(_object_type)


def _artist_id(artist: CSLArtistSample) -> str:
    """Flatten the artist reference used in edit request payloads."""
    return artist.id


for _edit_type in _edit_types:
    _overrides: dict[str, Any] = {field.name: override(rename=_json_key(field.name)) for field in fields(_edit_type)}
    if _edit_type in (ArtistCredit, TrackPutArtistCredit):
        _overrides['artist'] = override(rename='artistId', unstruct_hook=_artist_id)
    if _edit_type is TrackPutArtistCredit:
        _overrides['_name'] = override(rename='name')
    _converter.register_unstructure_hook(
        _edit_type,
        make_dict_unstructure_fn(_edit_type, _converter, **_overrides),
    )


@overload
def from_json(data: JSONArtist, target: type[CSLArtist]) -> CSLArtist: ...


@overload
def from_json(data: JSONSong, target: type[CSLSong]) -> CSLSong: ...


@overload
def from_json(data: JSONGroup, target: type[CSLGroup]) -> CSLGroup: ...


@overload
def from_json(data: JSONList, target: type[CSLList]) -> CSLList: ...


@overload
def from_json(data: JSONArtistSample, target: type[CSLArtistSample]) -> CSLArtistSample: ...


@overload
def from_json(data: JSONSongSample, target: type[CSLSongSample]) -> CSLSongSample: ...


@overload
def from_json(data: JSONExtraMetadata, target: type[CSLExtraMetadata]) -> CSLExtraMetadata: ...


@overload
def from_json(data: JSONSongArtistCredit, target: type[CSLSongArtistCredit]) -> CSLSongArtistCredit: ...


@overload
def from_json(data: JSONSongRelation, target: type[CSLSongRelation]) -> CSLSongRelation: ...


@overload
def from_json(data: JSONTrackArtistCredit, target: type[CSLTrackArtistCredit]) -> CSLTrackArtistCredit: ...


@overload
def from_json(data: JSONTrackLink, target: type[CSLTrackLink]) -> CSLTrackLink: ...


@overload
def from_json(data: JSONTrack, target: type[CSLTrack]) -> CSLTrack: ...


@overload
def from_json(data: JSONMetadata, target: type[CSLMetadata]) -> CSLMetadata: ...


def from_json(data: DatabaseJSON, target: type[DatabaseObject]) -> DatabaseObject:
    """Convert database JSON to the requested object, raising QueryError for invalid data."""
    if target not in _object_types:
        raise TypeError(f'Unsupported database object: {target!r}')
    try:
        return _converter.structure(data, target)
    except (ClassValidationError, IterableValidationError, TypeError, ValueError, KeyError) as error:
        message = f'Invalid json when parsing {target.__name__}'
        logger.info(message, extra={'json': data})
        raise QueryError(message) from error


@overload
def to_json(obj: CSLArtist) -> JSONArtist: ...


@overload
def to_json(obj: CSLSong) -> JSONSong: ...


@overload
def to_json(obj: CSLGroup) -> JSONGroup: ...


@overload
def to_json(obj: CSLList) -> JSONList: ...


@overload
def to_json(obj: CSLArtistSample) -> JSONArtistSample: ...


@overload
def to_json(obj: CSLSongSample) -> JSONSongSample: ...


@overload
def to_json(obj: CSLExtraMetadata) -> JSONExtraMetadata: ...


@overload
def to_json(obj: CSLSongArtistCredit) -> JSONSongArtistCredit: ...


@overload
def to_json(obj: CSLSongRelation) -> JSONSongRelation: ...


@overload
def to_json(obj: CSLTrackArtistCredit) -> JSONTrackArtistCredit: ...


@overload
def to_json(obj: CSLTrackLink) -> JSONTrackLink: ...


@overload
def to_json(obj: CSLTrack) -> JSONTrack: ...


@overload
def to_json(obj: CSLMetadata) -> JSONMetadata: ...


@overload
def to_json(obj: ArtistCredit) -> MetadataPostArtistCredit: ...


@overload
def to_json(obj: ExtraMetadata) -> MetadataPostExtraMetadata: ...


@overload
def to_json(obj: NewSong) -> TrackNewSong: ...


@overload
def to_json(obj: TrackPutArtistCredit, *, position: int) -> JSONTrackPutArtistCredit: ...


@overload
def to_json(
    obj: AlbumTrack,
    *,
    disc_number: int,
    track_number: int,
    track_total: int,
) -> JSONAlbumTrack: ...


def to_json(obj: DatabaseObject | EditObject, **kwargs: Any) -> DatabaseJSON | EditJSON:
    """Serialize an object; edit payloads require their positioning arguments as keywords."""
    if type(obj) not in (*_object_types, *_edit_types):
        raise TypeError(f'Unsupported object: {type(obj)!r}')
    context_fields = _context_fields.get(type(obj), {})
    missing = {*context_fields} - {*kwargs}
    unexpected = {*kwargs} - {*context_fields}
    if missing:
        raise TypeError(f'to_json() missing required keyword arguments: {", ".join(sorted(missing))}')
    if unexpected:
        raise TypeError(f'to_json() got unexpected keyword arguments: {", ".join(sorted(unexpected))}')
    payload = cast(dict[str, Any], _converter.unstructure(obj))
    if isinstance(obj, TrackPutArtistCredit):
        # Use the artist's name when _name is None, while preserving explicit empty names.
        payload['name'] = obj.name
    payload.update({context_fields[key]: value for key, value in kwargs.items()})
    return cast(DatabaseJSON | EditJSON, payload)
