import asyncio
from collections.abc import AsyncIterator, Generator, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, TypedDict
from uuid import uuid4

import niquests
from niquests.typing import HttpMethodType, QueryParameterType


class MultipartUpload:
    """Stream a single audio file without buffering it during request preparation."""

    chunk_size = 64 * 1024

    def __init__(self, path: Path, mime_type: str) -> None:
        self.path = path
        boundary = uuid4().hex
        filename = path.name.replace('\r', '%0D').replace('\n', '%0A').replace('"', '%22')
        self.content_type = f'multipart/form-data; boundary={boundary}'
        self._prefix = (
            f'--{boundary}\r\n'
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f'Content-Type: {mime_type}\r\n\r\n'
        ).encode()
        self._suffix = f'\r\n--{boundary}--\r\n'.encode('ascii')
        self._size = path.stat().st_size
        self._file: BinaryIO | None = None

    @property
    def closed(self) -> bool:
        """Whether this upload currently has no open file handle."""
        return self._file is None

    def __len__(self) -> int:
        return len(self._prefix) + self._size + len(self._suffix)

    @contextmanager
    def opened(self) -> Generator[None]:
        """Keep the file open only while its request occupies a transport slot."""
        with self.path.open('rb') as file:
            self._file = file
            try:
                yield
            finally:
                self._file = None

    def __iter__(self) -> Iterator[bytes]:
        """Read bounded chunks for the synchronous transport."""
        assert self._file is not None
        yield self._prefix
        while chunk := self._file.read(self.chunk_size):
            yield chunk
        yield self._suffix


class AsyncMultipartUpload(MultipartUpload):
    """Expose asynchronous file chunks exclusively to the async transport."""

    async def __aiter__(self) -> AsyncIterator[bytes]:
        """Read off the event loop and finish pending reads before closing the file."""
        assert self._file is not None
        yield self._prefix
        while True:
            read = asyncio.create_task(asyncio.to_thread(self._file.read, self.chunk_size))
            try:
                chunk = await asyncio.shield(read)
            except asyncio.CancelledError:
                await read
                raise
            if not chunk:
                break
            yield chunk
        yield self._suffix


def build_request(
    client: niquests.Session | niquests.AsyncSession,
    method: HttpMethodType,
    url: str,
    *,
    params: QueryParameterType | None = None,
    json: object = None,
    upload: MultipartUpload | None = None,
) -> niquests.PreparedRequest:
    """Prepare a request using the session's base URL, headers, and cookies."""
    headers = {'Content-Type': upload.content_type} if upload is not None else None
    return client.prepare_request(niquests.Request(method, url, params=params, json=json, data=upload, headers=headers))


class RequestOptions(TypedDict):
    """Transport keyword arguments shared by synchronous and asynchronous sends."""

    timeout: tuple[int, int]
    allow_redirects: bool


def request_options(request: niquests.PreparedRequest) -> RequestOptions:
    """Give multipart uploads longer socket timeouts without enabling retries."""
    upload = str((request.headers or {}).get('Content-Type', '')).startswith('multipart/form-data')
    return {'timeout': (120, 120) if upload else (10, 30), 'allow_redirects': False}


def reject_redirect(response: niquests.Response) -> None:
    """Preserve HTTPX's rejection of redirect responses without following them."""
    if response.status_code is not None and 300 <= response.status_code < 400:
        raise niquests.exceptions.HTTPError(
            f'Unexpected redirect: {response.status_code} for {response.url}',
            response=response,
            request=response.request,
        )
