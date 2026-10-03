import logging
import mimetypes
from collections.abc import Sequence
from functools import cached_property
from pathlib import Path
from typing import cast, override
from urllib.parse import urlsplit

import niquests
import rich.repr
from attrs import Attribute, field, frozen
from attrs.validators import gt, min_len, optional

from amqcsl.clients._client_consts import DB_URL
from amqcsl.clients._http_utils import AsyncMultipartUpload, MultipartUpload, build_request
from amqcsl.exceptions import LoginError, QueryError
from amqcsl.objects._conversion import (
    from_json,
    to_json,
)
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
    CSLTrackRef,
    ExtraMetadata,
    Metadata,
    NewSong,
    TrackPutArtistCredit,
)
from amqcsl.objects._json_types import (
    AlbumAddBody,
    JSONArtist,
    JSONGroup,
    JSONList,
    JSONMetadata,
    JSONSong,
    MetadataPostBody,
    SongMetadataPostBody,
    TrackPutBody,
)
from amqcsl.objects._obj_consts import EMPTY_ID, REVERSE_TRACK_TYPE, TrackType

from ._core import Bundle, SingleVendor, httpClient, materialize

logger = logging.getLogger('amqcsl.client')


@frozen
class AuthBundle(Bundle[None]):
    username: str | None
    password: str | None = field(repr=False)
    session_path: Path

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

    def login(self, client: httpClient) -> SingleVendor[None]:
        """Attempt login, saves session_id to file if successful

        Args:
            client: HTTP session

        Raises:
            LoginError: If the username and password are invalid
        """
        if not all((self.username, self.password)):
            raise LoginError('Username and password must not be empty')
        body = {
            'username': self.username,
            'password': self.password,
        }
        res = yield build_request(client, 'POST', '/api/login', json=body)
        if res.status_code == 403:
            raise LoginError('Invalid login credentials')
        res.raise_for_status()
        logger.info(f'Writing session_id to {self.session_path}')
        session_id = res.cookies['session-id']
        with open(self.session_path, 'w') as file:
            file.write(session_id)

    @override
    def vendor(self, client: httpClient) -> SingleVendor[None]:
        """Verify that the user login info is correct and user has admin

        Raises:
            LoginError: If login fails for any expected reason
            RuntimeError: If login fails for an unexpected reason
        """
        session_cookie = self.get_session_cookie()
        host = urlsplit(DB_URL).hostname
        assert host is not None
        client.cookies.set(  # type: ignore[reportUnknownMemberType]
            'session-id',
            session_cookie,
            domain=host,
            path='/',
            secure=True,
        )
        res: niquests.Response | None = None

        try:
            is_valid_cookie = bool(session_cookie)
            if is_valid_cookie:
                logger.info('Trying session cookie')
                res = yield build_request(client, 'GET', '/api/auth/me')
                is_valid_cookie = res.status_code != 401

            if not is_valid_cookie:
                logger.info('Invalid session cookie, attempting login')
                for cookie in [*client.cookies]:
                    if cookie.name == 'session-id':
                        client.cookies.clear(cookie.domain, cookie.path, cookie.name)
                yield from self.login(client)
                res = yield build_request(client, 'GET', '/api/auth/me')

            if res is None:
                raise RuntimeError('Unexpected branch')
            res.raise_for_status()
        except niquests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else 'unknown'
            logger.exception(f'Bad response during auth: {status}')
            raise
        except niquests.exceptions.RequestException:
            logger.exception('Bad request during auth')
            raise
        except LoginError:
            logger.exception(f'Error during login of user {self.username}')
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
    session_path: Path

    @override
    def vendor(self, client: httpClient) -> SingleVendor[None]:
        logger.info('Logging out the client')
        res = yield build_request(client, 'POST', '/api/logout')
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
    def vendor(self, client: httpClient) -> SingleVendor[CSLLists]:
        logger.info('Fetching lists')
        res = yield build_request(client, 'GET', '/api/lists')
        res.raise_for_status()
        rtn: CSLLists = {}
        for data in res.json():
            csl_list = from_json(cast(JSONList, data), CSLList)
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
    def vendor(self, client: httpClient) -> SingleVendor[CSLGroups]:
        logger.info('Fetching groups')
        res = yield build_request(client, 'GET', '/api/groups')
        res.raise_for_status()
        rtn: CSLGroups = {}
        for data in res.json():
            group = from_json(cast(JSONGroup, data), CSLGroup)
            rtn[group.name] = group
        return rtn

    @override
    def __rich_repr__(self) -> rich.repr.Result:
        return
        yield


@frozen
class GetSongBundle(Bundle[CSLSong]):
    song: CSLSongSample

    @override
    def vendor(self, client: httpClient) -> SingleVendor[CSLSong]:
        song = self.song
        if isinstance(song, CSLSong):
            logger.warning(f'client.get_song called with already filled CSLSong {song.name}')
            return song
        res = yield build_request(client, 'GET', f'/api/song/{song.id}')
        res.raise_for_status()
        return from_json(cast(JSONSong, res.json()), CSLSong)

    @override
    def __rich_repr__(self) -> rich.repr.Result:
        yield 'song', self.song


@frozen
class GetArtistBundle(Bundle[CSLArtist]):
    artist: CSLArtistSample

    @override
    def vendor(self, client: httpClient) -> SingleVendor[CSLArtist]:
        artist = self.artist
        if isinstance(artist, CSLArtist):
            logger.warning(f'client.get_artist called with already filled CSLArtist {artist.name}')
            return artist
        res = yield build_request(client, 'GET', f'/api/artist/{artist.id}')
        res.raise_for_status()
        return from_json(cast(JSONArtist, res.json()), CSLArtist)

    @override
    def __rich_repr__(self) -> rich.repr.Result:
        yield 'artist', self.artist


@frozen
class GetMetadataBundle(Bundle[CSLMetadata | None]):
    track: CSLTrack

    @override
    def vendor(self, client: httpClient) -> SingleVendor[CSLMetadata | None]:
        res = yield build_request(client, 'GET', f'/api/track/{self.track.id}/metadata')
        if res.status_code == 404:
            try:
                data = res.json()
            except ValueError:
                res.raise_for_status()
                raise
            match data:
                case {'statusCode': 404, 'errors': {'generalErrors': ['Song does not have metadata']}}:
                    return None
                case _:
                    pass
        res.raise_for_status()
        return from_json(cast(JSONMetadata, res.json()), CSLMetadata)

    @override
    def __rich_repr__(self) -> rich.repr.Result:
        yield 'track', self.track.simp


@frozen
class CreateListBundle(Bundle[None]):
    name: str = field(validator=min_len(1))
    csl_lists: list[CSLList] = field(factory=list[CSLList], converter=materialize)

    @override
    def vendor(self, client: httpClient) -> SingleVendor[None]:
        logger.info(f'Creating list {self.name}')
        body = {
            'importListIds': [csl_list.id for csl_list in self.csl_lists],
            'name': self.name,
        }
        res = yield build_request(client, 'POST', '/api/list', json=body)
        res.raise_for_status()
        logger.info(f'List {self.name} created')

    @override
    def __rich_repr__(self) -> rich.repr.Result:
        yield 'name', self.name
        yield 'lists', self.csl_lists, []


@frozen
class ListEditBundle(Bundle[None]):
    csl_list: CSLList
    name: str | None = field(default=None, validator=optional(min_len(1)))
    add: list[CSLTrackRef] = field(factory=list[CSLTrackRef], converter=materialize)
    remove: list[CSLTrackRef] = field(factory=list[CSLTrackRef], converter=materialize)

    @override
    def vendor(self, client: httpClient) -> SingleVendor[None]:
        csl_list = self.csl_list
        logger.info(f'Editing list {csl_list.name}')
        body = {
            'addSongIds': [track.id for track in self.add],
            'id': EMPTY_ID,
            'name': self.name,
            'removeSongIds': [track.id for track in self.remove],
        }
        res = yield build_request(client, 'PUT', f'/api/list/{csl_list.id}', json=body)
        res.raise_for_status()

    @override
    def __rich_repr__(self) -> rich.repr.Result:
        yield 'list', self.csl_list
        yield 'new_name', self.name, None


@frozen
class ListDeleteBundle(Bundle[None]):
    csl_list: CSLList

    @override
    def vendor(self, client: httpClient) -> SingleVendor[None]:
        csl_list = self.csl_list
        logger.info(f'Deleting list {csl_list.name}')
        res = yield build_request(client, 'DELETE', f'/api/list/{csl_list.id}')
        res.raise_for_status()

    @override
    def __rich_repr__(self) -> rich.repr.Result:
        yield 'list', self.csl_list


@frozen
class CreateGroupBundle(Bundle[CSLGroup]):
    name: str = field(validator=min_len(1))

    @override
    def vendor(self, client: httpClient) -> SingleVendor[CSLGroup]:
        logger.info(f'Adding group {self.name}')
        res = yield build_request(client, 'POST', '/api/group', json={'name': self.name})
        res.raise_for_status()
        return from_json(cast(JSONGroup, res.json()), CSLGroup)

    @override
    def __rich_repr__(self) -> rich.repr.Result:
        yield 'name', self.name


@frozen
class GroupEditBundle(Bundle[None]):
    group: CSLGroup
    name: str = field(validator=min_len(1))

    @override
    def vendor(self, client: httpClient) -> SingleVendor[None]:
        logger.info(f'Editing group {self.group.name}')
        body = {
            'id': EMPTY_ID,
            'name': self.name,
        }
        res = yield build_request(client, 'PUT', f'/api/group/{self.group.id}', json=body)
        res.raise_for_status()

    @override
    def __rich_repr__(self) -> rich.repr.Result:
        yield 'group', self.group
        yield 'new_name', self.name, None


@frozen
class GroupDeleteBundle(Bundle[None]):
    group: CSLGroup

    @override
    def vendor(self, client: httpClient) -> SingleVendor[None]:
        logger.info(f'Deleting group {self.group.name}')
        res = yield build_request(client, 'DELETE', f'/api/group/{self.group.id}')
        res.raise_for_status()

    @override
    def __rich_repr__(self) -> rich.repr.Result:
        yield 'group', self.group


@frozen
class SongEditBundle(Bundle[None]):
    song: CSLSong
    name: str | None
    disambiguation: str | None

    @override
    def vendor(self, client: httpClient) -> SingleVendor[None]:
        logger.info(f'Editing song {self.song.name}')
        body = {
            'id': EMPTY_ID,
            'name': self.song.name if self.name is None else self.name,
            'disambiguation': self.song.disambiguation if self.disambiguation is None else self.disambiguation,
        }
        res = yield build_request(client, 'PUT', f'/api/song/{self.song.id}', json=body)
        res.raise_for_status()

    @override
    def __rich_repr__(self) -> rich.repr.Result:
        yield 'song', self.song
        yield 'new_name', self.name, self.song.name
        yield 'new_disambiguation', self.name, self.song.disambiguation


@frozen
class SongDeleteBundle(Bundle[None]):
    song: CSLSong

    @override
    def vendor(self, client: httpClient) -> SingleVendor[None]:
        logger.info(f'Deleting song {self.song.name}')
        res = yield build_request(client, 'DELETE', f'/api/song/{self.song.id}')
        res.raise_for_status()

    @override
    def __rich_repr__(self) -> rich.repr.Result:
        yield 'song', self.song


@frozen
class SongAddMetadataBundle(Bundle[None]):
    song: CSLSong
    metas: list[Metadata] = field(converter=materialize)

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
        return artist_credits, extra_metadata

    @override
    def vendor(self, client: httpClient) -> SingleVendor[None]:
        logger.info(f'Queuing metadata edit on {self.song.name}')
        artist_credits, extra_metadata = self.filtered_metas
        body: SongMetadataPostBody = {
            'id': self.song.id,
            'artistCredits': [to_json(meta) for meta in artist_credits],
            'extraMetadatas': [to_json(meta) for meta in extra_metadata],
        }
        res = yield build_request(client, 'POST', f'/api/song/{self.song.id}/metadata', json=body)
        res.raise_for_status()

    @override
    def __rich_repr__(self) -> rich.repr.Result:
        yield 'song', self.song
        artist_credits, extra_metadata = self.filtered_metas
        yield 'artist_credits', artist_credits, []
        yield 'extra_metadata', extra_metadata, []


@frozen
class SongDeleteMetadataBundle(Bundle[None]):
    song: CSLSong
    meta: CSLSongArtistCredit | CSLExtraMetadata

    @override
    def vendor(self, client: httpClient) -> SingleVendor[None]:
        logger.info(f'Removing metadata {self.meta} from song {self.song.name}')
        res = yield build_request(client, 'DELETE', f'/api/song/{self.song.id}/metadata/{self.meta.id}')
        res.raise_for_status()

    @override
    def __rich_repr__(self) -> rich.repr.Result:
        yield 'track', self.song.name
        yield 'meta', self.meta


@frozen
class TrackAddMetadataBundle(Bundle[None]):
    track: CSLTrack
    metas: list[Metadata] = field(converter=materialize)
    _override: bool | None = None
    existing_meta: CSLMetadata | None = None

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
            current_metas.add(meta)
        return artist_credits, extra_metadata

    def __len__(self) -> int:
        artist_credits, extra_metadata = self.filtered_metas
        return len(artist_credits) + len(extra_metadata)

    @override
    def vendor(self, client: httpClient) -> SingleVendor[None]:
        track = self.track
        logger.info(f'Queuing metadata edit on {track.name}')

        artist_credits, extra_metadata = self.filtered_metas
        if self._override is None and not self:
            logger.info('No changes necessary, skipping request')
            return
        body: MetadataPostBody = {
            'artistCredits': [to_json(meta) for meta in artist_credits],
            'extraMetadatas': [to_json(meta) for meta in extra_metadata],
            'id': EMPTY_ID,
            'override': self._override,
        }
        res = yield build_request(client, 'POST', f'/api/track/{track.id}/metadata', json=body)
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
    track: CSLTrack
    meta: CSLSongArtistCredit | CSLExtraMetadata

    @override
    def vendor(self, client: httpClient) -> SingleVendor[None]:
        logger.info(f'Removing metadata {self.meta} from track {self.track.name}')
        res = yield build_request(client, 'DELETE', f'/api/track/{self.track.id}/metadata/{self.meta.id}')
        res.raise_for_status()

    @override
    def __rich_repr__(self) -> rich.repr.Result:
        yield 'track', self.track.simp
        yield 'meta', self.meta


@frozen
class TrackEditBundle(Bundle[None]):
    track: CSLTrack
    artist_credits: Sequence[TrackPutArtistCredit] | None = None
    groups: Sequence[CSLGroup] | None = None
    name: str | None = None
    original_artist: str | None = None
    original_name: str | None = None
    song: NewSong | CSLSongSample | None = None
    type: TrackType | None = None

    @override
    def vendor(self, client: httpClient) -> SingleVendor[None]:
        track = self.track
        logger.info(f'Editing track {track.name}')

        body: TrackPutBody = {
            'artistCredits': None
            if self.artist_credits is None
            else [to_json(credit, position=idx) for idx, credit in enumerate(self.artist_credits)],
            'batchSongIds': None,
            'groupIds': None if self.groups is None else [group.id for group in self.groups],
            'id': EMPTY_ID,
            'name': self.name,
            'newSong': None,
            'originalArtist': self.original_artist,
            'originalName': self.original_name,
            'songId': None,
            'type': None if self.type is None else REVERSE_TRACK_TYPE[self.type],
        }
        match self.song:
            case NewSong():
                body['newSong'] = to_json(self.song)
            case CSLSongSample():
                body['songId'] = self.song.id
            case None:
                pass
        res = yield build_request(client, 'PUT', f'/api/track/{track.id}', json=body)
        res.raise_for_status()

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
    name: str = field(validator=min_len(1))
    original_name: str = field(validator=min_len(1))
    year: int = field(validator=gt(0))
    groups: list[CSLGroup] = field(converter=materialize)
    tracks: Sequence[Sequence[AlbumTrack]]

    @override
    def vendor(self, client: httpClient) -> SingleVendor[None]:
        name = self.name
        logger.info(f'Adding album {name}')
        body: AlbumAddBody = {
            'album': name,
            'discTotal': len(self.tracks),
            'groupIds': [group.id for group in self.groups],
            'originalAlbum': self.original_name,
            'year': self.year,
            'tracks': [
                to_json(track, disc_number=disc_number, track_number=track_number, track_total=len(disc))
                for disc_number, disc in enumerate(self.tracks, start=1)
                for track_number, track in enumerate(disc, start=1)
            ],
        }
        res = yield build_request(client, 'POST', '/api/album', json=body)
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
    track: CSLTrack
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
    def vendor(self, client: httpClient) -> SingleVendor[None]:
        track = self.track
        logger.info(f'Uploading audio to {track.name}')
        res = yield build_request(client, 'POST', f'/api/track/{track.id}/presigned-upload', json={})
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
        upload_type = AsyncMultipartUpload if isinstance(client, niquests.AsyncSession) else MultipartUpload
        res = yield build_request(
            client,
            'POST',
            url,
            params={'sessionId': session_id, 'key': key},
            upload=upload_type(self.audio_path, self.mime_type),
        )
        res.raise_for_status()

    @override
    def __rich_repr__(self) -> rich.repr.Result:
        yield 'track', self.track.simp
        yield 'audio_path', self.audio_path.resolve()


@frozen
class ImportAudioBundle(Bundle[None]):
    track: CSLTrack
    track_to_import_from: CSLTrack

    @override
    def vendor(self, client: httpClient) -> SingleVendor[None]:
        track = self.track
        logger.info(f'Importing audio of {track.name}')
        res = yield build_request(
            client,
            'POST',
            f'/api/track/{track.id}/audio-import',
            json={
                'id': track.id,
                'url': self.track_to_import_from.audio_url,
            },
        )
        res.raise_for_status()

    @override
    def __rich_repr__(self) -> rich.repr.Result:
        yield 'track', self.track.simp
        yield 'track_to_import_from', self.track_to_import_from.simp
