from datetime import UTC, datetime
from io import StringIO

import pytest

from amqcsl.queues import GroupedQueue, StandardQueue


@pytest.mark.parametrize('queue_class', [StandardQueue, GroupedQueue])
def test_creation_time_is_utc_and_survives_file_round_trip(
    queue_class: type[StandardQueue] | type[GroupedQueue],
) -> None:
    before = datetime.now(UTC)
    queue = queue_class()
    after = datetime.now(UTC)
    assert queue.created_at is not None
    assert queue.created_at.tzinfo is UTC
    assert before <= queue.created_at <= after
    stream = StringIO()
    queue.to_file(stream)
    stream.seek(0)
    restored = queue_class.from_file(stream)
    assert restored.created_at == queue.created_at
    assert restored.to_json() == queue.to_json()


@pytest.mark.parametrize('queue_class', [StandardQueue, GroupedQueue])
def test_old_files_keep_creation_time_unknown(queue_class: type[StandardQueue] | type[GroupedQueue]) -> None:
    data = queue_class().to_json()
    del data['created_at']
    restored = queue_class.from_json(data)
    assert restored.created_at is None
    assert queue_class.from_json(restored.to_json()).created_at is None


@pytest.mark.parametrize('queue_class', [StandardQueue, GroupedQueue])
@pytest.mark.parametrize('timestamp', [42, '', 'not a date', '2026-10-22T12:00:00'])
def test_invalid_creation_times_are_rejected(
    queue_class: type[StandardQueue] | type[GroupedQueue],
    timestamp: object,
) -> None:
    data = queue_class().to_json()
    data['created_at'] = timestamp
    with pytest.raises((TypeError, ValueError)):
        queue_class.from_json(data)


@pytest.mark.parametrize('queue_class', [StandardQueue, GroupedQueue])
def test_creation_time_is_normalized_to_utc(queue_class: type[StandardQueue] | type[GroupedQueue]) -> None:
    data = queue_class().to_json()
    data['created_at'] = '2026-10-22T12:00:00+08:00'
    restored = queue_class.from_json(data)
    assert restored.created_at == datetime(2026, 10, 22, 4, tzinfo=UTC)
    assert restored.to_json()['created_at'] == '2026-10-22T04:00:00+00:00'
