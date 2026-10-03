.. |pprint| replace:: `pprint <https://rich.readthedocs.io/en/stable/pretty.html#pprint-method>`__

Quickstart
==========

If you're just here to add character metadata, check out :doc:`workflows/character`.

Firstly, open the ``scripts/test.py`` file you created in :doc:`installation`
in your desired text editor. You should see the following line:

.. code-block:: python

   pprint('shiHib')

This is a placeholder for the template, and we'll replace this with actual code.

We'll use `Love Live! Sunshine!! <https://www.lovelive-anime.jp/uranohoshi/>`_ as
an example. First, let's grab the group,

.. code-block:: python

    sunshine_group = client.groups['Love Live! Sunshine!!']

and then search for all tracks matching that group and print their names.

.. code-block:: python

    for track in client.iter_tracks(groups=[sunshine_group]):
        print(track.name)

In summary, your code should now look like

.. code-block:: python

    with amqcsl.DBClient(
        username=os.getenv('AMQ_USERNAME'),
        password=os.getenv('AMQ_PASSWORD'),
    ) as client:
        sunshine_group = client.groups['Love Live! Sunshine!!']
        for track in client.iter_tracks(groups=[sunshine_group]):
            print(track.name)

If you now run the file again, you should see the track names being printed
to the console in 50 track batches::

    WATER BLUE NEW WORLD
    WONDERFUL STORIES
    "MY LIST" to you!
    ...
    KU-RU-KU-RU Cruller! (Rockabilly Ver.)
    LIVE with a smile!
    LIVE with a smile!
    [2025-06-03 00:06:46,965|amqcsl.client]:INFO: Page exhausted

This is the client fetching each page of the search result and automatically chaining them
together, so you don't need to worry about iterating over pages yourself. Before we get into the
features of the client, note that `rich <https://github.com/Textualize/rich>`_ is a dependency of
the library, and the database objects support pretty printing. You'll need to import |pprint|:

.. code-block:: python

   from rich.pretty import pprint

and then you can use |pprint| to display objects.


Lists & Groups
--------------

To access your lists, :py:class:`amqcsl.DBClient` provides the dictionary
:py:attr:`lists <amqcsl.DBClient.lists>`, which you index via list names. As we saw earlier, there is a
similar dictionary :py:attr:`groups <amqcsl.DBClient.groups>`. For example, I have a list
called ``rat``, so I would do

.. code-block:: python

    rat_list = client.lists['rat']
    pprint(rat_list)

the output of which is::

    CSLList(id='01969119-fe28-7299-b266-1798ae844f1f', name='rat', count=48)

You can create groups and lists,

.. code-block:: python

    # Don't actually run this it'll make a new group in the db
    new_group = client.create_group('new group') 

    # This creates a list called test containing tracks from lists rat and game
    new_list = client.create_list('test', client.lists['rat'], client.lists['game'])

and add/remove tracks from these lists:

.. code-block:: python

    # Remove all existing tracks
    client.list_edit(new_list, remove=client.iter_tracks(active_list=new_list))
    # Add all idoly pride tracks
    client.list_edit(new_list, add=client.iter_tracks(groups=[client.groups['IDOLY PRIDE']]))

    # Or equivalently,
    new_tracks = [*client.iter_tracks(groups=[client.groups['IDOLY PRIDE']])]
    existing_tracks = [*client.iter_tracks(active_list=new_list)]
    client.list_edit(new_list, add=new_tracks, remove=existing_tracks)

You can also rename a list with ``client.list_edit(new_list, name='new name')``, or delete it with
:py:meth:`list_delete <amqcsl.DBClient.list_delete>`:

.. code-block:: python

    client.list_delete(new_list)

If you already have a track's ID, you don't need to fetch the whole track to add or remove it.
You can use :py:class:`CSLTrackRef <amqcsl.objects.CSLTrackRef>` instead:

.. code-block:: python

    from amqcsl.objects import CSLTrackRef

    client.list_edit(rat_list, add=[CSLTrackRef('track-id')])

.. _iter-info:

Tracks, Songs & Artists
------------------------

We've already seen :py:meth:`iter_tracks <amqcsl.DBClient.iter_tracks>`, and there are similar methods
:py:meth:`iter_songs <amqcsl.DBClient.iter_songs>` and
:py:meth:`iter_artists <amqcsl.DBClient.iter_artists>` for iterating over songs and artists respectively:

.. code-block:: python

    sunshine_group = client.groups['Love Live! Sunshine!!']
    for track in client.iter_tracks('You Watanabe', groups=[sunshine_group]):
        pprint(track)
    for song in client.iter_songs('Shuka Saitou'):
        pprint(song)
    for artist in client.iter_artists('Aoi Nagatsuki'):
        pprint(artist)

For ``iter_tracks``, you can also filter by lists:

.. code-block:: python

   client.iter_tracks(active_list=client.lists['rat'])

As well as songs missing audio/info:

.. code-block:: python

   client.iter_tracks(groups=[sunshine_group], missing_audio=True, missing_info=True)

If you accidentally make a really large query, such as

.. code-block:: python

    for track in client.iter_tracks():
        pprint(track)

the code will raise a :py:exc:`QueryError <amqcsl.exceptions.QueryError>` saying it is too large.
You can change the max query size (defaults to 1500) via the :py:attr:`max_query_size <amqcsl.DBClient.max_query_size>`
attribute in :py:class:`DBClient <amqcsl.DBClient>` if necessary.

Detailed Object Fetching
-------------------------

When you click on a song/artist/track, you can get extra information like metadata, artist credits,
etc. This is supported via the following methods:

.. code-block:: python

    for song in client.iter_songs('Shuka Saitou'):
        song = client.get_song(song)
    for artist in client.iter_artists('Aoi Nagatsuki'):
        artist = client.get_artist(artist)
    for track in client.iter_tracks('You Watanabe'):
        meta = client.get_metadata(track)

Track metadata is stored separately, which is why :py:meth:`get_metadata <amqcsl.DBClient.get_metadata>`
looks different to the other methods. See
:py:class:`CSLSong <amqcsl.objects.CSLSong>`,
:py:class:`CSLArtist <amqcsl.objects.CSLArtist>`, and
:py:class:`CSLMetadata <amqcsl.objects.CSLMetadata>` for more information.
If a track doesn't have metadata, ``get_metadata`` returns ``None``.

Editing Tracks
--------------

To edit a track, use :py:meth:`track_edit <amqcsl.DBClient.track_edit>`:

.. code-block:: python

    terraria = client.groups['Terraria']
    video_games = client.groups['Video Games']
    for track in client.iter_tracks(groups=[terraria]):
        client.track_edit(track, groups=[terraria, video_games])

For metadata, you can add/remove them with
:py:meth:`track_add_metadata <amqcsl.DBClient.track_add_metadata>` and 
:py:meth:`track_remove_metadata <amqcsl.DBClient.track_remove_metadata>`:

.. code-block:: python

    from amqcsl.objects import ExtraMetadata

    terraria = client.groups['Terraria']
    game_meta = ExtraMetadata(is_artist=False, type='Game', value='Terraria')
    for track in client.iter_tracks(groups=[terraria]):
        meta = client.get_metadata(track)
        if meta is not None:
            for m in meta.extra_metas:
                if m.key == 'Game' and m.value != 'Terraria':
                    client.track_remove_metadata(track, m)
        client.track_add_metadata(track, game_meta, existing_meta=meta)

You can pass in multiple metadata objects to 
:py:meth:`track_add_metadata <amqcsl.DBClient.track_add_metadata>`, and passing
``existing_meta`` means the client won't duplicate the metadata if it already exists. The metadata
objects can either be
:py:class:`ArtistCredit <amqcsl.objects.ArtistCredit>` or
:py:class:`ExtraMetadata <amqcsl.objects.ExtraMetadata>`.

Queueing Operations
-------------------

In many use cases, it's likely you'd want to see your changes before actually applying them. The
client offers a queue for this case. When calling 
:py:meth:`track_add_metadata <amqcsl.DBClient.track_add_metadata>` and 
:py:meth:`track_remove_metadata <amqcsl.DBClient.track_remove_metadata>`, you can pass in a
``queue=True`` keyword argument to add the edit to the queue, and then commit the queue at the end
after confirming your changes:

.. code-block:: python
   
    from amqcsl.objects import ExtraMetadata
    from amqcsl.workflows.character import prompt

    terraria = client.groups['Terraria']
    game_meta = ExtraMetadata(is_artist=False, type='Game', value='Terraria')
    for track in client.iter_tracks(groups=[terraria]):
        meta = client.get_metadata(track)
        client.track_add_metadata(track, game_meta, existing_meta=meta, queue=True)

    if prompt(client.queue):
        client.commit()

Creating Albums and Adding Audio
--------------------------------

You can create albums with :py:meth:`create_album <amqcsl.DBClient.create_album>`. Pass in the tracks
as a list of discs, where each disc is a list of :py:class:`AlbumTrack <amqcsl.objects.AlbumTrack>` objects:

.. code-block:: python

    from amqcsl.objects import AlbumTrack

    client.create_album(
        name='My album',
        original_name='My album',
        year=2025,
        groups=[sunshine_group],
        tracks=[[AlbumTrack('Track name', 'Original track name', 'Artist name')]],
    )

To upload an audio file for an existing track, use :py:meth:`add_audio <amqcsl.DBClient.add_audio>`:

.. code-block:: python

    track = next(client.iter_tracks('Track name'))
    client.add_audio(track, 'audio.flac')

If another track already has the audio you want, you can copy it with
:py:meth:`import_audio <amqcsl.DBClient.import_audio>`:

.. code-block:: python

    source_track = next(client.iter_tracks('Track with audio'))
    client.import_audio(track, source_track)

These three methods also accept ``queue=True`` if you'd like to review the changes first.

Using the Async Client
----------------------

If you're using :py:class:`AsyncDBClient <amqcsl.AsyncDBClient>`, use ``async with`` and ``async for``,
and await the methods that make requests:

.. code-block:: python

    async with amqcsl.AsyncDBClient(
        username=os.getenv('AMQ_USERNAME'),
        password=os.getenv('AMQ_PASSWORD'),
    ) as client:
        sunshine_group = client.groups['Love Live! Sunshine!!']
        async for track in client.iter_tracks(groups=[sunshine_group]):
            meta = await client.get_metadata(track)
            pprint(meta)

Lists and groups are still accessed through ``client.lists`` and ``client.groups`` without awaiting.
You'll also need to await edits with ``queue=True``, and ``client.commit()``. See :doc:`advanced/async`
for a full script example.
