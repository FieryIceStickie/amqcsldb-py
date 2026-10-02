Changelog
=========

Version 1.1.0
--------------

#. Added changelog
#. Added new ``workflows`` submodule and moved old ``utils`` into ``workflows/character``
#. Added :py:meth:`add_album <amqcsl.DBClient.add_album>` and :py:meth:`add_audio <amqcsl.DBClient.add_audio>`
#. Added tests


Version 1.1.1
--------------

#. Added async client and async support for character workflows
#. Added methods for editing/deleting songs/groups, and adding/deleting metadata on songs
#. Renamed add_album to :py:meth:`add_album <amqcsl.DBClient.create_album>` 


Unreleased
----------

#. Character metadata factories now return sync/async artist mappings implementing
   ``ArtistToMeta``. Global and fallback artist searches are batched through bundles.
#. Groups encountered on tracks infer character metadata from their forward group members.
   Explicit metadata takes precedence, and successful inference is cached.
#. ``apply_artist_to_meta`` replaces ``queue_character_metadata`` and fetches existing metadata,
   queues additions and deletions, and handles cached exclusions through ``should_exclude``.
   Callbacks receive a track and ``Reason`` objects describing unknown artists or incomplete groups.
#. Artist mappings expose dictionary reads through their shared protocol. Artist samples provide
   ``to_sample()`` to strip full artist relations for hashable lookups.
#. Page queries provide ``collect()`` to compose full-list queries without a separate artist
   listing implementation. ``ParallelBundle`` accepts iterable inputs and preserves result order.
#. Added ``ArtistType``, ``ExtraMetadataType``, and ``SongRelationType`` literal types.
#. Streaming bundles yield HTTP requests and explicit ``Items`` events. Page queries receive
   HTTP responses directly, and stateless strategies select the next request offsets.
   Sync queries remain lazy; async queries yield the first page before fetching remaining pages
   concurrently. A generic collection adapter composes any stream with ordinary bundles.
