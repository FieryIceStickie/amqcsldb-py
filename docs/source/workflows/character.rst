Adding Character Metadata
=============================

Creating the script
-------------------

To start, use the ``character`` template (or the ``character_compact`` template, see below) provided by the CLI:

.. tab-set::

   .. tab-item:: Normal

      .. code-block:: zsh

         python3 -m amqcsl make idoly_pride.py -t character

   .. tab-item:: With uv

      .. code-block:: zsh

         uv run amqcsl make idoly_pride.py -t character

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
it will prompt you with the track. Press enter to continue, or type ``q`` to quit the program.

After processing all the tracks, it'll prompt you with all the queued metadata. Have a look through it,
and if it's fine then type `y` and enter to make the changes, or type `n` to not commit the changes
(or press ``q`` to quit, that works too).

Final notes
-----------

By default,
:py:func:`make_artist_to_meta <amqcsl.workflows.character.make_artist_to_meta>` and 
:py:func:`compact_make_artist_to_meta <amqcsl.workflows.character.compact_make_artist_to_meta>`
will search for each artist name one by one and match them up. Often, you can make fewer requests by
searching for a group like ``Hoshimi Production``, since the search result includes all the artists
inside that group. You can pass in a list of search phrases to both functions like this:

.. code-block:: python

    artist_to_meta = cm.compact_make_artist_to_meta(client, artists, ['Hoshimi Production'])

It's fine if the search phrase doesn't cover all artists, it'll go back to the default after exhausting
the list of search phrases. This isn't necessary, but it'll just speed things up if you're working with
large groups of artists.

