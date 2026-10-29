Queues
======

Queues let you stage a bunch of edits, look over them, and then send them all at once.
They're separate from the client, so you can build one without being logged in, and nothing
is sent until you process it.

The simplest one is :py:class:`~amqcsl.queues.StandardQueue`. Its editing methods are the same as
the client's, but they only add the edit to the queue (and never need ``await``, even with the async client).

.. code-block:: python

    from rich.console import Console
    from amqcsl.queues import StandardQueue

    queue = StandardQueue()
    queue.track_edit(track, name='Corrected name')
    queue.track_add_metadata(track, metadata)

    Console().print(queue)
    print(len(queue))  # number of pending edits

Client edit methods always send immediately. The old client queue, ``commit()``, and ``queue=True`` are gone;
use a queue instead.

If you already have edits from somewhere else, like :py:meth:`~amqcsl.workflows.character.AsyncArtistToMeta.iter_edits`,
add them with ``queue.enqueue(edit)``, or ``queue.enqueue_batch(edits)`` for several at once.
Read-only operations like searches can't be queued.

Processing
----------

Pass the queue to :py:meth:`~amqcsl.DBClient.process_queue`:

.. code-block:: python

    with open('remaining-queue.json', 'w') as recovery:
        client.process_queue(queue, save_to=recovery)

or ``await client.process_queue(queue, save_to=recovery)`` with the async client.

Successful edits are removed from the queue as they go through. Whatever's left (failed edits, plus anything that
didn't get to run) is always written to ``save_to`` as JSON once processing ends, even if everything succeeded.
If you leave out ``save_to``, it goes to stdout.

By default, processing stops at the first failed request and raises the error. Pass ``stop_if_err=False``
to log request errors and keep going instead; the failed edits stay in the queue. Errors in your own
code always get raised.

The sync client sends edits one at a time. The async client runs several at once, but still sticks to
the client's request limit.

.. note::

    If something fails partway through, or you cancel the script, an edit left in the queue might have
    already reached the server. Check those before retrying.

Saving and loading
------------------

You can save a queue to any text stream with ``queue.to_file(stream)`` and load it back with ``StandardQueue.from_file(stream)``
(or ``GroupedQueue.from_file(stream)``).
This is the same format ``process_queue`` writes, so you can retry a recovery file by loading it and processing it again.

.. code-block:: python

    with open('queue.json', 'w') as file:
        queue.to_file(file)

    with open('queue.json') as file:
        queue = StandardQueue.from_file(file)

Audio uploads save the file path, not the audio itself, so the file needs to still be there when you load the queue.

You can also look at or run a saved queue from the terminal with ``amqcsl queue display`` and ``amqcsl queue run``;
see :doc:`cli`.

Each queue records when it was created in ``queue.created_at`` (UTC), and keeps it through saving, loading, and processing.
Queue files saved before this was added load with ``created_at=None``.

Grouped queues
--------------

:py:class:`~amqcsl.queues.GroupedQueue` sorts track and song edits by what they're editing, so all the edits to one
track end up together. This makes them much easier to review, and lets compatible edits be combined:

.. code-block:: python

    from amqcsl.queues import GroupedQueue

    queue = GroupedQueue()
    queue.track_edit(track, name='Corrected name')
    queue.track_edit(track, original_name='元の名前')
    queue.track_add_metadata(track, metadata)
    queue.track_remove_metadata(track, old_metadata)

    Console().print(queue)
    await client.process_queue(queue, save_to=recovery)

The two ``track_edit`` calls above become a single edit, and multiple metadata additions to the same track become a
single request. Tracks and songs are processed in the order they were first added.

Only track and song edits can go in a grouped queue. Lists, groups, and albums raise ``TypeError``;
use a :py:class:`~amqcsl.queues.StandardQueue` for those.

Printing a grouped queue shows one panel per track or song, with its current name, ID, and artists,
and the pending changes underneath. Removals are red and additions are green. To change the panel width (100 by default),
use ``Console().print(queue.display({'width': 80}))``.

Overlapping edits
~~~~~~~~~~~~~~~~~

By default, a grouped queue refuses edits that would clash with something already queued, raising ``ValueError``. For example:

- setting the same field twice (song edits always count, since they send both name and disambiguation)
- adding and removing the same metadata
- two audio uploads or imports for the same track

If you'd rather the latest edit win, use ``GroupedQueue(allow_overlapping_edits=True)``. Deleting a song replaces
everything else queued for it. ``None`` leaves a field alone, but empty strings and lists are treated as real values.

``enqueue_batch`` on a grouped queue is all-or-nothing: if any edit is rejected, none of the batch is added.
(On a standard queue, edits before the bad one are kept.)

Workers
~~~~~~~

With the async client, each track or song gets worked on by one worker at a time. The number of workers is picked
automatically by dividing the client's request limit by the average number of edits per target. To pick the divisor
yourself, pass something like ``GroupedQueue(worker_ratio=2)``. ``worker_ratio`` is also saved with the queue.
