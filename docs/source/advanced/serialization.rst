Serialization
=============

Use :py:func:`amqcsl.objects.to_json` to turn a database object into a dictionary with
its database field names. Nested objects are converted too, and ``None`` values are kept.
You can then save the result with Python's ``json`` module:

.. code-block:: python

    import json

    from amqcsl.objects import CSLTrack, from_json, to_json

    serialized = json.dumps(to_json(track), ensure_ascii=False)
    restored = from_json(json.loads(serialized), CSLTrack)

:py:func:`~amqcsl.objects.from_json` takes the dictionary and the class to construct.
It replaces the old per-class calls, such as ``CSLTrack.from_json(data)``.
Both functions support songs, artists, tracks, lists, groups, metadata, and their nested
credits, relations, and samples. Full objects retain their additional fields when serialized.

Missing fields and incorrect values raise :py:class:`amqcsl.exceptions.QueryError` during
conversion. Extra fields are ignored. Track artist credits are sorted by position,
and timestamps remain strings accessible through the existing datetime properties.

Edit payloads
-------------

The edit classes support :py:func:`~amqcsl.objects.to_json` for producing API request
payloads. They don't support :py:func:`~amqcsl.objects.from_json`.

:py:class:`~amqcsl.objects.ArtistCredit`, :py:class:`~amqcsl.objects.ExtraMetadata`, and
:py:class:`~amqcsl.objects.NewSong` only need the object. Artist references are emitted
as IDs, and ``None`` values are kept.

:py:class:`~amqcsl.objects.TrackPutArtistCredit` needs its position, while
:py:class:`~amqcsl.objects.AlbumTrack` needs disc and track numbering:

.. code-block:: python

    credit_payload = to_json(credit, position=0)
    track_payload = to_json(track, disc_number=1, track_number=2, track_total=10)

These arguments must be supplied by keyword. A credit with no explicit name uses its
artist's name; an explicitly empty name stays empty. The clients supply positioning
arguments when building their requests, so ordinary editing calls stay the same.
