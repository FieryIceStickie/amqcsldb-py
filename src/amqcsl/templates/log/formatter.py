import datetime as dt
import json
import logging
from typing import override

from attrs import define, field

LOG_RECORD_BUILTIN_ATTRS = {
    'args',
    'asctime',
    'created',
    'exc_info',
    'exc_text',
    'filename',
    'funcName',
    'levelname',
    'levelno',
    'lineno',
    'module',
    'msecs',
    'message',
    'msg',
    'name',
    'pathname',
    'process',
    'processName',
    'relativeCreated',
    'stack_info',
    'thread',
    'threadName',
    'taskName',
}


@define
class JSONFormatter(logging.Formatter):
    """
    Log to JSON lines file
    Taken from mCoding at https://youtu.be/9L77QExPmI0?si=dHSRmCNereKSJgxn
    """

    fmt_keys: dict[str, str] = field(factory=dict)

    @override
    def format(self, record: logging.LogRecord) -> str:
        message = self._prepare_log_dict(record)
        return json.dumps(message, default=str)

    def _prepare_log_dict(self, record: logging.LogRecord):
        log = {
            'message': record.getMessage(),
            'timestamp': dt.datetime.fromtimestamp(
                record.created, tz=dt.timezone.utc
            ).isoformat(),
        }
        if record.exc_info is not None:
            log['exc_info'] = self.formatException(record.exc_info)
        if record.stack_info is not None:
            log['stack_info'] = self.formatStack(record.stack_info)

        log |= {
            k: getattr(record, v)
            for k, v in self.fmt_keys.items()
            if k not in log
        }
        log |= {
            k: v
            for k, v in record.__dict__.items()
            if k not in LOG_RECORD_BUILTIN_ATTRS
        }
        return log
