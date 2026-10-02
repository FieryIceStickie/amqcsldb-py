Clients
=======

.. autoclass:: amqcsl.DBClient
   :members:
   :undoc-members:

.. autoclass:: amqcsl.AsyncDBClient
   :members:
   :undoc-members:


Query bundle execution
----------------------

Ordinary ``Bundle[R]`` vendors yield HTTP requests and receive HTTP responses, returning
one final result. ``StreamingBundle[T]`` vendors use the same HTTP response contract and
also yield ``Items[T]`` events containing iterables of results. After an item event is
consumed, the driver resumes the vendor with ``None``. Item conversion remains lazy, and
no next request is sent until the current item event has been consumed.

The public ``iter_tracks``, ``iter_songs``, and ``iter_artists`` methods drive these events.
Sync pagination fetches one page at a time. Async pagination yields the first page before
fetching the remaining pages concurrently and yielding their items in query order.
Page strategies only select offsets; response validation and item events belong to the
page bundle. Raw page tuples remain an internal parsing detail.

For composition with ordinary bundles, ``stream.collect()`` returns a generic
``CollectBundle`` that forwards requests, consumes item events, and returns a complete list.
This is suitable for ``client.process`` and ``ParallelBundle``. Calling ordinary ``process``
on an uncollected stream raises when it encounters an item event, rather than discarding results.
Streaming drivers and collection adapters close their underlying vendors when iteration ends
or item conversion fails.
