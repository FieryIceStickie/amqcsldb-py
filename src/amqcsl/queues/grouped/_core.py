from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Iterator, Mapping
from typing import Any, ClassVar, Literal, Self, TypedDict, cast, override

from attrs import Attribute, define, field
from rich.console import Group, RenderableType
from rich.text import Text

from amqcsl.bundles._core import Bundle, EntryEdit, HasEdit, flatten, overlapping_entries
from amqcsl.bundles._misc import (
    AddAudioBundle,
    ImportAudioBundle,
    SongAddMetadataBundle,
    SongDeleteBundle,
    SongDeleteMetadataBundle,
    SongEditBundle,
    TrackAddMetadataBundle,
    TrackDeleteMetadataBundle,
    TrackEditBundle,
)
from amqcsl.objects._db_types import CSLArtistSample, CSLSongSample, CSLTrack
from amqcsl.objects._serialization import bundle_from_json, bundle_to_json

from .._core import Queue, QueueGroup, is_queueable
from ._display import display_group
from ._merging import merge_bundle

type GroupKind = Literal['tracks', 'songs', 'artists']
type GroupKey = tuple[GroupKind, str]
type GroupTarget = CSLTrack | CSLSongSample | CSLArtistSample


class GroupedQueueOptions(TypedDict, total=False):
    """Configure grouped queue panels.

    Attributes:
        width: Maximum panel width in columns; defaults to 100.
    """

    width: int


@define
class PendingGroup[T: GroupTarget]:
    """Keep an object's pending edits together without changing the bundles.

    Attributes:
        target: Object identifying this group.
        bundles: Pending edits in insertion order.
    """

    target: T
    bundles: list[Bundle[Any]] = field(factory=list)

    def display(self, width: int = 100) -> RenderableType:
        """Render this target's current identity and pending edits in one panel.

        Args:
            width: Maximum panel width in columns.

        Returns:
            The Rich panel representing this pending group.
        """
        return display_group(self, width=width)

    def affected_entries(self) -> set[EntryEdit]:
        """Collect the entry identities affected by this group's pending requests.

        Returns:
            The union of the pending bundles' affected entries.
        """
        return {entry for bundle in self.bundles for entry in cast(HasEdit, bundle).affected_entries()}


@define
class PreparedTask:
    """Collect validated edits for one atomic enqueue operation.

    Groups accumulate edits by target without changing the queue. Group
    appearance order is staged alongside them.

    Attributes:
        allow_overlapping_edits: Whether conflicting proposals can replace earlier edits.
        lookup: Lookup for existing queued groups; only copied groups are changed.
        groups: Replacement groups containing existing and newly staged edits.
        order: First appearance of staged target groups.
    """

    allow_overlapping_edits: bool = field(default=False, kw_only=True)
    lookup: Callable[[GroupKey], PendingGroup[Any] | None] | None = field(default=None, kw_only=True, repr=False)
    groups: dict[GroupKey, PendingGroup[GroupTarget]] = field(factory=dict)
    order: list[GroupKey] = field(factory=list)
    _affected_entries: dict[GroupKey, set[EntryEdit]] = field(factory=dict, init=False, repr=False)

    def add(self, bundle: Bundle[Any]) -> None:
        """Validate a bundle and accumulate its edits in this prepared task.

        Flatten parallel wrappers and check affected entries against existing
        and staged work. Compatible edits share requests; permitted overlaps use
        the last proposal. Only staged copies of queue groups are changed.

        Args:
            bundle: Bundle or parallel composition to validate.

        Raises:
            TypeError: A child is not a supported track, song, or artist edit.
            ValueError: An edit overlaps pending work and overlaps are disabled.
        """
        for child in flatten(bundle):
            if not is_queueable(child):
                raise TypeError('Streaming and read-only bundles cannot be queued')
            kind, key, target = _target(child)
            group_key = kind, key
            pending = self.groups.get(group_key)
            if pending is None:
                existing = self.lookup(group_key) if self.lookup is not None else None
                pending = (
                    PendingGroup(target) if existing is None else PendingGroup(existing.target, [*existing.bundles])
                )
                self._affected_entries[group_key] = pending.affected_entries()
            entries = cast(HasEdit, child).affected_entries()
            conflicts = overlapping_entries(self._affected_entries[group_key], entries)
            if conflicts and not self.allow_overlapping_edits:
                details = ', '.join(sorted(repr(entry.entry) for entry in conflicts))
                raise ValueError(f'Overlapping edits for {kind} {key!r}: {details}')
            merged = merge_bundle(pending.bundles, child)
            if not merged:
                continue
            if group_key not in self.groups:
                self.order.append(group_key)
            pending = PendingGroup(pending.target, merged)
            self.groups[group_key] = pending
            self._affected_entries[group_key] = pending.affected_entries()


def _check_worker_ratio(
    _: object,
    attribute: Attribute[float | None],
    value: float | None,
) -> None:
    """Validate the worker ratio used to calculate the number of async workers.

    None selects the automatic ratio. Explicit ratios must be finite and positive.

    Args:
        _: Queue instance supplied by attrs.
        attribute: Worker ratio field being validated.
        value: Proposed worker ratio.

    Raises:
        ValueError: The explicit ratio is nonpositive or nonfinite.
    """
    if value is not None and (not math.isfinite(value) or value <= 0):
        raise ValueError(f'{attribute.name} must be finite and positive')


def _target(bundle: Bundle[Any]) -> tuple[GroupKind, str, GroupTarget]:
    """Return the target category, ID, and object for a supported edit bundle.

    Only track, song, and artist edits belong in this queue. Other bundle
    types raise an error instead of being assigned a fallback group.

    Args:
        bundle: A flattened bundle to classify.

    Returns:
        The category, target ID, and target object.

    Raises:
        TypeError: The bundle is not a supported track, song, or artist edit.
    """
    match bundle:
        case (
            TrackEditBundle(track=track)
            | TrackAddMetadataBundle(track=track)
            | TrackDeleteMetadataBundle(track=track)
            | AddAudioBundle(track=track)
            | ImportAudioBundle(track=track)
        ):
            return 'tracks', track.id, track
        case (
            SongEditBundle(song=song)
            | SongDeleteBundle(song=song)
            | SongAddMetadataBundle(song=song)
            | SongDeleteMetadataBundle(song=song)
        ):
            return 'songs', song.id, song
        case _:
            raise TypeError(
                f'GroupedQueue does not support {type(bundle).__name__}; expected a track, song, or artist edit'
            )


@define(eq=False)
class GroupedQueue(Queue[GroupedQueueOptions]):
    """Group edits by target while preserving first appearance and child order.

    Target objects are grouped by database ID. Lists, groups, albums, read-only
    operations, and unrecognized bundle types are rejected. Artist storage is
    reserved for future artist edit bundles.

    Attributes:
        worker_ratio: Requests per group worker, or None to use the average
            number of pending bundles per group.
        allow_overlapping_edits: Whether conflicting edits may be merged using
            the last proposal. False rejects overlapping pending entries.
    """

    queue_type: ClassVar[str] = 'grouped'
    version: ClassVar[tuple[int, int]] = (0, 0)

    worker_ratio: float | None = field(default=None, kw_only=True, validator=_check_worker_ratio)
    allow_overlapping_edits: bool = field(default=False, kw_only=True)
    _tracks: dict[str, PendingGroup[CSLTrack]] = field(factory=dict, init=False, repr=False)
    _songs: dict[str, PendingGroup[CSLSongSample]] = field(factory=dict, init=False, repr=False)
    _artists: dict[str, PendingGroup[CSLArtistSample]] = field(factory=dict, init=False, repr=False)
    _order: list[GroupKey] = field(factory=list, init=False, repr=False)

    def _bucket(self, kind: GroupKind) -> dict[str, PendingGroup[Any]]:
        """Return the dictionary storing pending groups for a category.

        Args:
            kind: Target category whose dictionary is needed.

        Returns:
            The category's mutable dictionary of pending groups.
        """
        match kind:
            case 'tracks':
                return self._tracks
            case 'songs':
                return self._songs
            case 'artists':
                return self._artists

    @override
    def enqueue(self, bundle: Bundle[Any]) -> None:
        """Add a bundle's edits to their target groups.

        Prepare all edits, including recursively flattened parallel children,
        before merging their staged groups into the queue.

        Args:
            bundle: Edit bundle or parallel composition to queue.

        Raises:
            RuntimeError: The queue is processing.
            TypeError: A child is not a supported track, song, or artist edit.
            ValueError: An edit overlaps pending work and overlaps are disabled.
        """
        self._check_mutable()
        prepared = PreparedTask(allow_overlapping_edits=self.allow_overlapping_edits, lookup=self._pending_group)
        prepared.add(bundle)
        self._add_task(prepared)

    @override
    def enqueue_batch(self, bundles: Iterable[Bundle[Any]]) -> None:
        """Add a batch of edits atomically, only if every bundle is valid.

        Use this when all edits must be accepted together. Unlike repeated
        enqueue calls, no edits are added until the entire iterable has been
        consumed and validated. An unsupported bundle or iteration error leaves
        the queue unchanged. Parallel wrappers are flattened recursively.

        Args:
            bundles: Bundles to add in insertion order.

        Raises:
            RuntimeError: The queue is processing.
            TypeError: A child is not a supported track, song, or artist edit.
            ValueError: An edit overlaps pending work and overlaps are disabled.
        """
        self._check_mutable()
        prepared = PreparedTask(allow_overlapping_edits=self.allow_overlapping_edits, lookup=self._pending_group)
        for bundle in bundles:
            prepared.add(bundle)
        self._add_task(prepared)

    def _pending_group(self, key: GroupKey) -> PendingGroup[Any] | None:
        """Look up an existing target group without changing its pending requests.

        Args:
            key: Target category and database ID.

        Returns:
            The queued group, or None when the target has no pending work.
        """
        kind, target_id = key
        return self._bucket(kind).get(target_id)

    def _add_task(self, task: PreparedTask) -> None:
        """Merge a validated task's groups into the queue.

        Existing target groups retain their positions and are replaced with
        normalized copies. New groups follow staged appearance order. No validation is
        performed here.

        Args:
            task: Fully validated enqueue operation to apply.
        """
        for entry in task.order:
            kind, key = entry
            bucket = self._bucket(kind)
            pending = task.groups[entry]
            if key not in bucket:
                self._order.append(entry)
            bucket[key] = pending

    def _ordered_bundles(self) -> Iterator[tuple[GroupKind, tuple[Bundle[Any], ...]]]:
        """Yield pending bundles by group in first-appearance order.

        Targets are looked up in their category dictionaries and their bundles
        are copied into immutable snapshots.

        Yields:
            The category and pending bundle snapshot for each group.
        """
        for kind, key in self._order:
            yield kind, (*self._bucket(kind)[key].bundles,)

    @override
    def __iter__(self) -> Iterator[QueueGroup]:
        """Return snapshots of pending groups in first-appearance order.

        Returns:
            An iterator over immutable group snapshots containing the original
            bundle objects.
        """
        return iter([QueueGroup(bundles) for _, bundles in self._ordered_bundles()])

    @override
    def __len__(self) -> int:
        """Count the pending bundles across all target groups.

        Returns:
            The total number of individual pending edits.
        """
        return sum(len(self._bucket(kind)[key].bundles) for kind, key in self._order)

    @override
    def complete(self, bundle: Bundle[Any]) -> None:
        """Remove a successfully processed bundle from its target group.

        Matching uses object identity. Empty groups are removed from storage and
        appearance order; remaining edits keep their existing order.

        Args:
            bundle: Completed bundle to remove.

        Raises:
            ValueError: The bundle is not pending in this queue.
        """
        for group_idx, (kind, key) in enumerate(self._order):
            bucket = self._bucket(kind)
            children = bucket[key].bundles
            for idx, child in enumerate(children):
                if child is bundle:
                    del children[idx]
                    if not children:
                        del bucket[key]
                        del self._order[group_idx]
                    return
        raise ValueError('Bundle is not pending in this queue')

    @override
    def num_workers(self, max_request_count: int) -> int:
        """Calculate how many async workers should process the pending groups.

        Divide the request limit by worker_ratio, or by the average number of
        pending bundles per group when no ratio is supplied. Round down and
        bound the result between one and the number of groups for nonempty queues.

        Args:
            max_request_count: Client's concurrent HTTP request limit.

        Returns:
            Worker count, or zero when the queue is empty.

        Raises:
            ValueError: The request limit is nonpositive.
        """
        if max_request_count <= 0:
            raise ValueError('max_request_count must be positive')
        if not self._order:
            return 0
        ratio = self.worker_ratio if self.worker_ratio is not None else len(self) / len(self._order)
        if ratio <= max_request_count / len(self._order):
            return len(self._order)
        return min(len(self._order), max(1, math.floor(max_request_count / ratio)))

    @override
    def display(self, options: GroupedQueueOptions | None = None) -> RenderableType:
        """Stack one identity-and-edits panel per pending target in appearance order.

        Args:
            options: Panel width, or None for the default width of 100 columns.

        Returns:
            The grouped panels, or a dim message when the queue is empty.
        """
        width = options.get('width', 100) if options is not None else 100
        if width <= 0:
            raise ValueError('Display width must be positive')
        if not self._order:
            return Text('No pending edits', style='dim')
        return Group(*(self._bucket(kind)[key].display(width) for kind, key in self._order))

    @override
    def to_json(self) -> dict[str, Any]:
        """Serialize the pending groups and worker ratio into a JSON-compatible mapping.

        Group and child order are preserved. The document includes this queue
        type's schema version.

        Returns:
            The serialized grouped queue document.
        """
        return {
            'queue_type': self.queue_type,
            'version': '.'.join(map(str, self.version)),
            'worker_ratio': self.worker_ratio,
            'allow_overlapping_edits': self.allow_overlapping_edits,
            'groups': [
                {
                    'kind': kind,
                    'bundles': [bundle_to_json(bundle) for bundle in bundles],
                }
                for kind, bundles in self._ordered_bundles()
            ],
        }

    @classmethod
    @override
    def from_json(cls, data: Mapping[str, Any]) -> Self:
        """Restore a grouped queue from a serialized JSON mapping.

        Rebuild groups from their bundle targets and verify that their categories
        and grouping match the saved layout. Return only after every entry loads.

        Args:
            data: Serialized grouped queue document.

        Returns:
            A new queue preserving pending group order and the worker ratio.

        Raises:
            TypeError: The document contains an invalid group or bundle structure.
            ValueError: The type, version, ratio, or group targets are invalid,
                or a bundle cannot be restored.
        """
        cls._validate_json(data)
        groups = data.get('groups')
        if not isinstance(groups, list):
            raise TypeError('Grouped queue groups must be an array of objects')
        groups = cast(list[Any], groups)
        overlaps = data.get('allow_overlapping_edits', False)
        if not isinstance(overlaps, bool):
            raise TypeError('allow_overlapping_edits must be a boolean')
        result = cls(worker_ratio=data.get('worker_ratio'), allow_overlapping_edits=overlaps)
        for group in groups:
            if not isinstance(group, dict):
                raise TypeError('Grouped queue groups must be an array of objects')
            group = cast(dict[str, Any], group)
            children = group.get('bundles')
            if not isinstance(children, list) or not children:
                raise ValueError('Each grouped queue group must contain a nonempty bundles array')
            children = cast(list[Any], children)
            previous_count = len(result._order)
            result.enqueue_batch(bundle_from_json(child) for child in children)
            if len(result._order) != previous_count + 1 or result._order[-1][0] != group.get('kind'):
                raise ValueError('Serialized groups do not match their bundle targets')
        return result
