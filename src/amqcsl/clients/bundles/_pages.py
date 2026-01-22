import logging
from abc import ABC, abstractmethod
from collections.abc import Generator, Iterator, Sequence
from functools import cached_property
from typing import TYPE_CHECKING, Generic, Iterable, TypeVar, overload, override

import httpx
import rich.repr
from attrs import Attribute, Converter, define, field, frozen
from attrs.validators import deep_iterable, gt, instance_of

from amqcsl.exceptions import QueryError
from amqcsl.objects._db_types import CSLArtistSample, CSLGroup, CSLList, CSLSongSample, CSLTrack
from amqcsl.objects._json_types import JSONType, QueryArtist, QuerySong, QueryTrack

from ._core import httpxClient

if TYPE_CHECKING:
    from amqcsl import AsyncDBClient, DBClient

logger = logging.getLogger('amqcsl.client')

type RawPage = tuple[int, str, Sequence[JSONType]]


type PageSingleVendor = Generator[httpx.Request, RawPage, None]
type PageMultiVendor = Generator[Iterable[httpx.Request], Iterable[RawPage], None]
type PageVendor = PageSingleVendor | PageMultiVendor


@frozen
class PageBundle[R, Vd: PageVendor](ABC):
    max_batch_size: int = field(validator=[instance_of(int), gt(0)])
    max_query_size: int = field(validator=[instance_of(int), gt(0)])
    batch_size: int = field()
    strategy: 'PageStrategy[R, Vd]' = field()

    @batch_size.validator  # type: ignore
    def check(self, _: 'Attribute[int]', value: int) -> None:
        if not isinstance(value, int):  # type: ignore[reportUnnecessaryComparison]
            raise QueryError('Batch size must be an integer')
        elif value <= 0:
            raise QueryError('Batch size must be positive')
        elif value > self.max_batch_size:
            raise QueryError(f'Batch size {value} is larger than the max batch size of {self.max_batch_size}')

    def vendor(self, client: httpxClient) -> Vd:
        return self.strategy.vendor(self, client)

    def process_response(self, res: httpx.Response) -> RawPage:
        return self.strategy.process(self, res)

    def clean_raw_page(self, item: RawPage) -> Iterator[R]:
        count, key, page = item
        yield from map(self.process_item, page)

    @abstractmethod
    def page_request(self, client: httpxClient, skip: int) -> httpx.Request: ...

    @abstractmethod
    def process_item(self, item: JSONType) -> R: ...

    @abstractmethod
    def __rich_repr__(self) -> rich.repr.Result: ...


R = TypeVar('R')
Vd = TypeVar('Vd', bound=PageVendor, covariant=True)


class PageStrategy(ABC, Generic[R, Vd]):
    _count: int | None = None

    @abstractmethod
    def vendor(self, bundle: PageBundle[R, Vd], client: httpxClient) -> Vd: ...

    def process(self, bundle: PageBundle[R, Vd], res: httpx.Response) -> RawPage:
        res.raise_for_status()
        match res.json():
            case {
                'count': int(count),
                **data,
            }:
                if self._count is None:
                    if count > bundle.max_query_size:
                        raise QueryError(
                            f'Query returns {count} results, which is larger than the max query size of {bundle.max_query_size}'
                        )
                    self._count = count
                elif count != self._count:
                    logger.error(f'Count mutated from {self._count} to {count}')
            case _:
                logger.error('Unexpected query response', extra={'response': res.json()})
                raise QueryError('Unexpected query response')
        key, item = data.popitem()
        return count, key, item


@define
class SyncPageStrategy[R](PageStrategy[R, PageSingleVendor], ABC):
    @override
    def vendor(self, bundle: PageBundle[R, PageSingleVendor], client: httpxClient) -> PageSingleVendor:
        skip = 0
        self._count = None
        while True:
            count, key, page = yield bundle.page_request(client, skip)
            skip += len(page)
            logger.info('Page exhausted')
            if skip >= count:
                break
            logger.info('Querying next page')
        logger.info(f'Finished querying {key}')


@define
class AsyncPageStrategy[R](PageStrategy[R, PageMultiVendor], ABC):
    @override
    def vendor(self, bundle: PageBundle[R, PageMultiVendor], client: httpxClient) -> PageMultiVendor:
        logger.info('Querying first page')
        ((count, key, page),) = yield [bundle.page_request(client, 0)]
        reqs = [
            bundle.page_request(client, skip)  #
            for skip in range(bundle.batch_size, count, bundle.batch_size)
        ]
        logger.info(f'Querying {len(reqs)} more pages')
        yield reqs


@frozen
class IterTracksBundle(PageBundle[CSLTrack, Vd], Generic[Vd]):
    search_term: str = field(validator=instance_of(str))
    groups: Iterable[CSLGroup] = field(validator=deep_iterable(instance_of(CSLGroup)))
    active_list: CSLList | None = field(validator=instance_of((CSLList, type(None))))
    missing_audio: bool = field(validator=instance_of(bool))
    missing_info: bool = field(validator=instance_of(bool))
    from_active_list: bool | None = field(
        validator=instance_of(bool),
        converter=Converter(
            lambda value, self_: bool(self_.active_list) if value is None else value,  # type: ignore
            takes_self=True,
        ),
    )

    @overload
    @classmethod
    def from_client(
        cls,
        client: 'DBClient',
        search_term: str,
        groups: Iterable[CSLGroup] = (),
        active_list: CSLList | None = None,
        missing_audio: bool = False,
        missing_info: bool = False,
        from_active_list: bool | None = None,
        batch_size: int = 100,
    ) -> 'IterTracksBundle[PageSingleVendor]': ...
    @overload
    @classmethod
    def from_client(
        cls,
        client: 'AsyncDBClient',
        search_term: str,
        groups: Iterable[CSLGroup] = (),
        active_list: CSLList | None = None,
        missing_audio: bool = False,
        missing_info: bool = False,
        from_active_list: bool | None = None,
        batch_size: int = 100,
    ) -> 'IterTracksBundle[PageMultiVendor]': ...

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
    ) -> 'IterTracksBundle[PageVendor]':
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
    def vendor(self, client: httpxClient) -> Vd:
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
class IterSongsBundle(PageBundle[CSLSongSample, Vd], Generic[Vd]):
    search_term: str = field(validator=instance_of(str))

    @overload
    @classmethod
    def from_client(
        cls,
        client: 'DBClient',
        search_term: str,
        batch_size: int = 100,
    ) -> 'IterSongsBundle[PageSingleVendor]': ...
    @overload
    @classmethod
    def from_client(
        cls,
        client: 'AsyncDBClient',
        search_term: str,
        batch_size: int = 100,
    ) -> 'IterSongsBundle[PageMultiVendor]': ...
    @classmethod
    def from_client(
        cls,
        client: 'DBClient | AsyncDBClient',
        search_term: str,
        batch_size: int = 100,
    ) -> 'IterSongsBundle[PageVendor]':
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
    def vendor(self, client: httpxClient) -> Vd:
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
class IterArtistsBundle(PageBundle[CSLArtistSample, Vd], Generic[Vd]):
    search_term: str = field(validator=instance_of(str))

    @overload
    @classmethod
    def from_client(
        cls,
        client: 'DBClient',
        search_term: str,
        batch_size: int = 100,
    ) -> 'IterArtistsBundle[PageSingleVendor]': ...
    @overload
    @classmethod
    def from_client(
        cls,
        client: 'AsyncDBClient',
        search_term: str,
        batch_size: int = 100,
    ) -> 'IterArtistsBundle[PageMultiVendor]': ...
    @classmethod
    def from_client(
        cls,
        client: 'DBClient | AsyncDBClient',
        search_term: str,
        batch_size: int = 100,
    ) -> 'IterArtistsBundle[PageVendor]':
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
    def vendor(self, client: httpxClient) -> Vd:
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
