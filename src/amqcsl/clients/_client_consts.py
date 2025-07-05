import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from attr import Attribute

if TYPE_CHECKING:
    pass

DB_URL = 'https://amqbot.082640.xyz'
DEFAULT_SESSION_PATH = 'amq_session.txt'


logger = logging.getLogger('amqcsl.client')

type Validator[T] = Callable[[object, Attribute[T], T], None]


def is_[T](expected: T) -> Validator[T]:
    def check(self: object, attribute: Attribute[T], value: T) -> None:
        if value is not expected:
            raise ValueError(f"'{attribute.name}' is not {expected}: {value}")

    return check
