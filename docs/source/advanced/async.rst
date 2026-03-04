Async
=====

In addition to the default synchronous client, there is also the asynchronous :py:class:`amqcsl.AsyncDBClient`.
The async client contains the same methods as :py:class:`amqcsl.DBClient`, but most methods will require awaiting.
There is an ``async`` template for a basic script using the async client, and the ``character`` and ``character_compact`` templates
are both async.

Usage
-----

The main differences are the usual ones when going from sync to async: ``with`` and ``for`` become ``async with`` and ``async for``,
calling methods require ``await``, and functions should be prefixed with ``async``. Lists and groups can be fetched with the same properties
as the sync client, and queueing client operations still requires an await despite not making any network requests.

Here's an example script with the sync client that finds all tracks that has You Watanabe in the extra metadata:

.. code-block:: python

    import os
    import logging
    from log import setup_logging

    import amqcsl
    from amqcsl.objects import CSLTrack
    from rich.pretty import pprint


    def main():
        with amqcsl.DBClient(
            username=os.getenv('AMQ_USERNAME'),
            password=os.getenv('AMQ_PASSWORD'),
        ) as client:
            group = client.groups['Love Live! Sunshine!!']
            my_list = client.lists['LoveLive']
            tracks_with_you: list[CSLTrack] = []
            for track in client.iter_tracks(active_list=my_list, groups=[group]):
                meta = client.get_metadata(track)
                if meta is None:
                    continue
                if any(m.value == 'You Watanabe' for m in meta.extra_metas):
                    tracks_with_you.append(track)
            pprint(tracks_with_you)


    logger = logging.getLogger('Sync')
    setup_logging()
    main()


Here is an async script doing the same thing:

.. code-block:: python

    import asyncio
    import logging
    import os

    import amqcsl
    from amqcsl.objects import CSLTrack
    from rich.pretty import pprint

    from log import setup_logging


    async def main(logger: logging.Logger):
        async with amqcsl.AsyncDBClient(
            username=os.getenv('AMQ_USERNAME'),
            password=os.getenv('AMQ_PASSWORD'),
        ) as client:
            group = client.groups['Love Live! Sunshine!!']
            my_list = client.lists['LoveLive']
            async with asyncio.TaskGroup() as tg:
                tasks = [
                    (track, tg.create_task(has_you_meta(client, track)))  #
                    async for track in client.iter_tracks(active_list=my_list, groups=[group])
                ]
            tracks_with_you = [track for track, task in tasks if task.result()]
            pprint(tracks_with_you)


    async def has_you_meta(client: amqcsl.AsyncDBClient, track: CSLTrack) -> bool:
        meta = await client.get_metadata(track)
        if meta is None:
            return False
        return any(m.value == 'You Watanabe' for m in meta.extra_metas)


    logger = logging.getLogger('Async')
    setup_logging()
    asyncio.run(main(logger))
