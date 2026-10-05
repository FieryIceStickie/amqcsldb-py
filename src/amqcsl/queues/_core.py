import json
import logging
import sys
from abc import ABC, abstractmethod
from collections.abc import Generator, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from os import PathLike
from typing import Any, ClassVar, Self, TextIO, cast

from attrs import define, field, frozen
from rich.console import Console, ConsoleOptions, RenderableType, RenderResult

from amqcsl.bundles._core import Bundle
from amqcsl.bundles._misc import (
    AddAudioBundle,
    CreateAlbumBundle,
    CreateGroupBundle,
    CreateListBundle,
    GroupDeleteBundle,
    GroupEditBundle,
    ImportAudioBundle,
    ListDeleteBundle,
    ListEditBundle,
    SongAddMetadataBundle,
    SongDeleteBundle,
    SongDeleteMetadataBundle,
    SongEditBundle,
    TrackAddMetadataBundle,
    TrackDeleteMetadataBundle,
    TrackEditBundle,
)
from amqcsl.objects._db_types import (
    AlbumTrack,
    CSLExtraMetadata,
    CSLGroup,
    CSLList,
    CSLMetadata,
    CSLSong,
    CSLSongArtistCredit,
    CSLSongSample,
    CSLTrack,
    CSLTrackRef,
    Metadata,
    NewSong,
    TrackPutArtistCredit,
)
from amqcsl.objects._obj_consts import TrackType

logger = logging.getLogger('amqcsl.queue')


@frozen
class QueueGroup:
    """An ordered snapshot of bundles to process together.

    Attributes:
        bundles: Child bundles in task creation order.
    """

    bundles: tuple[Bundle[Any], ...]


@define
class GroupState:
    """Failure state shared by a group's concurrent requests.

    Attributes:
        failed: Whether unsent requests in this group should be skipped.
    """

    failed: bool = False


class GroupAbortedError(Exception):
    """An unsent request was skipped because another bundle in its group failed."""


class RichQueueMixin(ABC):
    """Allow Rich consoles to render a queue using its default display options."""

    @abstractmethod
    def display(self) -> RenderableType:
        """Build a renderable using the queue's default options.

        Returns:
            The Rich renderable representing this queue.
        """
        raise NotImplementedError

    def __rich_console__(
        self,
        console: Console,
        options: ConsoleOptions,
    ) -> RenderResult:
        """Delegate terminal rendering to the queue's display implementation.

        Args:
            console: Console rendering the queue.
            options: Rendering options supplied by Rich.

        Yields:
            The queue's default display renderable.
        """
        yield self.display()


@define(eq=False)
class Queue[O: Mapping[str, object]](RichQueueMixin):
    """Independent pending work, with synchronous staging and client-owned execution.

    Mutations do not yield to other asyncio tasks. Cross-thread access and enqueueing
    the same bundle twice are unsupported. Concrete queues own their JSON schema.
    """

    queue_type: ClassVar[str]
    version: ClassVar[tuple[int, int]] = (0, 0)

    _is_processing: bool = field(default=False, init=False, repr=False)

    def _check_mutable(self) -> None:
        """Reject new work while a client owns the execution snapshot."""
        if self._is_processing:
            raise RuntimeError('Cannot enqueue while the queue is processing')

    @contextmanager
    def processing(self) -> Generator[tuple[QueueGroup, ...]]:
        """Claim this queue until execution, recovery output, and cleanup finish.

        Yields:
            An ordered execution snapshot.

        Raises:
            RuntimeError: Another client is already processing this queue.
        """
        if self._is_processing:
            raise RuntimeError('The queue is already processing')
        self._is_processing = True
        try:
            yield (*self,)
        finally:
            self._is_processing = False

    @abstractmethod
    def enqueue(self, bundle: Bundle[Any]) -> None:
        """Stage an ordinary bundle; streaming bundles cannot be queued.

        Args:
            bundle: Work to stage, with any return value discarded during processing.

        Raises:
            RuntimeError: The queue is currently processing.
            TypeError: The bundle contains streaming work.
        """
        raise NotImplementedError

    @abstractmethod
    def __iter__(self) -> Iterator[QueueGroup]:
        """Snapshot ordered groups without sharing their mutable storage.

        Returns:
            An iterator over the pending execution groups.
        """
        raise NotImplementedError

    @abstractmethod
    def __len__(self) -> int:
        """Count pending leaf bundles.

        Returns:
            The number of individual pending bundles.
        """
        raise NotImplementedError

    @abstractmethod
    def complete(self, bundle: Bundle[Any]) -> None:
        """Remove one successfully executed bundle by identity.

        Args:
            bundle: The completed bundle to remove.

        Raises:
            ValueError: The bundle is not pending in this queue.
        """
        raise NotImplementedError

    @abstractmethod
    def num_workers(self, max_request_count: int) -> int:
        """Choose how many groups the async client processes concurrently.

        Args:
            max_request_count: The client's current HTTP request limit.

        Returns:
            The worker count, or zero for an empty queue.
        """
        raise NotImplementedError

    @abstractmethod
    def display(self, options: O | None = None) -> RenderableType:
        """Build a Rich renderable without performing I/O.

        Args:
            options: Concrete queue display options, or None for defaults.

        Returns:
            The Rich renderable representing this queue.
        """
        raise NotImplementedError

    def changes_lists(self) -> bool:
        """Check whether pending work can change the client's list cache.

        Returns:
            Whether this queue contains list creation, editing, or deletion.
        """
        return any(
            isinstance(bundle, (CreateListBundle, ListEditBundle, ListDeleteBundle))
            for group in self
            for bundle in group.bundles
        )

    def changes_groups(self) -> bool:
        """Check whether pending work can change the client's group cache.

        Returns:
            Whether this queue contains group creation, editing, or deletion.
        """
        return any(
            isinstance(bundle, (CreateGroupBundle, GroupEditBundle, GroupDeleteBundle))
            for group in self
            for bundle in group.bundles
        )

    @abstractmethod
    def to_json(self) -> dict[str, Any]:
        """Encode this queue's versioned envelope and concrete group layout.

        Returns:
            A JSON-compatible object using this concrete queue's schema.
        """
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def from_json(cls, data: Mapping[str, Any]) -> Self:
        """Restore this concrete queue, rejecting incompatible input.

        Args:
            data: The versioned queue document to restore.

        Returns:
            A new instance containing all restored entries.

        Raises:
            ValueError: The queue type, version, or entries cannot be restored.
        """
        raise NotImplementedError

    def to_file(self, stream: TextIO) -> None:
        """Write JSON at the current stream position without closing or truncating it.

        Args:
            stream: Text stream receiving the serialized queue.
        """
        json.dump(self.to_json(), stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write('\n')

    @classmethod
    def from_file(cls, stream: TextIO) -> Self:
        """Read a complete JSON document without closing the supplied stream.

        Args:
            stream: Text stream containing one complete queue JSON document.

        Returns:
            A restored instance of the concrete queue class.

        Raises:
            TypeError: The document is not a JSON object.
            ValueError: The queue type, version, or entries cannot be restored.
        """
        data = json.load(stream)
        if not isinstance(data, dict):
            raise TypeError('Expected a queue JSON object')
        return cls.from_json(cast(dict[str, Any], data))

    @classmethod
    def _validate_json(cls, data: Mapping[str, Any]) -> None:
        """Validate the serialized queue's type and schema version.

        Args:
            data: The serialized queue document to validate.

        Raises:
            TypeError: The version is not a string.
            ValueError: The queue type or parsed version is unsupported.
        """
        if data.get('queue_type') != cls.queue_type:
            raise ValueError(f'Expected queue type {cls.queue_type!r}, got {data.get("queue_type")!r}')
        version = data.get('version')
        if not isinstance(version, str):
            raise TypeError('Queue version must have the form major.minor')
        parts = version.split('.')
        if len(parts) != 2 or any(not part.isdecimal() for part in parts):
            raise ValueError('Queue version must have the form major.minor')
        parsed = (int(parts[0]), int(parts[1]))
        if parsed != cls.version:
            raise ValueError(f'Unsupported {cls.queue_type} queue version: {version}')

    def create_list(
        self,
        name: str,
        *csl_lists: CSLList,
    ) -> None:
        """Queue list creation

        Args:
            name: name of the list
            *csl_lists: Lists to pull tracks from
        """
        bundle = CreateListBundle(name, csl_lists)
        self.enqueue(bundle)

    def list_edit(
        self,
        csl_list: CSLList,
        *,
        name: str | None = None,
        add: Iterable[CSLTrackRef] = (),
        remove: Iterable[CSLTrackRef] = (),
    ) -> None:
        """Queue a list edit

        Args:
            csl_list: List to edit
            name: New name
            add: Tracks to add
            remove: Tracks to remove
        """
        bundle = ListEditBundle(csl_list, name, add, remove)
        self.enqueue(bundle)

    def list_delete(self, csl_list: CSLList) -> None:
        """Queue list deletion

        Args:
            csl_list: List to delete
        """
        bundle = ListDeleteBundle(csl_list)
        self.enqueue(bundle)

    def create_group(self, name: str) -> None:
        """Queue group creation

        Args:
            name: Name of the group

        """
        bundle = CreateGroupBundle(name)
        self.enqueue(bundle)

    def group_edit(
        self,
        group: CSLGroup,
        name: str,
    ) -> None:
        """Queue a group edit

        Args:
            group: CSLGroup
            name: New name of the group
        """
        bundle = GroupEditBundle(group, name)
        self.enqueue(bundle)

    def group_delete(self, group: CSLGroup) -> None:
        """Queue group deletion

        Args:
            group: CSLGroup
        """
        bundle = GroupDeleteBundle(group)
        self.enqueue(bundle)

    def song_edit(
        self,
        song: CSLSong,
        name: str | None = None,
        disambiguation: str | None = None,
    ) -> None:
        """Queue a song edit

        Args:
            song: CSLSong
            name: New name
            disambiguation: New disambiguation
        """
        bundle = SongEditBundle(song, name, disambiguation)
        self.enqueue(bundle)

    def song_delete(self, song: CSLSong) -> None:
        """Queue song deletion

        Args:
            song: CSLSong
        """
        bundle = SongDeleteBundle(song)
        self.enqueue(bundle)

    def song_add_metadata(
        self,
        song: CSLSong,
        *metas: Metadata,
    ) -> None:
        """Queue metadata additions to a song

        Args:
            song: CSLSong
            *metas: Metadata to add
        """
        bundle = SongAddMetadataBundle(song, metas)
        self.enqueue(bundle)

    def song_delete_metadata(
        self,
        song: CSLSong,
        meta: CSLSongArtistCredit | CSLExtraMetadata,
    ) -> None:
        """Queue metadata removal from a song

        Args:
            song: CSLSong
            meta: Metadata to remove
        """
        bundle = SongDeleteMetadataBundle(song, meta)
        self.enqueue(bundle)

    def track_add_metadata(
        self,
        track: CSLTrack,
        *metas: Metadata,
        override: bool | None = None,
        existing_meta: CSLMetadata | None = None,
    ) -> None:
        """Queue metadata additions to a track

        Args:
            track: CSLTrack
            *metas: Metadata to add
            override: Change metadata to override or append
            existing_meta: Existing metadata on the track, pass in to avoid duplicating metadata

        Raises:
            ValueError: If non-metadata is passed into metas
        """
        bundle = TrackAddMetadataBundle(track, metas, override, existing_meta)
        self.enqueue(bundle)

    def track_remove_metadata(
        self,
        track: CSLTrack,
        meta: CSLSongArtistCredit | CSLExtraMetadata,
    ) -> None:
        """Queue metadata removal from a track

        Args:
            track: CSLTrack
            meta: Metadata to remove
        """
        bundle = TrackDeleteMetadataBundle(track, meta)
        self.enqueue(bundle)

    def track_edit(
        self,
        track: CSLTrack,
        *,
        artist_credits: Sequence[TrackPutArtistCredit] | None = None,
        groups: Sequence[CSLGroup] | None = None,
        name: str | None = None,
        original_artist: str | None = None,
        original_name: str | None = None,
        song: NewSong | CSLSongSample | None = None,
        type: TrackType | None = None,
    ) -> None:
        """Queue a track edit

        Args:
            track: CSLTrack
            artist_credits: List of new artist credits
            groups: List of new groups
            name: New track name
            original_artist: New track original artist
            original_name: New track original name
            song: New song
            type: New track type

        """
        bundle = TrackEditBundle(track, artist_credits, groups, name, original_artist, original_name, song, type)
        self.enqueue(bundle)

    def create_album(
        self,
        name: str,
        original_name: str,
        year: int,
        groups: Iterable[CSLGroup],
        tracks: Sequence[Sequence[AlbumTrack]],
    ) -> None:
        """Queue album creation

        Args:
            name: Album name
            original_name: Original album name
            year: Year album was created
            groups: Groups associated with album
            tracks: CSLTrack[][], where each CSLTrack[] is a disc
        """
        bundle = CreateAlbumBundle(name, original_name, year, groups, tracks)
        self.enqueue(bundle)

    def add_audio(
        self,
        track: CSLTrack,
        audio_path: str | PathLike[str],
    ) -> None:
        """Queue an audio upload

        Args:
            track: CSLTrack
            audio_path: Path to the audio file

        Raises:
            QueryError: Audio path is invalid
        """
        bundle = AddAudioBundle(track, audio_path)
        self.enqueue(bundle)

    def import_audio(
        self,
        track: CSLTrack,
        track_to_import_from: CSLTrack,
    ) -> None:
        """Queue audio import from an existing track

        Args:
            track: CSLTrack
            track_to_import_from: Other track to import audio from
        """
        bundle = ImportAudioBundle(track, track_to_import_from)
        self.enqueue(bundle)


def save_recovery(queue: Queue[Any], stream: TextIO | None, error: BaseException | None) -> None:
    """Always save remaining work, preserving execution and cancellation failures.

    Args:
        queue: Remaining work to serialize.
        stream: Output stream, or None for the current stdout.
        error: Execution error already being propagated, if any.

    Raises:
        ExceptionGroup: Execution and saving both fail with ordinary exceptions.
    """
    destination = sys.stdout if stream is None else stream
    logger.info('Writing remaining queue to %s', getattr(destination, 'name', type(destination).__name__))
    try:
        queue.to_file(destination)
    except Exception as save_error:
        if error is None:
            raise
        if not isinstance(error, Exception):
            logger.exception('Could not write the recovery queue')
        else:
            raise ExceptionGroup('Queue processing and recovery output failed', [error, save_error]) from None
