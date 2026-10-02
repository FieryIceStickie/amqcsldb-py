Adding Character Metadata
=============================

Creating the script
-------------------

To start, use the ``character`` template (or the ``character_compact`` template, see below) provided by the CLI:

.. code-block:: zsh

    amqcsl make idoly_pride.py -t character

.. tip::
   Remember to prefix with either ``python3 -m amqcsl`` or ``uv run amqcsl``.

Filling in the info
--------------------

Firstly, replace ``INSERT GROUP NAME HERE`` with the group you want to search by,
or edit the code to iterate over the tracks you want (see :ref:`iter-info`).

After that, you'll want to fill in the two dictionaries. For characters, fill it with
keys -> character names:

.. code-block:: python

    characters: cm.CharacterDict = {
        'kotono': 'Kotono Nagase',
        'nagisa': 'Nagisa Ibuki',
        'saki': 'Saki Shiraishi',
        'suzu': 'Suzu Narumiya',
        'mei': 'Mei Hayasaka',
        'fran': 'fran',
        'rio': 'Rio Kanzaki',
        'aoi': 'Aoi Igawa',
    }

For artists, fill it with artist name -> keys separated by spaces:

.. code-block:: python

    artists: cm.ArtistDict = {
        'Mirai Tachibana': 'kotono',
        ArtistName('Lynn', original_name='Lynn'): 'fran',
        'Tsuki no Tempest': 'kotono nagisa saki suzu mei',
        ('LizNoir', 'Idoly Pride (Anime)'): 'rio aoi',
    }

The key can come in three formats:

1. name
2. (name, disambiguation)
3. :py:class:`ArtistName <amqcsl.workflows.character.ArtistName>` (name, original name, disambiguation)

It's fine to not provide the full information as long as the result is unique; for example,
we just did ``Tsuki no Tempest`` even though it has an original name and disambiguation, but for
``Lynn`` we needed to provide the original name since there are two artists named ``Lynn`` in the database.
The function will error if there are duplicates, so it's fine to be lazy at first and add more info if necessary.

If necessary, you can use a different separator for the keys, which you'll need to pass into
:py:func:`make_artist_to_meta <amqcsl.workflows.character.make_artist_to_meta>` as ``sep``.

A more compact way
-------------------

Due to popular request, you can also input character names directly into artists. Instead of
using the ``character`` template, use the ``character_compact`` template, and instead of keys,
fill it out with character names separated by a comma and a space:

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
        'Sunny Peace': 'Sakura Kawasaki, Shizuku Hyoudou, Chisa Shiraishi, Rei Ichinose, Haruko Saeki',
        'Tsuki no Tempest': 'Kotono Nagase, Nagisa Ibuki, Saki Shiraishi, Suzu Narumiya, Mei Hayasaka',
        'TRINITYAiLE': 'Rui Tendou, Yuu Suzumura, Sumire Okuyama',
        ('LizNoir', 'Idoly Pride'): 'Rio Kanzaki, Aoi Igawa, Ai Komiyama, Kokoro Akazaki',
        ('LizNoir', 'Idoly Pride (Anime)'): 'Rio Kanzaki, Aoi Igawa',
    }

You can customize the separator by passing ``sep`` into
:py:func:`compact_make_artist_to_meta <amqcsl.workflows.character.compact_make_artist_to_meta>`. If you're reusing
character names a lot, the first method is preferable to minimize typos.

Running the script
-------------------

Now, if you run the script, it'll go through and queue all the metadata changes necessary (Feel
free to run this on tracks already filled with metadata; it won't do anything if the metadata
is correct, but it will change it if it's incorrect). If a track shows up with unrecognized artists,
it will prompt you once with the track and all unresolved artists. Choose ``y`` to exclude
those artists, ``n`` to raise an error, or ``q`` to quit. Exclusions are remembered for later tracks.

After processing all the tracks, it'll prompt you with all the queued metadata. Have a look through it,
and if it's fine then type `y` and enter to make the changes, or type `n` to not commit the changes
(or press ``q`` to quit, that works too).

Search phrases
--------------

When creating the mapping,
:py:func:`make_artist_to_meta <amqcsl.workflows.character.make_artist_to_meta>` and
:py:func:`compact_make_artist_to_meta <amqcsl.workflows.character.compact_make_artist_to_meta>`
need to search the database to find your artists. Searching for a group like ``Hoshimi Production``
can get a lot of them in one go, which is faster than searching for each name individually.
You can pass in a list of search phrases to both functions like this:

.. code-block:: python

    artist_to_meta = cm.compact_make_artist_to_meta(client, artists, ['Hoshimi Production'])

It's fine if the search phrases don't cover all artists; it'll search for any remaining names
individually afterwards. If you pass in multiple phrases, it'll search them in parallel. You don't
need to provide any, but it'll speed things up if you're working with large groups of artists.


Applying the mapping
--------------------

If you've filled in the members of a group, you can leave the group itself out of the dictionary.
When it shows up on a track, the mapping will fetch its members from the database and combine
their character metadata. For example, if you've filled in all five members of ``Tsuki no Tempest``,
you don't need to write out their characters again for the group. It'll remember the result for
later tracks too.

If you do provide an entry for the group, it'll just use that. Only forward relations of type
``GroupMember`` are used to find members; other relations are ignored. If a member is itself a
group, that group needs to already be in the mapping, since it won't go and fetch another set of members.

To use the mapping, call ``artist_to_meta.apply(track)`` for each track. It'll fetch the existing metadata
and queue any additions and deletions necessary:

.. code-block:: python

    artist_to_meta = cm.compact_make_artist_to_meta(client, artists, ['Hoshimi Production'])
    for track in client.iter_tracks('My album'):
        artist_to_meta.apply(track)
    client.commit()

If you're using ``AsyncDBClient``, you'll need to await these calls:

.. code-block:: python

    artist_to_meta = await cm.compact_make_artist_to_meta(client, artists, ['Hoshimi Production'])
    async for track in client.iter_tracks('My album'):
        await artist_to_meta.apply(track)
    await client.commit()

The mapping uses the client that created it. It also works like a dictionary for reading metadata,
so you can do things like
``artist_to_meta[artist]`` or ``artist_to_meta.get(artist)``, or use ``keys()``, ``values()``, and ``items()``.
It's fine to pass in either an artist sample or a full artist object.

The metadata is stored in ``artist_to_meta.metadata``, and excluded artist IDs are stored in
``artist_to_meta.excluded_artists``. Search phrases are only needed when creating the mapping.
If you're updating an older script, replace ``queue_character_metadata`` with ``artist_to_meta.apply(track)``;
you no longer need to fetch the track's metadata yourself.

If you already know which artists to ignore, pass them in as ``exclude`` when creating the mapping:

.. code-block:: python

    artist_to_meta = cm.compact_make_artist_to_meta(
        client,
        artists,
        ['Hoshimi Production'],
        exclude=['Artist to ignore'],
    )

These use the same name formats as the artist dictionary, and still need to match a unique artist.
Exclusions take priority over dictionary entries. Excluded members are also skipped when filling in
a group, so you don't need to provide character metadata for them.

If you want to handle unrecognized artists yourself, you can pass in a ``should_exclude`` function:

.. code-block:: python

    from collections.abc import Sequence
    from amqcsl.objects import CSLTrack

    def should_exclude(
        track: CSLTrack,
        artists: Sequence[cm.Reason],
    ) -> bool:
        for failure in artists:
            print(failure.artist.name)
            if failure.reason is cm.UNKNOWN_ARTIST:
                print('No character metadata for this artist')
            else:
                print('Missing members:', [member.name for member in failure.reason.artists])
        return True

    artist_to_meta.apply(track, should_exclude)

The function gets the track and a list of ``Reason`` objects. For each one, ``artist`` is the artist
on the track (for an incomplete group, this is the group, not the missing member). If the full artist
was fetched, you'll get that instead of a sample. The ``reason`` tells you what went wrong:

* ``UNKNOWN_ARTIST`` means there's no character metadata for that artist.
* ``INCOMPLETE_GROUP`` has an ``artists`` list containing all the members without metadata.
  If the group has no members in the database, this list will be empty.

It'll call your function once per track, after collecting all the artists it couldn't fill in.
Return ``True`` to exclude all of them, or ``False`` to raise an ``AMQCSLError``. If it raises,
no changes are queued for that track.

An excluded artist is treated as if they weren't on the track, so the other artists are processed
as usual. If a group is incomplete, none of its metadata is used; it won't just add the characters
it knows about. Stale character metadata will still be removed, but unrelated metadata is left alone.
If everything is already correct, nothing is queued. Off-vocal tracks are skipped.
