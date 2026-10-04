Adding Character Metadata
=============================

Creating the script
-------------------

To start, use the ``character`` template provided by the CLI:

.. code-block:: zsh

    amqcsl make idoly_pride.py -t character

.. tip::
   Remember to prefix with either ``python3 -m amqcsl`` or ``uv run amqcsl``.

Filling in the info
--------------------

Firstly, replace ``INSERT GROUP NAME HERE`` with the group you want to search by,
or edit the code to iterate over the tracks you want (see :ref:`iter-info`).

Fill in the artist dictionary with artist names mapped to character names, separated by a comma and a space:

.. code-block:: python

    artists: cm.ArtistDict = {
        'Mirai Tachibana': 'Kotono Nagase',
        'Kokona Natsume': 'Nagisa Ibuki',
        'Koharu Miyazawa': 'Saki Shiraishi',
        'Kanata Aikawa': 'Suzu Narumiya',
        'Moka Hinata': 'Mei Hayasaka',
        'Mai Kanno': 'Sakura Kawasaki',
        'Yukina Shutou': 'Shizuku Hyoudou',
        'Kanon Takao': 'Chisa Shiraishi',
        'Moeko Yuuki': 'Rei Ichinose',
        'Nao Sasaki': 'Haruko Saeki',
        'Sora Amamiya': 'Rui Tendou',
        'Momo Asakura': 'Yuu Suzumura',
        'Shiina Natsukawa': 'Sumire Okuyama',
        'Haruka Tomatsu': 'Rio Kanzaki',
        'Ayahi Takagaki': 'Aoi Igawa',
        'Minako Kotobuki': 'Ai Komiyama',
        'Aki Toyosaki': 'Kokoro Akazaki',
        'Sayaka Kanda': 'Mana Nagase',
    }

If you've filled in the members of a group, you can leave the group itself out of the dictionary.
When it shows up on a track, the mapping will fetch its members from the database and combine
their character metadata. For example, if you've filled in all five members of ``Tsuki no Tempest``,
you don't need to write out their characters again for the group.

If you do provide an entry for the group, it'll take priority. If the member itself is a group, then it will fetch
that group's members as well. If a cycle exists, it will report the group as incomplete. This process
is done once per group and cached.

The key can come in three formats:

1. name
2. (name, disambiguation)
3. :py:class:`ArtistName <amqcsl.workflows.character.ArtistName>` (name, original name, disambiguation)

It's fine to not provide the full information as long as the result is unique; for example,
``Mirai Tachibana`` works without an original name or disambiguation if there is only one matching artist.
For duplicate names, use a tuple or ``cm.ArtistName`` to narrow the match.
The function will error if there are duplicates, so it's fine to be lazy at first and add more info if necessary.

You can customize the separator by passing ``sep`` into
:py:func:`make_artist_to_meta <amqcsl.workflows.character.make_artist_to_meta>`.
The ``character`` template uses this function directly.

Search phrases
--------------

When creating the mapping,
:py:func:`make_artist_to_meta <amqcsl.workflows.character.make_artist_to_meta>`
needs to search the database to find your artists. Searching for a group like ``Hoshimi Production``
can get a lot of them in one go, which is faster than searching for each name individually.
You can pass in a list of search phrases like this:

.. code-block:: python

    artist_to_meta = cm.make_artist_to_meta(client, artists, ['Hoshimi Production'])

It's fine if the search phrases don't cover all artists; it'll search for any remaining names
individually afterwards. If you pass in multiple phrases, it'll search them in parallel. You don't
need to provide any, but it'll speed things up if you're working with large groups of artists.


Applying the mapping
--------------------

Use :py:meth:`~amqcsl.workflows.character.AsyncArtistToMeta.iter_edits` to process your tracks.
It prepares the character metadata changes and yields edits for you to enqueue, as in the template:

.. code-block:: python

    from contextlib import aclosing

    artist_to_meta = await cm.make_artist_to_meta(client, artists, ['Hoshimi Production'])
    tracks = client.iter_tracks('My album')
    async with aclosing(artist_to_meta.iter_edits(tracks)) as edits:
        async for bundle in edits:
            client.enqueue(bundle)

    if cm.prompt(client.queue):
        await client.commit()

The mapping uses the client that created it, so you'll want to run this inside your client's
``async with`` block. Preparing and enqueueing edits doesn't send the changes to the database,
so call ``client.commit()`` after you've checked the queue to apply them.

When processing tracks:

* Existing character metadata is compared with the inferred metadata, adding missing entries and
  removing stale ones. Other metadata is left alone, and tracks that already have the right metadata
  don't yield an edit.
* Off Vocal and instrumental tracks are skipped. Groups with no members resolve to no character
  metadata, so old character entries can still be removed from those tracks.
* The async mapping processes up to ``client.max_request_count`` tracks concurrently and yields edits
  as they finish. Exclusion prompts are handled one at a time, while other requests can continue.
* ``aclosing`` ensures pending work is cancelled if you break out of the loop or an error occurs.
  Edits you've already enqueued remain in the queue. If a terminal prompt is open, you'll need to
  finish answering it before cancellation completes, and its answer is then discarded.

If you're using ``DBClient`` instead, use
:py:meth:`~amqcsl.workflows.character.SyncArtistToMeta.iter_edits` with a regular ``for`` loop:

.. code-block:: python

    artist_to_meta = cm.make_artist_to_meta(client, artists, ['Hoshimi Production'])
    for bundle in artist_to_meta.iter_edits(client.iter_tracks('My album')):
        client.enqueue(bundle)

    if cm.prompt(client.queue):
        client.commit()

Unknown artists
---------------

If a track has artists whose character metadata can't be resolved, the prompt shows the track ID,
track name, credited artists, and the reasons it needs your input. Incomplete groups also show
known members alongside the members without character metadata. Choose one of:

* ``y`` to exclude the listed artists and continue with the remaining artists on the track.
  Exclusions are remembered for later tracks, so you won't keep being asked about the same artist.
* ``n`` to raise an error and stop processing.
* ``i`` to skip this track, leaving its metadata unchanged. This doesn't remember an exclusion,
  so the artist can show up in another prompt later.
* ``q`` to quit. The template catches the quit and logs an exit message.

If a group is incomplete, none of that group's character metadata is used. Excluding it lets the
other credited artists continue to be processed, including removal of stale character metadata.

If you already know which artists to ignore, pass them in as ``exclude`` when creating the mapping:

.. code-block:: python

    artist_to_meta = await cm.make_artist_to_meta(
        client,
        artists,
        ['Hoshimi Production'],
        exclude=['Artist to ignore'],
    )

These use the same name formats as the artist dictionary and must match a unique artist. Exclusions
take priority over dictionary entries, and excluded group members are skipped during inference.

You can also pass a ``should_exclude`` function to ``iter_edits`` to make the decision yourself:

.. code-block:: python

    from collections.abc import Sequence
    from amqcsl.objects import CSLMetadata, CSLTrack

    def should_exclude(
        track: CSLTrack,
        reasons: Sequence[cm.Reason],
        existing_metadata: CSLMetadata | None,
    ) -> cm.ExcludeDecision:
        for failure in reasons:
            print(failure.artist.name)
            if failure.reason is cm.UNKNOWN_ARTIST:
                print('No character metadata for this artist')
            else:
                print('Missing members:', [member.name for member in failure.reason.artists])
        return cm.ExcludeDecision.EXCLUDE

    async with aclosing(artist_to_meta.iter_edits(tracks, should_exclude)) as edits:
        async for bundle in edits:
            client.enqueue(bundle)

The callback takes ``(track, reasons, existing_metadata)``. The third argument contains the track's
current metadata, or ``None`` if it has none. Existing character metadata is also shown in the default
exclusion prompt. The unresolved artists are :py:class:`~amqcsl.workflows.character.Reason` objects,
each with an ``artist`` and a ``reason``:

* ``UNKNOWN_ARTIST`` means the artist has no character metadata in the mapping.
* ``INCOMPLETE_GROUP`` reports the credited group, with unresolved members in ``artists`` and resolved
  members in ``known_artists``. For nested groups, unresolved members may come from further down the
  group hierarchy, and cycles are also reported as incomplete.

Return a :py:class:`~amqcsl.workflows.character.ExcludeDecision`: ``EXCLUDE``, ``ERROR``, and ``IGNORE``
have the same effects as the prompt choices above.

Async mappings accept either a regular callback or an async one. Regular callbacks run on the event
loop, so use an async callback if it needs to wait. The built-in
:py:func:`~amqcsl.workflows.character.async_prompt_should_exclude` only runs terminal input in a thread.

One-off edits
-------------

For a single track, use :py:meth:`~amqcsl.workflows.character.AsyncArtistToMeta.apply`.
It returns an edit for you to enqueue, or ``None`` if the track doesn't need changes or was skipped:

.. code-block:: python

    bundle = await artist_to_meta.apply(track)
    if bundle is not None:
        client.enqueue(bundle)

With ``DBClient``, call :py:meth:`~amqcsl.workflows.character.SyncArtistToMeta.apply` without ``await``.
You can pass the same ``should_exclude`` callback as with ``iter_edits``. Neither method enqueues or
commits changes for you.

Reading the mapping
-------------------

:py:class:`~amqcsl.workflows.character.ArtistToMeta` works like a dictionary for looking up character metadata,
so you can use ``artist_to_meta[artist]``, ``get()``, ``keys()``, ``values()``, and ``items()``.
Lookups accept either an artist sample or a full artist object.

Character metadata is stored in ``artist_to_meta.metadata``, and excluded artist IDs are stored in
``artist_to_meta.excluded_artists``. Group metadata is filled in as tracks are processed and cached
for later tracks, so you don't need to fetch it yourself.
