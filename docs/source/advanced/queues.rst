Queues
======

Build a :py:class:`~amqcsl.queues.StandardQueue` independently of a client. Its editing methods
match the client methods, but only stage work. They never require ``await``. Client edit methods
always send immediately; the old client queue, ``commit()``, and ``queue=True`` are removed.

Review edits with ``console.print(queue)`` or ``console.print(queue.display())``. Standard queues
use the existing Rich representations. ``len(queue)`` counts pending operations after merging.

Both queues provide ``enqueue_batch(bundles)``. Standard queues call ``enqueue()`` for each
bundle and retain earlier additions if a later bundle or iteration fails. Grouped queues
validate the whole batch before adding any edits, as described below.

Grouped queues
--------------

Use :py:class:`~amqcsl.queues.GroupedQueue` to collect track and song edits by target ID.
Artist grouping is reserved for future artist edit operations; there are no artist edit
bundles yet. Each group keeps its edits in insertion order, and groups run in the order their
targets first appeared. Parallel batches are flattened recursively and their edits are
regrouped by target.

Use ``queue.enqueue_batch(bundles)`` when edits must be added all together or not at all.
It consumes and validates the entire iterable before adding any work. If any edit is
unsupported or iteration fails, the queue stays unchanged. Repeated ``enqueue()`` calls
instead retain earlier additions if a later call fails.

.. code-block:: python

    from amqcsl.queues import GroupedQueue

    queue = GroupedQueue()
    queue.track_edit(track, name='Corrected name')
    queue.track_add_metadata(track, metadata)
    queue.track_remove_metadata(track, old_metadata)
    await client.process_queue(queue, save_to=recovery)

The async client runs concurrent edits within each group. Insertion order determines task
creation order; it does not guarantee request completion order. Every request still uses the
client's request limit. A group failure skips its unsent requests while active requests finish.

Compatible edits share requests: changes to different track fields become one update, and
metadata additions become one addition request per target. Distinct metadata removals still
require separate requests. Merging preserves the first occurrence's position in the group.

Overlapping entries raise ``ValueError`` by default, whether the conflict is with existing
pending work or within an incoming batch. Adding and removing the same metadata entry counts
as an overlap; artist links use artist ID and role, ignoring credited names. Two audio
operations conflict, as do two explicit metadata override settings. Song updates send both
name and disambiguation, so two song update calls overlap even when different arguments are supplied.

Set ``GroupedQueue(allow_overlapping_edits=True)`` to merge overlapping edits. The last explicit
field value, artist credit, or audio operation wins. For each metadata entry, only the last
addition or removal remains; repeated removals send one request. Deleting a song supersedes
all other pending work for that song. Empty lists and strings are explicit proposals; ``None``
leaves an earlier field proposal intact. Edits already excluded by metadata filtering remain excluded.

Validation and merging operate on staged copies. A rejected batch leaves existing requests
untouched. Completed operations stop participating in overlap checks. Once merged, an operation
is removed from the queue as a unit when its request succeeds; inspect the current queue when
reviewing or manually completing pending operations.

By default, the worker count is the request limit divided by the average number of pending
edits per group, rounded down. Set ``GroupedQueue(worker_ratio=2)`` to choose that ratio yourself.
Nonempty queues always get at least one worker, and workers never outnumber pending groups.

Lists, groups, albums, and unrecognized bundles raise ``TypeError`` in a grouped queue.
If any child of a parallel bundle is unsupported, none of its edits are added. Use
:py:class:`~amqcsl.queues.StandardQueue` for other edits. Streaming and known read-only
operations cannot be queued in either queue type.

Restore with ``GroupedQueue.from_file(stream)``. The JSON saves group order, remaining edits,
the worker ratio, and the overlap option.

``console.print(queue)`` shows one panel per pending target, in appearance order. Track panels
always show the current name, ID, and artist credits with their join phrases. Pending name,
original name, original artist, and type changes appear underneath without another heading.
Groups, artist credits, linked song, metadata, and audio each get
a section only when affected. Song panels use the same style, including a prominent deletion
message when applicable.

Removals appear before additions, in red and green respectively. Proposed field values are
yellow, referenced objects are cyan, and IDs are gray. Artist diffs include disambiguation,
credited names when different, and join phrase changes. Long values wrap instead of losing
their endings. Only merged pending proposals are displayed; display options are not saved.

Use ``console.print(queue.display({'width': 80}))`` to set a maximum panel width; the default
is 100 columns, constrained further by the terminal. Displaying a queue neither sends requests
nor changes its pending edits.

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

Creation time
-------------

Queues record their creation time in ``queue.created_at`` as a UTC datetime. Saving,
loading, and processing preserve that time in the JSON ``created_at`` field. Older
queue files without a timestamp load with ``created_at=None``.
