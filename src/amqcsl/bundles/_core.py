from collections.abc import Generator, Iterable, Iterator
from typing import Any, Literal, Protocol, cast, override, runtime_checkable

import niquests
import rich.repr
from attrs import field, frozen

type httpClient = niquests.Session | niquests.AsyncSession  # noqa: PYI042 -- use the maintainer-requested alias spelling

type SingleVendor[R] = Generator[niquests.PreparedRequest, niquests.Response, R]
type MultiVendor[R] = Generator[Iterable[niquests.PreparedRequest], Iterable[niquests.Response], R]
type MixedVendor[R] = Generator[
    niquests.PreparedRequest | Iterable[niquests.PreparedRequest], niquests.Response | Iterable[niquests.Response], R
]
type Vendor[R] = SingleVendor[R] | MultiVendor[R] | MixedVendor[R]


class Bundle[R](Protocol):
    # httpClient provides session state for preparing requests
    # Do not use to send actual requests, since it should work for both sync
    # and async clients
    def vendor(self, client: httpClient) -> Vendor[R]: ...
    def __rich_repr__(self) -> rich.repr.Result: ...


@frozen
class EntryEdit:
    """Identify a field or metadata entry changed by a bundle.

    Attributes:
        target_type: Kind of database object being edited.
        target_id: Database ID of the edited object.
        entry: Comparable field or metadata key. None affects the entire object,
            as with deletion, and overlaps every entry on that target.
    """

    target_type: Literal['track', 'song', 'artist']
    target_id: str
    entry: tuple[str | bool, ...] | None = None


class HasEdit(Protocol):
    """Describe work that reports the entries it changes."""

    def affected_entries(self) -> set[EntryEdit]:
        """Return the fields and metadata entries changed by this work.

        Returns:
            Comparable edit identities, independent of proposed values.
        """
        ...


def has_overlap(
    first: HasEdit,
    second: HasEdit,
) -> bool:
    """Check whether two edits change the same entry on the same target.

    Whole-object edits overlap any reported entry for that object. Metadata
    identities ignore the operation, so adding and removing one entry overlap.

    Args:
        first: Work reporting its affected entries.
        second: Other work to compare against it.

    Returns:
        Whether any affected entries conflict.
    """
    return bool(overlapping_entries(first.affected_entries(), second.affected_entries()))


def overlapping_entries(
    first: set[EntryEdit],
    second: set[EntryEdit],
) -> set[EntryEdit]:
    """Find shared entry identities, including whole-object deletion conflicts.

    Args:
        first: Affected entries from one edit or accumulated group.
        second: Other entries to compare against them.

    Returns:
        Shared entries and whole-object keys that conflict with the other set.
    """
    # Project entries to whole-object keys to detect deletions without pairwise comparisons
    return (
        (first & second)
        | ({EntryEdit(entry.target_type, entry.target_id) for entry in first} & second)
        | (first & {EntryEdit(entry.target_type, entry.target_id) for entry in second})
    )


@frozen
class Items[T]:
    """An explicit stream event whose values are consumed before requesting more work."""

    values: Iterable[T]


type StreamingVendor[T] = Generator[
    niquests.PreparedRequest | Iterable[niquests.PreparedRequest] | Items[T],
    niquests.Response | Iterable[niquests.Response] | None,
    None,
]


@runtime_checkable
class StreamingBundle[T](Protocol):
    """HTTP work that also yields items incrementally through Items events."""

    def vendor(self, client: httpClient) -> StreamingVendor[T]: ...
    def __rich_repr__(self) -> rich.repr.Result: ...

    def collect(self) -> Bundle[list[T]]:
        """Adapt this stream to an ordinary bundle returning all its items."""
        return CollectBundle(self)


@frozen
class CollectBundle[T](Bundle[list[T]]):
    """Collect any streaming bundle while forwarding its ordinary HTTP requests."""

    stream: StreamingBundle[T]

    @override
    def vendor(self, client: httpClient) -> MixedVendor[list[T]]:
        vendor = self.stream.vendor(client)
        items: list[T] = []
        reply: niquests.Response | Iterable[niquests.Response] | None = None
        try:
            while True:
                try:
                    event = vendor.send(reply)
                except StopIteration:
                    return items
                match event:
                    case Items(values=values):
                        items.extend(values)
                        reply = None
                    case _:
                        reply = yield event
        finally:
            vendor.close()

    @override
    def __rich_repr__(self) -> rich.repr.Result:
        yield 'stream', self.stream


type Replies = dict[int, niquests.Response | list[niquests.Response] | None]
type RequestSpans = dict[int, tuple[int, int, bool]]


@frozen
class ParallelBundle[R](Bundle[list[R]]):
    """Run child vendors together, including vendors with multiple request rounds."""

    bundles: list[Bundle[R]] = field(converter=list)

    @staticmethod
    def _batch(
        vendors: dict[int, Vendor[R]],
        replies: Replies,
        results: dict[int, R],
    ) -> tuple[list[niquests.PreparedRequest], RequestSpans]:
        """Advance active vendors and remember where each vendor's responses belong."""
        requests: list[niquests.PreparedRequest] = []
        spans: RequestSpans = {}
        for idx, vendor in [*vendors.items()]:
            try:
                outgoing = vendor.send(replies[idx])  # type: ignore[reportArgumentType]
            except StopIteration as done:
                results[idx] = cast(R, done.value)
                del vendors[idx]
                continue
            start = len(requests)
            single = isinstance(outgoing, niquests.PreparedRequest)
            if isinstance(outgoing, niquests.PreparedRequest):
                requests.append(outgoing)
            else:
                requests.extend(outgoing)
            spans[idx] = (start, len(requests), single)
        return requests, spans

    @override
    def vendor(self, client: httpClient) -> MultiVendor[list[R]]:
        vendors = {idx: bundle.vendor(client) for idx, bundle in enumerate(self.bundles)}
        replies: Replies = dict.fromkeys(vendors)
        results: dict[int, R] = {}
        try:
            while vendors:
                requests, spans = self._batch(vendors, replies, results)
                responses = [*(yield requests)] if requests else []
                if len(responses) != len(requests):
                    raise ValueError('Response count does not match request count')
                for idx, (start, end, single) in spans.items():
                    replies[idx] = responses[start] if single else responses[start:end]
            # Completion order can differ from input order
            return [results[idx] for idx in sorted(results)]
        finally:
            for vendor in vendors.values():
                vendor.close()

    @override
    def __rich_repr__(self) -> rich.repr.Result:
        yield 'bundles', self.bundles


@frozen
class ParallelActionBundle(Bundle[None]):
    parallel: ParallelBundle[None]

    @property
    def bundles(self) -> list[Bundle[None]]:
        return self.parallel.bundles

    @override
    def vendor(self, client: httpClient) -> MultiVendor[None]:
        yield from self.parallel.vendor(client)

    @override
    def __rich_repr__(self) -> rich.repr.Result:
        yield 'bundles', self.bundles


def action_bundle_compose(bundles: Iterable[Bundle[None]]) -> Bundle[None]:
    """Compose queued actions in parallel, discarding their individual return values."""
    return ParallelActionBundle(ParallelBundle(bundles))


def flatten(bundle: Bundle[Any]) -> Iterator[Bundle[Any]]:
    """Recursively flatten known parallel wrappers and reject streaming work.

    Args:
        bundle: An ordinary bundle or nested parallel wrapper.

    Yields:
        Leaf bundles in child insertion order.

    Raises:
        TypeError: A child is a streaming bundle.
    """
    match bundle:
        case ParallelBundle() | ParallelActionBundle():
            children = cast(ParallelBundle[Any] | ParallelActionBundle, bundle).bundles
            for child in children:
                yield from flatten(child)
        case StreamingBundle():
            raise TypeError('Streaming bundles cannot be queued')
        case _:
            yield bundle
