import logging
from abc import ABC, abstractmethod
from typing import Iterable, override

import httpx
from attrs import Attribute, field, frozen
from attrs.validators import deep_iterable, gt, instance_of

from amqcsl.exceptions import QueryError
from amqcsl.objects._db_types import CSLGroup, CSLList, CSLTrack
from amqcsl.objects._json_types import JSONType

from .core import Bundle, SingleVendor, httpxClient, MultiVendor

logger = logging.getLogger('amqcsl.client')


@frozen
class PageBundle[R](Bundle[Iterable[R]], ABC):
    max_batch_size: int = field(validator=[instance_of(int), gt(0)])
    max_query_size: int = field(validator=[instance_of(int), gt(0)])
    batch_size: int = field()

    @batch_size.validator  # type: ignore
    def check(self, attribute: 'Attribute[int]', value: int) -> None:
        if value <= 0:
            raise QueryError('Batch size must be positive')
        elif value > self.max_batch_size:
            raise QueryError(f'Batch size {value} is larger than the max batch size of {self.max_batch_size}')

    @abstractmethod
    def page_request(self, skip: int, take: int) -> httpx.Request: ...

    @abstractmethod
    def process(self, item: JSONType) -> R: ...


@frozen
class SyncPageBundle[R](PageBundle[R], ABC):
    @override
    def vendor(self, client: httpxClient) -> SingleVendor[Iterable[R]]:
        prev_count = None
        skip = 0
        items: list[R] = []
        while True:
            res = yield self.page_request(skip, self.batch_size)
            res.raise_for_status()
            match res.json():
                case {
                    'count': int(count),
                    **data,
                }:
                    if prev_count is None:
                        if count > self.max_query_size:
                            raise QueryError(
                                f'Query returns {count} results, which is larger than the max query size of {self.max_query_size}'
                            )
                    elif count != prev_count:
                        logger.error(f'Count mutated from {prev_count} to {count}')
                case _:
                    logger.error('Unexpected query response', extra={'response': res.json()})
                    raise QueryError('Unexpected query response')
            key, item = data.popitem()
            skip += len(data[key])
            items.append(item)
            logger.info('Page exhausted')
            if skip >= count:
                break
            prev_count = count
            logger.info('Querying next page')
        logger.info(f'Finished querying {key}')
        return items


class AsyncPageBundle[R](PageBundle[R], ABC):
    @override
    def vendor(self, client: httpxClient) -> MultiVendor[Iterable[R]]:
        logger.info('Querying first page')
        (res,) = yield [self.page_request(0, self.batch_size)]
        res.raise_for_status()
        match res.json():
            case {
                'count': int(count),
                **data,
            }:
                key, page = data.popitem()
            case _:
                logger.error('Unexpected query response', extra={'response': res.json()})
                raise QueryError('Unexpected query response')
        reqs = [
            self.page_request(skip, self.batch_size)  #
            for skip in range(self.batch_size, count, self.batch_size)
        ]
        logger.info(f'Querying {len(reqs)} more pages')
        resps = yield reqs
        pages = [page]
        for res in resps:
            data = res.json()
            if data['count'] != count:
                logger.error(f'Count mutated from {data["count"]} to {count}')
            pages.append(data[key])
        items = [self.process(item) for page in pages for item in page]
        return items


@frozen
class IterTracksBundle(SyncPageBundle[CSLTrack]):
    search_term: str = field(validator=instance_of(str))
    groups: Iterable[CSLGroup] = field(validator=deep_iterable(instance_of(CSLGroup)))
    active_list: CSLList | None = field(validator=instance_of((CSLList, type(None))))
    missing_audio: bool = field(validator=instance_of(bool))
    missing_info: bool = field(validator=instance_of(bool))
    from_active_list: bool = field(validator=instance_of(bool))

    @override
    def page_request(self, skip: int, take: int) -> httpx.Request:
        return []

    @override
    def process(self, item: JSONType) -> CSLTrack:
        return CSLTrack.from_json(item)
