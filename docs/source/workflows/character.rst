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

Final notes
-----------

By default,
:py:func:`make_artist_to_meta <amqcsl.workflows.character.make_artist_to_meta>` and 
:py:func:`compact_make_artist_to_meta <amqcsl.workflows.character.compact_make_artist_to_meta>`
return mapping objects. Initialization searches the supplied phrases in parallel, then searches
for unmatched artist names. Often, you can make fewer requests by
searching for a group like ``Hoshimi Production``, since the search result includes all the artists
inside that group. You can pass in a list of search phrases to both functions like this:

.. code-block:: python

    artist_to_meta = cm.compact_make_artist_to_meta(client, artists, ['Hoshimi Production'])

It's fine if the search phrase doesn't cover all artists, it'll go back to the default after exhausting
the list of search phrases. This isn't necessary, but it'll just speed things up if you're working with
large groups of artists.


Applying the mapping
-------------------

Groups can be omitted from the input dictionary when their members have metadata entries.
When a group appears on a track, the mapping fetches its forward ``GroupMember`` relations
and combines the members' metadata. Explicit group entries take precedence. Other relation
types and reverse relations are ignored. Successful group inference is cached across tracks.
A member that is itself a group is looked up in the mapping; membership queries do not recurse.

Use the public helper to apply the mapping and queue all necessary additions and deletions:

.. code-block:: python

    artist_to_meta = cm.compact_make_artist_to_meta(client, artists, ['Hoshimi Production'])
    for track in client.iter_tracks('My album'):
        cm.apply_artist_to_meta(client, artist_to_meta, track)
    client.commit()

With ``AsyncDBClient``, await creation, application, and commit:

.. code-block:: python

    artist_to_meta = await cm.compact_make_artist_to_meta(client, artists, ['Hoshimi Production'])
    async for track in client.iter_tracks('My album'):
        await cm.apply_artist_to_meta(client, artist_to_meta, track)
    await client.commit()

The mapping also has an ``apply(track, should_exclude)`` method using the client supplied at
creation. It stores resolved metadata in ``metadata`` and excluded artist IDs in
``excluded_artists``. Dictionary operations such as ``artist_to_meta[artist]``,
``artist_to_meta.get(artist)``, iteration, and ``keys()``, ``values()``, and ``items()`` delegate
to the cached metadata. Full artist objects are converted to samples for lookup.
Search phrases are only used during creation. The former
``queue_character_metadata`` function is replaced by ``apply_artist_to_meta``; callers no
longer need to fetch existing track metadata themselves.

To control exclusions, supply a ``should_exclude(track, artists) -> bool`` callback:

.. code-block:: python

    from collections.abc import Sequence
    from amqcsl.objects import CSLTrack

    def should_exclude(track: CSLTrack, artists: Sequence[cm.Reason]) -> bool:
        for failure in artists:
            print(failure.artist.name)
            if failure.reason is cm.UNKNOWN_ARTIST:
                print('No character metadata for this artist')
            else:
                print('Missing members:', [member.name for member in failure.reason.artists])
        return True

    cm.apply_artist_to_meta(client, artist_to_meta, track, should_exclude)

Each ``Reason.artist`` identifies an artist credited on the track; a fetched ``CSLArtist``
is used when available. Its ``reason`` is either the ``UNKNOWN_ARTIST`` singleton or an
``INCOMPLETE_GROUP`` containing every member without metadata. An incomplete group with no
members contains an empty list. The callback runs once per track after all failures have been
collected. Returning ``True`` excludes every listed artist across subsequent tracks; returning
``False`` raises ``AMQCSLError`` and queues no changes for that track.

Excluded artists contribute no metadata. The remaining artists still determine additions
and stale character metadata deletions. Incomplete groups contribute no partial metadata.
Unrelated metadata is preserved, unchanged tracks queue nothing, and off-vocal tracks are skipped.
