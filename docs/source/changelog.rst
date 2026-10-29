Changelog
=========

Version 1.1.0
--------------

#. Added changelog
#. Added new ``workflows`` submodule and moved old ``utils`` into ``workflows/character``
#. Added :py:meth:`add_album <amqcsl.DBClient.create_album>` and :py:meth:`add_audio <amqcsl.DBClient.add_audio>`
#. Added tests


Version 1.1.1
--------------

#. Added async client and async support for character workflows
#. Added methods for editing/deleting songs/groups, and adding/deleting metadata on songs
#. Renamed add_album to :py:meth:`add_album <amqcsl.DBClient.create_album>` 


Version 1.1.2
--------------

#. Record queue creation times in ``queue.created_at`` and saved queue files. See :doc:`advanced/queues`.

#. :py:class:`~amqcsl.DBClient` and :py:class:`~amqcsl.AsyncDBClient` now require ``session_path``.
   Script templates read it from ``SESSION_PATH``.

#. Match character workflow exclusions as artists are encountered, avoiding preparation searches
   for excluded names. See :doc:`workflows/character`.

#. Display grouped edits as compact target panels with colored field, artist, metadata, and audio changes.
   See :doc:`advanced/queues`.

#. Reject overlapping grouped edits by default and merge compatible edits into shared requests.
   Opt in to replacing overlapping proposals with ``GroupedQueue(allow_overlapping_edits=True)``;
   see :doc:`advanced/queues`.

#. Add edit entry reporting and :py:func:`~amqcsl.bundles.has_overlap` for comparing bundle changes.

#. Add :py:class:`~amqcsl.queues.GroupedQueue` for track and song edits grouped by target, with configurable
   async workers and JSON recovery. See :doc:`advanced/queues`.

#. Reject known read-only operations when queueing edits.

#. Consolidate object and bundle serialization in ``objects/_serialization.py``.

#. Move bundles from ``amqcsl.clients.bundles`` to ``amqcsl.bundles``.

#. Rename ``parallel_actions()`` to ``action_bundle_compose()``.

#. Add independent :py:class:`~amqcsl.queues.StandardQueue` queues and
   :py:meth:`~amqcsl.AsyncDBClient.process_queue`, replacing client queues and ``queue=True``.
   Queues support review, JSON recovery, and retries; see :doc:`advanced/queues`.

#. Show existing character metadata in exclusion prompts and pass existing metadata as the third
   argument to :py:func:`~amqcsl.workflows.character.prompt_should_exclude` and custom exclusion callbacks.

#. Close pending character workflow tasks when generated scripts stop iterating.

#. Consolidate character scripts into the ``character`` template and
   :py:func:`~amqcsl.workflows.character.make_artist_to_meta`, using character names directly.
   Remove ``character_compact``, ``compact_make_artist_to_meta``, and ``CharacterDict``.

#. Use the filename without its extension for generated script logger names.

#. Add :py:meth:`~amqcsl.workflows.character.SyncArtistToMeta.iter_edits` and
   :py:meth:`~amqcsl.workflows.character.AsyncArtistToMeta.iter_edits` to yield edits for manual enqueueing.
   Async processing runs concurrently and keeps requests running during exclusion prompts.
#. :py:meth:`~amqcsl.workflows.character.SyncArtistToMeta.apply` and
   :py:meth:`~amqcsl.workflows.character.AsyncArtistToMeta.apply` now return prepared edits instead of enqueueing them.
#. Groups with no members now resolve to empty character metadata without prompting.
#. Show a compact character exclusion prompt with track details and unresolved artist names.

#. Fixed the endpoint for :py:meth:`~amqcsl.DBClient.song_add_metadata`.

#. Replaced object ``from_json`` methods with :py:func:`~amqcsl.objects.from_json` and
   replaced edit-object ``to_json`` methods with :py:func:`~amqcsl.objects.to_json`.
   Credit positions and album numbering are now keyword arguments.
#. Allow null song disambiguation and linked track names; corrected the ``linkedAMQSongs`` schema key.

#. Allow changing :py:attr:`~amqcsl.AsyncDBClient.max_request_count` while requests are running.
#. Stream audio uploads without buffering whole files in memory.
#. Scope saved session cookies to the database over HTTPS and stop logging session IDs.

#. Fixed async initialization cleanup, stalled pagination, and login/metadata HTTP error handling.
#. Allow clearing song disambiguation with an empty string.

#. Replaced HTTPX/RESPX with niquests/niquests-mock; enabled HTTP/2 and HTTP/3.
#. Increased request timeouts, with longer timeouts for audio uploads; redirects remain disabled.
#. Renamed the bundle client alias to ``httpClient``; underlying sessions and HTTP exceptions now use niquests.

#. Added :py:meth:`~amqcsl.DBClient.import_audio` and :py:meth:`~amqcsl.DBClient.list_delete`.
#. Added :py:class:`~amqcsl.objects.CSLTrackRef` for editing lists with track IDs.
#. Added ``exclude`` to :py:func:`~amqcsl.workflows.character.make_artist_to_meta` and
   ``compact_make_artist_to_meta``.

#. Changed :py:func:`~amqcsl.workflows.character.make_artist_to_meta` and
   ``compact_make_artist_to_meta`` to return
   :py:class:`~amqcsl.workflows.character.SyncArtistToMeta` or
   :py:class:`~amqcsl.workflows.character.AsyncArtistToMeta` mappings.
#. Added group metadata inference, cached exclusions, and parallel artist searches to the
   :doc:`character workflow <workflows/character>`.
#. Added recursive group inference and cycle detection to
   :py:meth:`~amqcsl.workflows.character.SyncArtistToMeta.apply` and
   :py:meth:`~amqcsl.workflows.character.AsyncArtistToMeta.apply`.
#. Added :py:class:`~amqcsl.workflows.character.ExcludeDecision` with an option to ignore tracks.
#. Skip instrumental tracks in :py:meth:`~amqcsl.workflows.character.SyncArtistToMeta.apply` and
   :py:meth:`~amqcsl.workflows.character.AsyncArtistToMeta.apply`.
#. Parallelized group and metadata fetching in :py:meth:`~amqcsl.workflows.character.AsyncArtistToMeta.apply`.
#. Replaced ``queue_character_metadata`` with
   :py:meth:`SyncArtistToMeta.apply <amqcsl.workflows.character.SyncArtistToMeta.apply>` and
   :py:meth:`AsyncArtistToMeta.apply <amqcsl.workflows.character.AsyncArtistToMeta.apply>`.
#. Added dictionary reads to :py:class:`~amqcsl.workflows.character.ArtistToMeta` and
   :py:meth:`~amqcsl.objects.CSLArtistSample.to_sample` to artist objects.
#. Added :py:obj:`~amqcsl.objects.ArtistType`, :py:obj:`~amqcsl.objects.ExtraMetadataType`,
   and :py:obj:`~amqcsl.objects.SongRelationType` literal types.
#. Refactored pagination in :py:class:`~amqcsl.DBClient` and :py:class:`~amqcsl.AsyncDBClient`.
#. Merged sync/async API tests and expanded input validation and edge-case coverage.
#. Fixed endpoint routing in :py:meth:`~amqcsl.DBClient.song_edit` and
   :py:meth:`~amqcsl.DBClient.song_delete_metadata`.
#. Fixed HTTP error handling in :py:meth:`~amqcsl.DBClient.track_edit` and
   ``commit()``.
#. Fixed generator inputs losing their values before requests were built.
#. Updated :doc:`character workflow documentation <workflows/character>`.
#. Removed runtime type validators from :py:class:`~amqcsl.DBClient` and
   :py:class:`~amqcsl.AsyncDBClient` operations.
