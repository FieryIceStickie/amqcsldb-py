from collections.abc import Iterator, Mapping
from typing import Any, ClassVar, Self, TypedDict, cast, override

from attrs import define, field
from rich.console import RenderableType
from rich.pretty import Pretty

from amqcsl.bundles._core import Bundle, flatten
from amqcsl.objects._serialization import bundle_from_json, bundle_to_json

from ._core import Queue, QueueGroup


class StandardQueueOptions(TypedDict):
    """Display options reserved for the standard queue."""


@define(eq=False)
class StandardQueue(Queue[StandardQueueOptions]):
    """Queue bundles in insertion order, retaining parallel batches as groups."""

    queue_type: ClassVar[str] = 'standard'
    version: ClassVar[tuple[int, int]] = (0, 0)

    _groups: list[list[Bundle[Any]]] = field(factory=list, init=False, repr=False)

    @override
    def enqueue(self, bundle: Bundle[Any]) -> None:
        self._check_mutable()
        children = [*flatten(bundle)]
        if children:
            self._groups.append(children)

    @override
    def __iter__(self) -> Iterator[QueueGroup]:
        return iter([QueueGroup((*group,)) for group in self._groups])

    @override
    def __len__(self) -> int:
        return sum(len(group) for group in self._groups)

    @override
    def complete(self, bundle: Bundle[Any]) -> None:
        for group_idx, group in enumerate(self._groups):
            for idx, pending in enumerate(group):
                if pending is bundle:
                    del group[idx]
                    if not group:
                        del self._groups[group_idx]
                    return
        raise ValueError('Bundle is not pending in this queue')

    @override
    def num_workers(self, max_request_count: int) -> int:
        if max_request_count <= 0:
            raise ValueError('max_request_count must be positive')
        return min(max_request_count, len(self._groups))

    @override
    def display(self, options: StandardQueueOptions | None = None) -> RenderableType:
        return Pretty([*self])

    @override
    def to_json(self) -> dict[str, Any]:
        return {
            'queue_type': self.queue_type,
            'version': '.'.join(map(str, self.version)),
            'groups': [[bundle_to_json(bundle) for bundle in group] for group in self._groups],
        }

    @classmethod
    @override
    def from_json(cls, data: Mapping[str, Any]) -> Self:
        cls._validate_json(data)
        groups = data.get('groups')
        if not isinstance(groups, list):
            raise TypeError('Standard queue groups must be an array of arrays')
        groups = cast(list[Any], groups)
        if any(not isinstance(group, list) for group in groups):
            raise ValueError('Standard queue groups must be an array of arrays')
        result = cls()
        result._groups = [[bundle_from_json(bundle) for bundle in group] for group in groups if group]
        return result
