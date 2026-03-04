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
