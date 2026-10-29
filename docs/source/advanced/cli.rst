Command Line
============

A small CLI is bundled with the library for setting up scripts and working with saved queues.
The CLI is made with `typer <https://typer.tiangolo.com/>`_.

queue
-----

Review or run a saved standard or grouped queue. The queue type is read from its JSON file.

.. code-block:: zsh

    amqcsl queue display queue.json
    amqcsl queue run queue.json > remaining.json

``display`` prints the queue to stdout without contacting the database.
``run`` processes queued edits with the async client and the queue's worker settings,
stopping on the first error. It writes
remaining work as JSON to stdout, including an empty queue after success. Both commands
leave the input file unchanged. Use a different output file when redirecting recovery JSON.

``run`` reads ``AMQ_USERNAME``, ``AMQ_PASSWORD``, and ``SESSION_PATH`` from the environment
or the current directory's ``.env`` file. Environment variables take priority.
``SESSION_PATH`` is required; ``amqcsl init`` writes it to ``.env``. Credentials can be omitted
with a valid saved session.

init
----

Initialize a directory for writing scripts.

**Usage**

.. code-block:: zsh

    amqcsl init [dir]

**Description**

- ``dir``  
  Path to the directory to initialize, defaults to cwd

This command creates directories ``log/``, ``logs/``, ``scripts/``, and files ``.env`` and ``.gitignore``. The user will be prompted with credentials to fill out the env file. 
If there is an existing directory called ``log/``, the command will terminate early. For the files, data will be appended if the files already exist, and added to a new file if it doesn't.

make
----

Create a new script from a template.

.. code-block:: zsh

    amqcsl make [-t] [dest]

**Description**

- ``dest``
  Path for the destination file, required
- ``-t``/``--template``
  The file template to generate, defaults to ``simple``. See below for available templates.


.. tab-set::

   .. tab-item:: simple

      .. literalinclude:: ../../../src/amqcsl/_templates/scripts/simple.py.txt
         :language: python
         :caption: Default Sync

   .. tab-item:: character

      .. literalinclude:: ../../../src/amqcsl/_templates/scripts/character.py.txt
         :language: python
         :caption: Character

   .. tab-item:: async

      .. literalinclude:: ../../../src/amqcsl/_templates/scripts/async.py.txt
         :language: python
         :caption: Default Async

  
