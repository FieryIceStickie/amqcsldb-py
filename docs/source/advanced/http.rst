HTTP requests
=============

The clients use niquests, with HTTP/2 and HTTP/3 enabled when supported by the server.
Redirects are rejected with ``niquests.HTTPError``. Other HTTP failures also use niquests exceptions.

Ordinary requests use 10-second connect and 30-second read timeouts. Audio uploads use
120 seconds for both phases, giving parallel uploads more time to finish. These are socket
operation timeouts, not a deadline for the whole upload. Uploads are not automatically retried.
Audio uploads stream from disk in bounded chunks. Waiting uploads don't load files into memory.

The ``client`` property exposes a ``niquests.Session`` or ``niquests.AsyncSession``.
Requests made directly through that session use niquests' redirect behavior and bypass the
wrapper's upload timeout policy and concurrency limit.
