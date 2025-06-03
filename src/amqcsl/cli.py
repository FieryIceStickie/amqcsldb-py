import os
import shutil
from enum import StrEnum
from importlib.resources import as_file, files
from pathlib import Path
from typing import Annotated

import typer

app = typer.Typer(no_args_is_help=True)


@app.command()
def init(
    dest: Annotated[
        Path,
        typer.Argument(
            default_factory=os.getcwd,
            help='Destination path',
            show_default='Current directory',
        ),
    ],
):
    """Initialize an empty directory with logs, an env file, and gitignore
    Note: will override existing files if they exist

    Args:
        dest: Path to script directory, defaults to cwd
    """
    dest.mkdir(parents=True, exist_ok=True)
    template_dir = files('amqcsl') / 'templates'
    with as_file(template_dir) as dir:
        shutil.copytree(dir / 'log', dest / 'log', dirs_exist_ok=True)
    with open(dest / '.env', 'w') as file:
        username = input('Enter AMQ bot username (for .env file): ')
        print(f'AMQ_USERNAME="{username}"', file=file)
        password = input('Enter AMQ bot password (for .env file): ')
        print(f'AMQ_PASSWORD="{password}"', file=file)
        session_path = (
            input(
                'Enter path to file for storing session id (for .env file, defaults to amq_session.txt, press Enter to skip): '
            )
            or 'amq_session.txt'
        )
        print(f'SESSION_PATH="{session_path}"', file=file)
    with open(dest / '.gitignore', 'w') as file:
        for name in (session_path, '.env', 'logs'):
            print(name, file=file)
    os.makedirs(dest / 'logs', exist_ok=True)


class Templates(StrEnum):
    simple = 'simple'
    character = 'character'
    character_compact = 'character_compact'


@app.command()
def make(
    dest: Annotated[str, typer.Argument(help='Name of the file')],
    template: Annotated[
        Templates,
        typer.Option(
            '--template',
            '-t',
            help='Template to choose from',
            case_sensitive=False,
        ),
    ] = Templates.simple,
):
    """Create a script file from a template

    Args:
        dest: Path to the file
        template: The template to use for the file, defaults to simple

    Raises:
        FileExistsError: File already exists
    """
    if (Path.cwd() / dest).exists():
        raise FileExistsError(Path.cwd() / dest)
    template_file = files('amqcsl') / f'templates/scripts/{template}.py.txt'
    with as_file(template_file) as file:
        shutil.copy(file, dest)


@app.callback()
def callback():
    """
    CLI script for setting up amqcsl script directory
    """
