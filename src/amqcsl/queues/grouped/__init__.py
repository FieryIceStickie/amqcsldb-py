"""Queues that combine pending edits by track, song, or artist target."""

from ._core import GroupedQueue, GroupedQueueOptions

__all__ = [
    'GroupedQueue',
    'GroupedQueueOptions',
]
