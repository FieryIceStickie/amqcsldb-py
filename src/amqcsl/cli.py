import os
import shutil
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
    """
    Initialize an empty directory with logs, an env file, and gitignore
    Note: will override existing files if they exist
    """
    dest.mkdir(parents=True, exist_ok=True)
    template_dir = files('amqcsl') / 'templates'
    with as_file(template_dir) as dir:
        shutil.copytree(dir / 'log', dest / 'log', dirs_exist_ok=True)
    with open(dest / '.env', 'w') as file:
        username = input('Enter AMQ bot username (for .env file): ')
        print(f'USERNAME={username}', file=file)
        password = input('Enter AMQ bot password (for .env file): ')
        print(f'PASSWORD={password}', file=file)
        session_path = (
            input('Enter path to file for storing session id (for .env file, defaults to amq_session.txt): ')
            or 'amq_session.txt'
        )
        print(f'SESSION_PATH={session_path}', file=file)
    with open(dest / '.gitignore', 'w') as file:
        for name in (session_path, '.env', 'logs'):
            print(name, file=file)
    os.mkdir(dest / 'logs')

@app.callback()
def callback():
    """
    CLI script for setting up amqcsl script directory
    """
