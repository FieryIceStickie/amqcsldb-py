import logging
import mimetypes
from collections.abc import Iterable, Sequence
from functools import cached_property
from pathlib import Path
from typing import override

import httpx
import rich.repr
from attr.validators import optional
from attrs import Attribute, field, frozen
from attrs.validators import deep_iterable, gt, in_, instance_of, min_len

from amqcsl.exceptions import LoginError, QueryError
from amqcsl.objects._db_types import (
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
    CSLSongSample,
    CSLTrack,
    ExtraMetadata,
    Metadata,
    NewSong,
    TrackPutArtistCredit,
)
from amqcsl.objects._json_types import AlbumAddBody, MetadataPostBody, SongMetadataPostBody, TrackPutBody
from amqcsl.objects._obj_consts import EMPTY_ID, REVERSE_TRACK_TYPE, TrackType

from ._core import Bundle, SingleVendor, httpxClient

logger = logging.getLogger('amqcsl.client')


@frozen
class AuthBundle(Bundle[None]):
    username: str | None = field(validator=optional(instance_of(str)))
    password: str | None = field(repr=False, validator=optional(instance_of(str)))
    session_path: Path = field(validator=instance_of(Path))

    def get_session_cookie(self) -> str:
        """Get the session cookie from the file

        Returns:
            the session cookie

        Raises:
            FileNotFoundError: File doesn't exist, or path is a directory
        """
        if self.session_path.is_dir():
            raise FileNotFoundError('session_path must not be a directory')
        logger.info('Retrieving session cookie')
        try:
            with open(self.session_path, 'r') as file:
                return file.read().strip()
        except FileNotFoundError:
            return ''

    def login(self, client: httpxClient) -> SingleVendor[None]:
        """Attempt login, saves session_id to file if successful

        Args:
            client: HTTPx client

        Raises:
            LoginError: If the username and password are invalid
        """
        if not all((self.username, self.password)):
            raise LoginError('Username and password must not be empty')
        body = {
            'username': self.username,
            'password': self.password,
        }
        res = yield client.build_request('POST', '/api/login', json=body)
        if res.status_code == 403:
            raise LoginError('Invalid login credentials')
        logger.info(f'Writing session_id to {self.session_path}')
        session_id = res.cookies['session-id']
        with open(self.session_path, 'w') as file:
            file.write(session_id)

    @override
    def vendor(self, client: httpxClient) -> SingleVendor[None]:
        """Verify that the user login info is correct and user has admin

        Raises:
            LoginError: If login fails for any expected reason
            RuntimeError: If login fails for an unexpected reason
        """
        session_cookie = self.get_session_cookie()
        client.cookies.set('session-id', session_cookie)
        res: httpx.Response | None = None

        try:
            is_valid_cookie = bool(session_cookie)
            if is_valid_cookie:
                logger.info('Trying session cookie')
                logger.debug('SESSION-ID', extra={'session-id': session_cookie})
                res = yield client.build_request('GET', '/api/auth/me')
                is_valid_cookie = res.status_code != 401

            if not is_valid_cookie:
                logger.info('Invalid session cookie, attempting login')
                client.cookies.delete('session-id')
                yield from self.login(client)
                res = yield client.build_request('GET', '/api/auth/me')

            if res is None:
                raise RuntimeError('Unexpected branch')
            res.raise_for_status()
        except httpx.RequestError as e:
            logger.exception(f'Bad request during auth: {e}')
            raise
        except httpx.HTTPStatusError as e:
            logger.exception(f'Bad response during auth: {e.response.status_code}')
            raise
        except LoginError as e:
            logger.exception(f'Error during login of user {self.username}: {e}')
            raise
        except Exception:
            logger.exception('Unexpected error during auth')
            raise

        logger.info('Auth successful')
        if 'ADMIN' not in res.json()['roles']:
            raise LoginError(f'User {res.json()["name"]} does not have admin privileges')

    @override
    def __rich_repr__(self) -> rich.repr.Result:
        yield 'username', self.username
        yield 'session_path', self.session_path


@frozen
class LogoutBundle(Bundle[None]):
    session_path: Path = field(validator=instance_of(Path))

    @override
    def vendor(self, client: httpxClient) -> SingleVendor[None]:
        logger.info('Logging out the client')
        res = yield client.build_request('POST', '/api/logout')
        res.raise_for_status()

        logger.info('Logout successful')
        with open(self.session_path, 'w'):
            pass

    @override
    def __rich_repr__(self) -> rich.repr.Result:
        yield 'session_path', self.session_path


type CSLLists = dict[str, CSLList]


@frozen
class ListBundle(Bundle[CSLLists]):
    @override
    def vendor(self, client: httpxClient) -> SingleVendor[CSLLists]:
        logger.info('Fetching lists')
        res = yield client.build_request('GET', '/api/lists')
        res.raise_for_status()
        rtn: CSLLists = {}
        for data in res.json():
            csl_list = CSLList.from_json(data)
            rtn[csl_list.name] = csl_list
        return rtn

    @override
    def __rich_repr__(self) -> rich.repr.Result:
        return
        yield


type CSLGroups = dict[str, CSLGroup]


@frozen
class GroupBundle(Bundle[CSLGroups]):
    @override
    def vendor(self, client: httpxClient) -> SingleVendor[CSLGroups]:
        logger.info('Fetching groups')
        res = yield client.build_request('GET', '/api/groups')
        res.raise_for_status()
        rtn: CSLGroups = {}
        for data in res.json():
            group = CSLGroup.from_json(data)
            rtn[group.name] = group
        return rtn

    @override
    def __rich_repr__(self) -> rich.repr.Result:
        return
        yield


@frozen
class GetSongBundle(Bundle[CSLSong]):
    song: CSLSongSample = field(validator=instance_of(CSLSongSample))

    @override
    def vendor(self, client: httpxClient) -> SingleVendor[CSLSong]:
        song = self.song
        if isinstance(song, CSLSong):
            logger.warning(f'client.get_song called with already filled CSLSong {song.name}')
            return song
        res = yield client.build_request('GET', f'/api/song/{song.id}')
        res.raise_for_status()
        return CSLSong.from_json(res.json())

    @override
    def __rich_repr__(self) -> rich.repr.Result:
        yield 'song', self.song


@frozen
class GetArtistBundle(Bundle[CSLArtist]):
    artist: CSLArtistSample = field(validator=instance_of(CSLArtistSample))

    @override
    def vendor(self, client: httpxClient) -> SingleVendor[CSLArtist]:
        artist = self.artist
        if isinstance(artist, CSLArtist):
            logger.warning(f'client.get_artist called with already filled CSLArtist {artist.name}')
            return artist
        res = yield client.build_request('GET', f'/api/artist/{artist.id}')
        res.raise_for_status()
        return CSLArtist.from_json(res.json())

    @override
    def __rich_repr__(self) -> rich.repr.Result:
        yield 'artist', self.artist


@frozen
class GetMetadataBundle(Bundle[CSLMetadata | None]):
    track: CSLTrack = field(validator=instance_of(CSLTrack))

    @override
    def vendor(self, client: httpxClient) -> SingleVendor[CSLMetadata | None]:
        res = yield client.build_request('GET', f'/api/track/{self.track.id}/metadata')
        match res.json():
            case {'statusCode': 404, 'errors': {'generalErrors': ['Song does not have metadata']}}:
                return None
            case _:
                res.raise_for_status()
                return CSLMetadata.from_json(res.json())

    @override
    def __rich_repr__(self) -> rich.repr.Result:
        yield 'track', self.track.simp


@frozen
class CreateListBundle(Bundle[None]):
    name: str = field(validator=[instance_of(str), min_len(1)])
    csl_lists: Iterable[CSLList] = field(default=(), validator=deep_iterable(instance_of(CSLList)))

    @override
    def vendor(self, client: httpxClient) -> SingleVendor[None]:
        logger.info(f'Creating list {self.name}')
        body = {
            'importListIds': [csl_list.id for csl_list in self.csl_lists],
            'name': self.name,
        }
        res = yield client.build_request('POST', '/api/list', json=body)
        res.raise_for_status()
        logger.info(f'List {self.name} created')

    @override
    def __rich_repr__(self) -> rich.repr.Result:
        yield 'name', self.name
        yield 'lists', self.csl_lists, []


@frozen
class ListEditBundle(Bundle[None]):
    csl_list: CSLList = field(validator=instance_of(CSLList))
    name: str | None = field(default=None, validator=optional([instance_of(str), min_len(1)]))
    add: Iterable[CSLTrack] = field(default=(), validator=deep_iterable(instance_of(CSLTrack)))
    remove: Iterable[CSLTrack] = field(default=(), validator=deep_iterable(instance_of(CSLTrack)))

    @override
    def vendor(self, client: httpxClient) -> SingleVendor[None]:
        csl_list = self.csl_list
        logger.info(f'Editing list {csl_list.name}')
        body = {
            'addSongIds': [track.id for track in self.add],
            'id': EMPTY_ID,
            'name': self.name,
            'removeSongIds': [track.id for track in self.remove],
        }
        res = yield client.build_request('PUT', f'/api/list/{csl_list.id}', json=body)
        res.raise_for_status()

    @override
    def __rich_repr__(self) -> rich.repr.Result:
        yield 'list', self.csl_list
        yield 'new_name', self.name, None


@frozen
class CreateGroupBundle(Bundle[CSLGroup]):
    name: str = field(validator=[instance_of(str), min_len(1)])

    @override
    def vendor(self, client: httpxClient) -> SingleVendor[CSLGroup]:
        logger.info(f'Adding group {self.name}')
        res = yield client.build_request('POST', '/api/group', json={'name': self.name})
        res.raise_for_status()
        return CSLGroup.from_json(res.json())

    @override
    def __rich_repr__(self) -> rich.repr.Result:
        yield 'name', self.name


@frozen
class GroupEditBundle(Bundle[None]):
    group: CSLGroup = field(validator=instance_of(CSLGroup))
    name: str = field(validator=[instance_of(str), min_len(1)])

    @override
    def vendor(self, client: httpxClient) -> SingleVendor[None]:
        logger.info(f'Editing group {self.group.name}')
        body = {
            'id': EMPTY_ID,
            'name': self.name,
        }
        res = yield client.build_request('PUT', f'/api/group/{self.group.id}', json=body)
        res.raise_for_status()

    @override
    def __rich_repr__(self) -> rich.repr.Result:
        yield 'group', self.group
        yield 'new_name', self.name, None


@frozen
class GroupDeleteBundle(Bundle[None]):
    group: CSLGroup = field(validator=instance_of(CSLGroup))

    @override
    def vendor(self, client: httpxClient) -> SingleVendor[None]:
        logger.info(f'Deleting group {self.group.name}')
        res = yield client.build_request('DELETE', f'/api/group/{self.group.id}')
        res.raise_for_status()

    @override
    def __rich_repr__(self) -> rich.repr.Result:
        yield 'group', self.group


@frozen
class SongEditBundle(Bundle[None]):
    song: CSLSong = field(validator=instance_of(CSLSong))
    name: str | None = field(validator=optional(instance_of(str)))
    disambiguation: str | None = field(validator=optional(instance_of(str)))

    @override
    def vendor(self, client: httpxClient) -> SingleVendor[None]:
        logger.info(f'Editing song {self.song.name}')
        body = {
            'id': EMPTY_ID,
            'name': self.name if self.name else self.song.name,
            'disambiguation': self.disambiguation if self.disambiguation else self.song.disambiguation,
        }
        res = yield client.build_request('PUT', f'/api/group/{self.song.id}', json=body)
        res.raise_for_status()

    @override
    def __rich_repr__(self) -> rich.repr.Result:
        yield 'song', self.song
        yield 'new_name', self.name, self.song.name
        yield 'new_disambiguation', self.name, self.song.disambiguation


@frozen
class SongDeleteBundle(Bundle[None]):
    song: CSLSong = field(validator=instance_of(CSLSong))

    @override
    def vendor(self, client: httpxClient) -> SingleVendor[None]:
        logger.info(f'Deleting song {self.song.name}')
        res = yield client.build_request('DELETE', f'/api/song/{self.song.id}')
        res.raise_for_status()

    @override
    def __rich_repr__(self) -> rich.repr.Result:
        yield 'song', self.song


@frozen
class SongAddMetadataBundle(Bundle[None]):
    song: CSLSong = field(validator=instance_of(CSLSong))
    metas: Iterable[Metadata] = field(validator=deep_iterable(instance_of((ArtistCredit, ExtraMetadata))))

    @cached_property
    def filtered_metas(self) -> tuple[Sequence[ArtistCredit], Sequence[ExtraMetadata]]:
        artist_credits: list[ArtistCredit] = []
        extra_metadata: list[ExtraMetadata] = []
        for meta in self.metas:
            match meta:
                case ArtistCredit():
                    artist_credits.append(meta)
                case ExtraMetadata():
                    extra_metadata.append(meta)
                case _:
                    raise ValueError('metas must be ArtistCredit or ExtraMetadata')
        return artist_credits, extra_metadata

    @override
    def vendor(self, client: httpxClient) -> SingleVendor[None]:
        logger.info(f'Queuing metadata edit on {self.song.name}')
        artist_credits, extra_metadata = self.filtered_metas
        body: SongMetadataPostBody = {
            'id': self.song.id,
            'artistCredits': [meta.to_json() for meta in artist_credits],
            'extraMetadatas': [meta.to_json() for meta in extra_metadata],
        }
        res = yield client.build_request('POST', f'/api/song/{self.song.id}', json=body)
        res.raise_for_status()

    @override
    def __rich_repr__(self) -> rich.repr.Result:
        yield 'song', self.song
        artist_credits, extra_metadata = self.filtered_metas
        yield 'artist_credits', artist_credits, []
        yield 'extra_metadata', extra_metadata, []


@frozen
class SongDeleteMetadataBundle(Bundle[None]):
    song: CSLSong = field(validator=instance_of(CSLSong))
    meta: CSLSongArtistCredit | CSLExtraMetadata = field(validator=instance_of((CSLSongArtistCredit, CSLExtraMetadata)))

    @override
    def vendor(self, client: httpxClient) -> SingleVendor[None]:
        logger.info(f'Removing metadata {self.meta} from song {self.song.name}')
        res = yield client.build_request('DELETE', f'/api/track/{self.song.id}/metadata/{self.meta.id}')
        res.raise_for_status()

    @override
    def __rich_repr__(self) -> rich.repr.Result:
        yield 'track', self.song.name
        yield 'meta', self.meta


@frozen
class TrackAddMetadataBundle(Bundle[None]):
    track: CSLTrack = field(validator=instance_of(CSLTrack))
    metas: Iterable[Metadata] = field(validator=deep_iterable(instance_of((ArtistCredit, ExtraMetadata))))
    _override: bool | None = field(default=None, validator=optional(instance_of(bool)))
    existing_meta: CSLMetadata | None = field(default=None, validator=optional(instance_of(CSLMetadata)))

    @cached_property
    def filtered_metas(self) -> tuple[Sequence[ArtistCredit], Sequence[ExtraMetadata]]:
        current_metas: set[Metadata]
        if self.existing_meta is None:
            current_metas = set()
        else:
            current_metas = {
                *map(ArtistCredit.simplify, self.existing_meta.artist_credits),
                *map(ExtraMetadata.simplify, self.existing_meta.extra_metas),
            }

        artist_credits: list[ArtistCredit] = []
        extra_metadata: list[ExtraMetadata] = []
        for meta in self.metas:
            match meta:
                case ArtistCredit():
                    if meta not in current_metas:
                        logger.debug(f'Adding artist credit {meta.type} {meta.artist.name}')
                        artist_credits.append(meta)
                case ExtraMetadata():
                    if meta not in current_metas:
                        logger.debug(f'Adding extra metadata {meta.type}: {meta.value}')
                        extra_metadata.append(meta)
                case _:
                    raise ValueError('metas must be ArtistCredit or ExtraMetadata')
            current_metas.add(meta)
        return artist_credits, extra_metadata

    def __len__(self) -> int:
        artist_credits, extra_metadata = self.filtered_metas
        return len(artist_credits) + len(extra_metadata)

    @override
    def vendor(self, client: httpxClient) -> SingleVendor[None]:
        track = self.track
        logger.info(f'Queuing metadata edit on {track.name}')

        artist_credits, extra_metadata = self.filtered_metas
        if self._override is None and not self:
            logger.info('No changes necessary, skipping request')
            return
        body: MetadataPostBody = {
            'artistCredits': [meta.to_json() for meta in artist_credits],
            'extraMetadatas': [meta.to_json() for meta in extra_metadata],
            'id': EMPTY_ID,
            'override': self._override,
        }
        res = yield client.build_request('POST', f'/api/track/{track.id}/metadata', json=body)
        res.raise_for_status()

    @override
    def __rich_repr__(self) -> rich.repr.Result:
        yield 'track', self.track.simp
        yield 'override', self._override, None
        artist_credits, extra_metadata = self.filtered_metas
        yield 'artist_credits', artist_credits, []
        yield 'extra_metadata', extra_metadata, []


@frozen
class TrackDeleteMetadataBundle(Bundle[None]):
    track: CSLTrack = field(validator=instance_of(CSLTrack))
    meta: CSLSongArtistCredit | CSLExtraMetadata = field(validator=instance_of((CSLSongArtistCredit, CSLExtraMetadata)))

    @override
    def vendor(self, client: httpxClient) -> SingleVendor[None]:
        logger.info(f'Removing metadata {self.meta} from track {self.track.name}')
        res = yield client.build_request('DELETE', f'/api/track/{self.track.id}/metadata/{self.meta.id}')
        res.raise_for_status()

    @override
    def __rich_repr__(self) -> rich.repr.Result:
        yield 'track', self.track.simp
        yield 'meta', self.meta


@frozen
class TrackEditBundle(Bundle[None]):
    track: CSLTrack = field(validator=instance_of(CSLTrack))
    artist_credits: Sequence[TrackPutArtistCredit] | None = field(  # type: ignore[reportUnknownArgumentType]
        default=None,
        validator=optional(deep_iterable(instance_of(TrackPutArtistCredit))),  # type: ignore[reportUnknownArgumentType]
    )
    groups: Sequence[CSLGroup] | None = field(default=None, validator=optional(deep_iterable(instance_of(CSLGroup))))  # type: ignore[reportUnknownArgumentType]
    name: str | None = field(default=None, validator=optional(instance_of(str)))
    original_artist: str | None = field(default=None, validator=optional(instance_of(str)))
    original_name: str | None = field(default=None, validator=optional(instance_of(str)))
    song: NewSong | None = field(default=None, validator=optional(instance_of(NewSong)))
    type: TrackType | None = field(
        default=None,
        validator=optional(in_(REVERSE_TRACK_TYPE)),  # type: ignore[reportUnknownArgumentType]
    )

    @override
    def vendor(self, client: httpxClient) -> SingleVendor[None]:
        track = self.track
        logger.info(f'Editing track {track.name}')
        body: TrackPutBody = {
            'artistCredits': None
            if self.artist_credits is None
            else [v.to_json(i) for i, v in enumerate(self.artist_credits)],
            'batchSongIds': None,
            'groupIds': None if self.groups is None else [group.id for group in self.groups],
            'id': EMPTY_ID,
            'name': self.name,
            'newSong': None if self.song is None else self.song.to_json(),
            'originalArtist': self.original_artist,
            'originalName': self.original_name,
            'songId': getattr(self.song, 'id', None),
            'type': None if self.type is None else REVERSE_TRACK_TYPE[self.type],
        }
        yield client.build_request('PUT', f'/api/track/{track.id}', json=body)

    @override
    def __rich_repr__(self) -> rich.repr.Result:
        yield 'track', self.track.simp
        yield 'artist_credits', self.artist_credits, None
        yield 'groups', self.groups, None
        yield 'name', self.name, None
        yield 'original_artist', self.original_artist, None
        yield 'original_name', self.original_name, None
        yield 'song', self.song, None
        yield 'type', self.type, None


@frozen
class CreateAlbumBundle(Bundle[None]):
    name: str = field(validator=[instance_of(str), min_len(1)])
    original_name: str = field(validator=[instance_of(str), min_len(1)])
    year: int = field(validator=[instance_of(int), gt(0)])
    groups: Iterable[CSLGroup] = field(validator=deep_iterable(instance_of(CSLGroup)))
    tracks: Sequence[Sequence[AlbumTrack]] = field(validator=deep_iterable(deep_iterable(instance_of(AlbumTrack))))  # type: ignore[reportUnknownArgumentType]

    @override
    def vendor(self, client: httpxClient) -> SingleVendor[None]:
        name = self.name
        logger.info(f'Adding album {name}')
        body: AlbumAddBody = {
            'album': name,
            'discTotal': len(self.tracks),
            'groupIds': [group.id for group in self.groups],
            'originalAlbum': self.original_name,
            'year': self.year,
            'tracks': [
                track.to_json(disc_number, track_number, len(disc))
                for disc_number, disc in enumerate(self.tracks, start=1)
                for track_number, track in enumerate(disc, start=1)
            ],
        }
        res = yield client.build_request('POST', '/api/album', json=body)
        res.raise_for_status()

    @override
    def __rich_repr__(self) -> rich.repr.Result:
        yield 'name', self.name
        yield 'original_name', self.original_name
        yield 'year', self.year
        yield 'groups', self.groups
        yield 'tracks', self.tracks


@frozen
class AddAudioBundle(Bundle[None]):
    track: CSLTrack = field(validator=instance_of(CSLTrack))
    audio_path: Path = field(converter=Path)

    @audio_path.validator  # type: ignore
    def check(self, _: 'Attribute[Path]', value: Path) -> None:
        if not value.exists():
            raise QueryError(f'{value.resolve()} does not exist')
        elif not value.is_file():
            raise QueryError(f'{value.resolve()} is not a file')

    @cached_property
    def mime_type(self) -> str:
        mime_type, _ = mimetypes.guess_type(self.audio_path)
        if mime_type is None or not mime_type.startswith('audio/'):
            raise QueryError(f'{self.audio_path.name} is not an audio file')
        return mime_type

    @override
    def vendor(self, client: httpxClient) -> SingleVendor[None]:
        track = self.track
        logger.info(f'Uploading audio to {track.name}')
        res = yield client.build_request('POST', f'/api/track/{track.id}/presigned-upload', json={})
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
        with open(self.audio_path, 'rb') as file:
            res = yield client.build_request(
                'POST',
                url,
                params={'sessionId': session_id, 'key': key},
                files={'file': (self.audio_path.name, file, self.mime_type)},
            )
        res.raise_for_status()

    @override
    def __rich_repr__(self) -> rich.repr.Result:
        yield 'track', self.track.simp
        yield 'audio_path', self.audio_path.resolve()
