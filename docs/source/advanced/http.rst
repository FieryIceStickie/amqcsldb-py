HTTP requests
=============

The clients use niquests to make requests to the database. You don't need to manage the HTTP session
when using the library's methods, but these are the defaults to keep in mind when running your scripts:

* HTTP/2 and HTTP/3 are enabled when supported by the server, so requests can use those protocols
  without any extra setup in your script.
* Redirects are rejected with ``niquests.HTTPError`` rather than followed automatically. Other
  unsuccessful HTTP responses also raise niquests exceptions.
* Ordinary requests have a 10-second connect timeout and a 30-second read timeout. These limit how
  long a request can wait to connect or receive data, rather than setting a deadline for the entire
  operation.
* Audio uploads use 120 seconds for both timeouts, giving parallel uploads more time to finish.
  Files are streamed from disk in bounded chunks, so waiting uploads don't load the whole file into
  memory, and failed uploads aren't automatically retried.
* Async requests share a concurrency limit, controlled by ``client.max_request_count``. If you're
  processing lots of tracks at once, see :doc:`async` for how that limit works.

If you need to use the underlying session directly, it's available through
:py:attr:`~amqcsl.DBClient.client` or :py:attr:`~amqcsl.AsyncDBClient.client`:

.. code-block:: python

    session = client.client

This gives you a ``niquests.Session`` or ``niquests.AsyncSession``. Requests made through it directly
use niquests' redirect behavior and bypass the library's upload timeouts and concurrency limit, so
use the library's methods for ordinary database operations.
