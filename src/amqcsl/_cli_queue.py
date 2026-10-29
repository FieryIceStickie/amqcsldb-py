"""Load saved queues for terminal review and execution."""

import asyncio
import json
import os
from pathlib import Path
from typing import Annotated, Any, cast

import typer
from dotenv import load_dotenv
from rich.console import Console

from amqcsl import AsyncDBClient
from amqcsl.queues import GroupedQueue, Queue, StandardQueue

app = typer.Typer(no_args_is_help=True, help='Display and run saved queues.')


def _load_queue(path: Path) -> Queue[Any]:
    """Restore a saved queue using the concrete type recorded in its JSON.

    Args:
        path: Queue file to read without modifying it.

    Returns:
        A standard or grouped queue containing the saved work.

    Raises:
        BadParameter: The file cannot be read or restored as a supported queue.
    """
    try:
        with path.open(encoding='utf-8') as stream:
            data: object = json.load(stream)
        if not isinstance(data, dict):
            raise TypeError('Expected a queue JSON object')
        document = cast(dict[str, Any], data)
        match document.get('queue_type'):
            case StandardQueue.queue_type:
                return StandardQueue.from_json(document)
            case GroupedQueue.queue_type:
                return GroupedQueue.from_json(document)
            case unknown:
                raise ValueError(f'Unsupported queue type: {unknown!r}')
    except (OSError, ValueError, TypeError, ExceptionGroup) as error:
        raise typer.BadParameter(f'Cannot load queue: {error}', param_hint='path') from error


@app.command()
def display(
    path: Annotated[
        Path,
        typer.Argument(exists=True, file_okay=True, dir_okay=False, readable=True, help='Saved queue JSON file'),
    ],
) -> None:
    """Display a saved queue on stdout without contacting the database.

    Args:
        path: Saved queue JSON file.
    """
    Console().print(_load_queue(path).display())


@app.command()
def run(
    path: Annotated[
        Path,
        typer.Argument(exists=True, file_okay=True, dir_okay=False, readable=True, help='Saved queue JSON file'),
    ],
) -> None:
    """Run a saved queue and write remaining work as JSON to stdout.

    Read AMQ_USERNAME, AMQ_PASSWORD, and SESSION_PATH from the environment or
    the current directory's .env file. Leave the input queue file unchanged.

    Args:
        path: Saved queue JSON file.
    """
    queue = _load_queue(path)
    load_dotenv(Path.cwd() / '.env')
    asyncio.run(_run_queue(queue))


async def _run_queue(queue: Queue[Any]) -> None:
    """Process a restored queue with async workers and write recovery JSON to stdout.

    Args:
        queue: Pending work to process using the environment's client settings.
    """
    session_path = os.getenv('SESSION_PATH')
    if not session_path:
        raise typer.BadParameter('SESSION_PATH is required; run amqcsl init to configure it')
    async with AsyncDBClient(
        username=os.getenv('AMQ_USERNAME'),
        password=os.getenv('AMQ_PASSWORD'),
        session_path=Path(session_path),
    ) as client:
        await client.process_queue(queue)
