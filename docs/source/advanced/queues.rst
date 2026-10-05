Queues
======

Build a :py:class:`~amqcsl.queues.StandardQueue` independently of a client. Its editing methods
match the client methods, but only stage work. They never require ``await``. Client edit methods
always send immediately; the old client queue, ``commit()``, and ``queue=True`` are removed.

Review edits with ``console.print(queue)`` or ``console.print(queue.display())``. Standard queues
use the existing Rich representations. ``len(queue)`` counts pending edits.

Saving and loading
------------------

Queues use text streams. You can save manually with ``queue.to_file(stream)`` and restore with
``StandardQueue.from_file(stream)``. Streams stay open, and writing uses their current position;
rewinding and truncating reused streams is up to you.

.. code-block:: python

    from io import StringIO
    from amqcsl.queues import StandardQueue

    queue = StandardQueue()
    queue.track_edit(track, name='Corrected name')
    saved = StringIO()
    queue.to_file(saved)
    saved.seek(0)
    restored = StandardQueue.from_file(saved)

Each queue type owns its JSON layout and schema version, initially ``0.0``. Loading checks the
queue type and version and restores every entry before returning. Audio uploads save the path,
not the audio; their normal constructor checks apply when loading.

Unsupported bundles are saved as tagged Rich representation strings for manual recovery.
Loading those entries raises an error explaining that serialization support is required.
Custom serialization hooks aren't available yet.

Processing
----------

.. code-block:: python

    with open('remaining-queue.json', 'w') as recovery:
        client.process_queue(queue, save_to=recovery)

Use ``await client.process_queue(queue, save_to=recovery)`` with the async client. The remaining
queue is always written, even after success or when the queue was empty. If ``save_to`` is omitted,
JSON goes to the current stdout. The destination is logged before writing.

Successful edits are removed immediately. Failed, skipped, and interrupted edits remain.
Bundle return values are discarded. Queueing new work or processing the same queue again while
it is running raises an error. Access from multiple threads is unsupported, and enqueueing the
same bundle twice is undefined behavior.

Standard queues retain insertion order. Parallel batches are flattened recursively into one
group. The sync client runs children sequentially; the async client starts concurrent child tasks
in insertion order and uses its shared request semaphore for every HTTP request. When a child
fails, unsent requests in that group are skipped, including later requests in a multi-step edit.
Requests already in flight finish. List and group caches are refreshed or invalidated only when
queued operations can change them.

With ``stop_if_err=True`` (the default), workers stop taking new groups, active groups finish,
and execution errors propagate. With ``False``, request errors are logged and workers continue
with other groups; failed edits remain queued. Programming errors always propagate. Multiple
execution errors, or an execution error together with a save error, are raised as an
``ExceptionGroup``.

Cancellation drains workers before saving and propagates ``CancelledError``. If saving also fails,
that failure is logged without replacing cancellation. A retained edit may already have affected
the server before an error or cancellation reached the client; inspect uncertain edits before retrying.
