"""Combine grouped edits into the requests needed for their final proposals."""

from typing import Any

from attrs import evolve, fields

from amqcsl.bundles._core import Bundle, EntryEdit
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
    metadata_entry,
)
from amqcsl.objects._db_types import Metadata

type MetadataAdd = TrackAddMetadataBundle | SongAddMetadataBundle
type MetadataRemove = TrackDeleteMetadataBundle | SongDeleteMetadataBundle


def _additions(bundle: MetadataAdd) -> dict[EntryEdit, Metadata]:
    """Index effective metadata additions by identity, keeping the last proposal.

    Args:
        bundle: Track or song metadata addition to normalize.

    Returns:
        Effective additions indexed by the same keys used for overlap detection.
    """
    match bundle:
        case TrackAddMetadataBundle():
            artists, extras = bundle.filtered_metas
            return {metadata_entry('track', bundle.track.id, meta): meta for meta in (*artists, *extras)}
        case SongAddMetadataBundle():
            return {metadata_entry('song', bundle.song.id, meta): meta for meta in bundle.metas}


def _with_additions(
    bundle: MetadataAdd,
    entries: dict[EntryEdit, Metadata],
) -> MetadataAdd:
    """Replace a metadata bundle's additions with an already filtered proposal.

    Track additions no longer need existing_meta because filtering has already
    happened independently for each original bundle.

    Args:
        bundle: Bundle whose target and override settings should be retained.
        entries: Effective additions to send in the replacement bundle.

    Returns:
        A fresh bundle without stale cached metadata filtering.
    """
    match bundle:
        case TrackAddMetadataBundle():
            return evolve(bundle, metas=[*entries.values()], existing_meta=None)
        case SongAddMetadataBundle():
            return evolve(bundle, metas=[*entries.values()])


def _has_additions(bundle: MetadataAdd) -> bool:
    """Check whether a normalized metadata bundle would change metadata or override."""
    return bool(bundle.metas) or isinstance(bundle, TrackAddMetadataBundle) and bundle.metadata_override is not None


def _merge_metadata_add(
    pending: list[Bundle[Any]],
    incoming: MetadataAdd,
) -> list[Bundle[Any]]:
    """Combine metadata additions and discard earlier removals of added entries.

    Args:
        pending: Existing requests for this target, left unchanged.
        incoming: Latest metadata addition.

    Returns:
        Requests containing one combined addition and only surviving removals.
    """
    entries = _additions(incoming)
    result: list[Bundle[Any]] = []
    addition_idx: int | None = None
    combined = incoming
    for old in pending:
        match old:
            case TrackAddMetadataBundle() | SongAddMetadataBundle():
                old_entries = _additions(old)
                old_entries.update(entries)
                entries = old_entries
                match old, incoming:
                    case TrackAddMetadataBundle(), TrackAddMetadataBundle():
                        override = (
                            incoming.metadata_override
                            if incoming.metadata_override is not None
                            else old.metadata_override
                        )
                        combined = evolve(old, override=override)
                    case _:
                        combined = old
                addition_idx = len(result)
                result.append(old)
            case TrackDeleteMetadataBundle() | SongDeleteMetadataBundle():
                if not old.affected_entries() & entries.keys():
                    result.append(old)
            case _:
                result.append(old)
    combined = _with_additions(combined, entries)
    if _has_additions(combined):
        if addition_idx is None:
            result.append(combined)
        else:
            result[addition_idx] = combined
    elif addition_idx is not None:
        del result[addition_idx]
    return result


def _merge_metadata_remove(
    pending: list[Bundle[Any]],
    incoming: MetadataRemove,
) -> list[Bundle[Any]]:
    """Keep the latest removal and discard additions of that metadata entry.

    Args:
        pending: Existing requests for this target, left unchanged.
        incoming: Latest removal, including the server metadata ID to delete.

    Returns:
        Requests containing one removal for the entry and other surviving work.
    """
    removed = incoming.affected_entries()
    result: list[Bundle[Any]] = []
    removal_idx: int | None = None
    for old in pending:
        match old:
            case TrackAddMetadataBundle() | SongAddMetadataBundle():
                entries = {key: meta for key, meta in _additions(old).items() if key not in removed}
                replacement = _with_additions(old, entries)
                if _has_additions(replacement):
                    result.append(replacement)
            case TrackDeleteMetadataBundle() | SongDeleteMetadataBundle() if old.affected_entries() & removed:
                if removal_idx is None:
                    removal_idx = len(result)
                    result.append(incoming)
            case _:
                result.append(old)
    if removal_idx is None:
        result.append(incoming)
    return result


def merge_bundle(
    pending: list[Bundle[Any]],
    incoming: Bundle[Any],
) -> list[Bundle[Any]]:
    """Merge a supported edit into one target's pending requests.

    Compatible fields and metadata additions share requests. Repeated audio
    operations and removals keep the latest proposal. Song deletion supersedes
    every other operation for the song. Input bundles and lists stay unchanged.

    Args:
        pending: Normalized requests already staged for this target.
        incoming: Validated edit to merge into them.

    Returns:
        A new list of the requests needed for the final proposal.
    """
    match incoming:
        case SongDeleteBundle():
            return [incoming]
        case _ if any(isinstance(old, SongDeleteBundle) for old in pending):
            return [*pending]
        case TrackAddMetadataBundle() | SongAddMetadataBundle():
            return _merge_metadata_add(pending, incoming)
        case TrackDeleteMetadataBundle() | SongDeleteMetadataBundle():
            return _merge_metadata_remove(pending, incoming)
        case _:
            pass
    result = [*pending]
    for idx, old in enumerate(result):
        match old, incoming:
            case TrackEditBundle(), TrackEditBundle():
                changes = {
                    attribute.name: getattr(incoming, attribute.name)
                    for attribute in fields(TrackEditBundle)
                    if attribute.name != 'track' and getattr(incoming, attribute.name) is not None
                }
                result[idx] = evolve(old, **changes)
                return result
            case SongEditBundle(), SongEditBundle():
                result[idx] = evolve(
                    old,
                    name=old.name if incoming.name is None else incoming.name,
                    disambiguation=old.disambiguation if incoming.disambiguation is None else incoming.disambiguation,
                )
                return result
            case ((AddAudioBundle() | ImportAudioBundle()), (AddAudioBundle() | ImportAudioBundle())):
                result[idx] = incoming
                return result
            case _:
                pass
    result.append(incoming)
    return result
