.. |uv| replace:: `uv <https://github.com/astral-sh/uv>`__
.. |venv| replace:: `venv <https://docs.python.org/3/library/venv.html>`__
.. |pip| replace:: `pip <https://pip.pypa.io/en/stable/>`__

Installation & Setup
=====================

For users who haven't used Python before, check out :ref:`uv_install`. For more experienced
users, a more typical installation documentation is listed below, but I would still recommend
using |uv|.

Installing the Library
-----------------------

With pip
~~~~~~~~
To install AMQCSLdb via |pip|:

.. code-block:: zsh

   pip install "git+https://github.com/FieryIceStickie/amqcsldb-py"

.. tip::
   It's highly recommended to install AMQCSLdb in a virtual environment.

.. _uv_install:

With uv (**Recommended**)
~~~~~~~~~~~~~~~~~~~~~~~~~

Firstly, install |uv|. If you don't have python 3.12+ installed, run

.. code-block:: zsh

   uv python install

After that, ``cd`` to the directory you want the project to sit, and run

.. code-block:: zsh

   uv init amq_scripts
   cd amq_scripts
   uv add "git+https://github.com/FieryIceStickie/amqcsldb-py"


Setting up the script directory
--------------------------------

Now that you have the library installed, you should set up a script directory to store your scripts in.
A CLI tool comes bundled with the library for this purpose. Inside your project, run

.. tab-set::

   .. tab-item:: Normal

      .. code-block:: zsh

         python3 -m amqcsl init scripts
         cd scripts

   .. tab-item:: With uv

      .. code-block:: zsh

         uv run amqcsl init scripts
         cd scripts

It will prompt you for your username and password; this is your amqbot account username and password, not your AMQ account.

Making a script file
--------------------

You can choose to do this manually, but AMQCSLdb provides templates to help you skip the
boilerplate. Make sure you're in the script directory, and run

.. tab-set::

   .. tab-item:: Normal

      .. code-block:: zsh

         python3 -m amqcsl make test.py

   .. tab-item:: With uv

      .. code-block:: zsh

         uv run amqcsl make test.py

Running the script
~~~~~~~~~~~~~~~~~~

There should now be a ``test.py`` file sitting in the scripts directory. To run it:

.. tab-set::

   .. tab-item:: Normal

      .. code-block:: zsh

         python3 test.py

   .. tab-item:: With uv

      .. code-block:: zsh

         uv run test.py

You should see something like::

    [2025-06-02 23:43:35,859|amqcsl.client]:INFO: Retrieving session cookie
    [2025-06-02 23:43:35,860|amqcsl.client]:INFO: Creating client
    [2025-06-02 23:43:35,893|amqcsl.client]:INFO: Verifying permissions
    [2025-06-02 23:43:35,893|amqcsl.client]:INFO: Invalid session cookie, attempting login
    [2025-06-02 23:43:37,833|httpx]:INFO: HTTP Request: POST https://amqbot.082640.xyz/api/login "HTTP/1.1 200 OK"
    [2025-06-02 23:43:37,833|amqcsl.client]:INFO: Writing session_id to amq_session.txt
    [2025-06-02 23:43:38,181|httpx]:INFO: HTTP Request: GET https://amqbot.082640.xyz/api/auth/me "HTTP/1.1 200 OK"
    [2025-06-02 23:43:38,183|amqcsl.client]:INFO: Auth successful
    [2025-06-02 23:43:38,183|__main__]:INFO: shiHib
    [2025-06-02 23:43:38,183|amqcsl.client]:INFO: Closing client

If you run into any issues, double check your username and password in ``scripts/.env``. The next
time you run a script, it should automatically detect the session cookie in ``amq_session.txt`` and
skip the login.

Next Steps
----------

You're all set up! 🎉

Now head over to the :doc:`quickstart` guide to learn how to use the library.
