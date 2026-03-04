Command Line
============

A small CLI is bundled with the library, designed to reduce boilerplate when writing scripts. It helps you quickly initialize a
working directory and generate script files from predefined templates. The CLI is made with `typer <https://typer.tiangolo.com/>`_.

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
~~~~

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

   .. tab-item:: character_compact

      .. literalinclude:: ../../../src/amqcsl/_templates/scripts/character_compact.py.txt
         :language: python
         :caption: Compact Character

   .. tab-item:: async

      .. literalinclude:: ../../../src/amqcsl/_templates/scripts/async.py.txt
         :language: python
         :caption: Default Async

  
