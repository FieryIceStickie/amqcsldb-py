import logging
from abc import ABC, abstractmethod
from collections.abc import Iterable, Iterator, Sequence
from functools import cached_property
from typing import TYPE_CHECKING, cast, override

import httpx
import rich.repr
from attrs import Attribute, Converter, field, frozen
from attrs.validators import gt

from amqcsl.exceptions import QueryError
from amqcsl.objects._db_types import CSLArtistSample, CSLGroup, CSLList, CSLSongSample, CSLTrack
from amqcsl.objects._json_types import JSONType, QueryArtist, QuerySong, QueryTrack

from ._core import Items, StreamingBundle, StreamingVendor, httpxClient, materialize

if TYPE_CHECKING:
    from amqcsl import AsyncDBClient, DBClient

logger = logging.getLogger('amqcsl.client')

type RawPage = tuple[int, str, Sequence[JSONType]]


class PageStrategy(ABC):
    """Choose request offsets without interpreting HTTP responses or yielding events."""

    @abstractmethod
    def next_offsets(
        self,
        *,
        skip: int,
        count: int,
        page_size: int,
        batch_size: int,
    ) -> Sequence[int]:
        """Choose the next request round after the last completed page."""
        ...


@frozen
class SyncPageStrategy(PageStrategy):
    @override
    def next_offsets(
        self,
        *,
        skip: int,
        count: int,
        page_size: int,
        batch_size: int,
    ) -> Sequence[int]:
        next_skip = skip + page_size
        return [next_skip] if next_skip < count else []


@frozen
class AsyncPageStrategy(PageStrategy):
    @override
    def next_offsets(
        self,
        *,
        skip: int,
        count: int,
        page_size: int,
        batch_size: int,
    ) -> Sequence[int]:
        return range(skip + batch_size, count, batch_size)


@frozen
class PageBundle[R](StreamingBundle[R], ABC):
    max_batch_size: int = field(validator=gt(0))
    max_query_size: int = field(validator=gt(0))
    batch_size: int = field()
    strategy: PageStrategy

    @batch_size.validator  # type: ignore
    def check(self, _: 'Attribute[int]', value: int) -> None:
        if value <= 0:
            raise QueryError('Batch size must be positive')
        elif value > self.max_batch_size:
            raise QueryError(f'Batch size {value} is larger than the max batch size of {self.max_batch_size}')

    @override
    def vendor(self, client: httpxClient) -> StreamingVendor[R]:
        """Yield HTTP request batches and lazy item events in query order."""
        offsets: Sequence[int] = [0]
        initial_count: int | None = None
        key = ''
        logger.info('Querying first page')
        while offsets:
            responses = yield [self.page_request(client, skip) for skip in offsets]
            if responses is None or isinstance(responses, httpx.Response):
                raise TypeError('Page request batches require a batch of HTTP responses')
            raw_pages = [self.process_response(response) for response in responses]
            if len(raw_pages) != len(offsets):
                raise ValueError('Response count does not match page request count')
            for raw_page in raw_pages:
                count, key, page = raw_page
                if initial_count is None:
                    initial_count = count
                elif count != initial_count:
                    logger.error(f'Count mutated from {initial_count} to {count}')
                yield Items(self.clean_raw_page(raw_page))
            logger.info('Page exhausted')
            count, key, page = raw_pages[-1]
            offsets = self.strategy.next_offsets(
                skip=offsets[-1],
                count=count,
                page_size=len(page),
                batch_size=self.batch_size,
            )
            if offsets:
                logger.info(f'Querying {len(offsets)} more pages')
        logger.info(f'Finished querying {key}')

    def process_response(self, res: httpx.Response) -> RawPage:
        """Validate an HTTP response and query limits, then extract its raw page."""
        res.raise_for_status()
        match res.json():
            case {'count': int(count), **data} if len(data) == 1:
                if count > self.max_query_size:
                    raise QueryError(
                        f'Query returns {count} results, which is larger than the max query size of {self.max_query_size}'
                    )
                key, page = cast(dict[str, JSONType], data).popitem()
                if isinstance(page, list):
                    return count, key, page
            case _:
                pass
        logger.error('Unexpected query response', extra={'response': res.json()})
        raise QueryError('Unexpected query response')

    def clean_raw_page(self, item: RawPage) -> Iterator[R]:
        """Lazily convert a raw page's JSON items into typed results."""
        _count, _key, page = item
        yield from map(self.process_item, page)

    @abstractmethod
    def page_request(self, client: httpxClient, skip: int) -> httpx.Request:
        """Build a request for up to batch_size items, starting at the given offset."""
        ...

    @abstractmethod
    def process_item(self, item: JSONType) -> R:
        """Convert one JSON item from a query response into a typed result."""
        ...

    @abstractmethod
    def __rich_repr__(self) -> rich.repr.Result:
        """Yield the query details used by Rich to display this bundle."""
        ...


def _from_active_list(
    value: bool | None,
    bundle: object,
) -> bool:
    return bool(cast('IterTracksBundle', bundle).active_list) if value is None else value


@frozen
class IterTracksBundle(PageBundle[CSLTrack]):
    search_term: str
    groups: list[CSLGroup] = field(converter=materialize)
    active_list: CSLList | None
    missing_audio: bool
    missing_info: bool
    from_active_list: bool | None = field(converter=Converter(_from_active_list, takes_self=True))

    @classmethod
    def from_client(
        cls,
        client: 'DBClient | AsyncDBClient',
        search_term: str,
        groups: Iterable[CSLGroup] = (),
        active_list: CSLList | None = None,
        missing_audio: bool = False,
        missing_info: bool = False,
        from_active_list: bool | None = None,
        batch_size: int = 100,
    ) -> 'IterTracksBundle':
        return IterTracksBundle(
            search_term=search_term,
            groups=groups,
            active_list=active_list,
            missing_audio=missing_audio,
            missing_info=missing_info,
            from_active_list=from_active_list,
            max_batch_size=client.max_batch_size,
            max_query_size=client.max_query_size,
            batch_size=batch_size,
            strategy=SyncPageStrategy() if client.is_sync() else AsyncPageStrategy(),
        )

    @cached_property
    def body(self) -> QueryTrack:
        body: QueryTrack = {
            'activeListId': getattr(self.active_list, 'id', None),
            'filter': '',
            'groupFilters': [group.id for group in self.groups],
            'orderBy': '',
            'quickFilters': [
                idx
                for idx, val in enumerate(
                    [self.missing_audio, self.missing_info, self.from_active_list],
                    start=1,
                )
                if val
            ],
            'searchTerm': self.search_term,
            'skip': -1,
            'take': -1,
        }
        return body

    @override
    def vendor(self, client: httpxClient) -> StreamingVendor[CSLTrack]:
        logger.info(f'Fetching tracks matching search term "{self.search_term}"')
        return super().vendor(client)

    @override
    def page_request(self, client: httpxClient, skip: int) -> httpx.Request:
        body = self.body
        body['skip'] = skip
        body['take'] = self.batch_size
        return client.build_request('POST', '/api/tracks', json=body)

    @override
    def process_item(self, item: JSONType) -> CSLTrack:
        return CSLTrack.from_json(item)

    @override
    def __rich_repr__(self) -> rich.repr.Result:
        yield 'search_term', self.search_term
        if self.groups:
            yield 'groups', [group.name for group in self.groups]
        if self.active_list:
            yield 'active_list', self.active_list
        flags: list[str] = []
        if self.missing_audio:
            flags.append('missing_audio')
        if self.missing_info:
            flags.append('missing_info')
        if self.from_active_list:
            flags.append('from_active_list')
        yield 'flags', flags, flags
        yield 'batch_size', self.batch_size


@frozen
class IterSongsBundle(PageBundle[CSLSongSample]):
    search_term: str

    @classmethod
    def from_client(
        cls,
        client: 'DBClient | AsyncDBClient',
        search_term: str,
        batch_size: int = 100,
    ) -> 'IterSongsBundle':
        return IterSongsBundle(
            search_term=search_term,
            max_batch_size=client.max_batch_size,
            max_query_size=client.max_query_size,
            batch_size=batch_size,
            strategy=SyncPageStrategy() if client.is_sync() else AsyncPageStrategy(),
        )

    @cached_property
    def params(self) -> QuerySong:
        params: QuerySong = {
            'searchTerm': self.search_term,
            'orderBy': '',
            'filter': '',
            'skip': -1,
            'take': -1,
        }
        return params

    @override
    def vendor(self, client: httpxClient) -> StreamingVendor[CSLSongSample]:
        logger.info(f'Fetching songs matching search term "{self.search_term}"')
        return super().vendor(client)

    @override
    def page_request(self, client: httpxClient, skip: int) -> httpx.Request:
        params = self.params
        params['skip'] = skip
        params['take'] = self.batch_size
        return client.build_request('GET', '/api/songs', params=params)  # type: ignore[reportArgumentType]

    @override
    def process_item(self, item: JSONType) -> CSLSongSample:
        return CSLSongSample.from_json(item)

    @override
    def __rich_repr__(self) -> rich.repr.Result:
        yield 'search_term', self.search_term
        yield 'batch_size', self.batch_size


@frozen
class IterArtistsBundle(PageBundle[CSLArtistSample]):
    search_term: str

    @classmethod
    def from_client(
        cls,
        client: 'DBClient | AsyncDBClient',
        search_term: str,
        batch_size: int = 100,
    ) -> 'IterArtistsBundle':
        return IterArtistsBundle(
            search_term=search_term,
            max_batch_size=client.max_batch_size,
            max_query_size=client.max_query_size,
            batch_size=batch_size,
            strategy=SyncPageStrategy() if client.is_sync() else AsyncPageStrategy(),
        )

    @cached_property
    def params(self) -> QueryArtist:
        params: QueryArtist = {
            'searchTerm': self.search_term,
            'orderBy': '',
            'filter': '',
            'skip': -1,
            'take': -1,
        }
        return params

    @override
    def vendor(self, client: httpxClient) -> StreamingVendor[CSLArtistSample]:
        logger.info(f'Fetching artists matching search term "{self.search_term}"')
        return super().vendor(client)

    @override
    def page_request(self, client: httpxClient, skip: int) -> httpx.Request:
        params = self.params
        params['skip'] = skip
        params['take'] = self.batch_size
        return client.build_request('GET', '/api/artists', params=params)  # type: ignore[reportArgumentType]

    @override
    def process_item(self, item: JSONType) -> CSLArtistSample:
        return CSLArtistSample.from_json(item)

    @override
    def __rich_repr__(self) -> rich.repr.Result:
        yield 'search_term', self.search_term
        yield 'batch_size', self.batch_size
