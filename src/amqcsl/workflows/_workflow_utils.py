from typing import Any
from rich.pretty import pprint
from amqcsl.exceptions import QuitError


def prompt(
    *objs: Any,
    msg: str = 'Accept?',
    pretty: bool = True,
    continue_on_empty: bool = False,
    default: bool = True,
    **kwargs: Any,
) -> bool:
    """Prompt the user for a Yes or No answer, or to quit the script

    Args:
        objs: Objects to print
        msg: Message to prompt user with, defaults to 'Accept?'
        pretty: Whether to pretty print with rich.pretty.pprint
        continue_on_empty: Whether to exit input loop on empty input
        default: Default to pass if continue_on_empty is True and empty is passed
        kwargs: Kwargs to pass to the print function

    Returns:
        User's choice as a boolean

    Raises:
        QuitError: If the user chooses to quit
    """
    print_func = pprint if pretty else print
    for obj in objs:
        print_func(obj, **kwargs)
    while True:
        inp = input(f'{msg} Y(es) N(o) Q(uit): ').lower().strip()
        if continue_on_empty and not inp:
            return default
        match inp:
            case 'y' | 'yes':
                return True
            case 'n' | 'no':
                return False
            case 'q' | 'quit':
                raise QuitError
            case _:
                continue
