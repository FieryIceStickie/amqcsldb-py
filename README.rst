Installation
============

Run:

.. code-block:: zsh

    pip install "git+https://github.com/FieryIceStickie/amqcsldb-py"

Preferably inside a virtual environment.

Usage
=====

The library offers a small CLI to help with setting up a script directory. Run:

.. code-block:: zsh

    amqcsl init scripts

It will prompt you for your amqbot username and password, which will be put into the `.env` file. You can edit
it manually if you enter it wrong.

This will make a directory `scripts` with a logs, a `.env` file, and a `.gitignore` if you're using git.
Do note that this will overwrite stuff that already exists.

After that, move into the scripts directory with:

.. code-block:: zsh

    cd scripts

And then run:

.. code-block:: zsh

    amqcsl make my_script.py

To make a script scaffold. You can add a flag like:

.. code-block:: zsh

    amqcsl make superstar_metadata.py -t character

To change the type of scaffold to use.

Currently there are two types:

* Simple: The default scaffold
* Character: A scaffold for adding character metadata

For actual script writing, I haven't had time to make proper docs yet, so see the ``examples`` directory on GitHub.
``hk_metadata`` and ``terraria_metadata`` are fairly simple examples, and if you're looking to add character metadata then
have a look at ``superstar_metadata`` for an example.

Ping/DM me on Discord (`fieryicestickie`) if you have questions or issues.
