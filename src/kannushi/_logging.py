from enum import Enum
from sys import stderr

#
# Types
#

class _AnsiColor(str, Enum):
    DEFAULT = '\033[0m'
    DIM     = '\033[2m'
    RED     = '\033[31m'
    GREEN   = '\033[32m'
    YELLOW  = '\033[33m'
    BLUE    = '\033[34m'

#
# Globals
#

_is_color_disabled = False
_is_verbose        = False

#
# Interface
#

def set_color_disabled(is_color_disabled: bool):
    global _is_color_disabled
    _is_color_disabled = is_color_disabled

def set_verbose(verbose: bool):
    global _is_verbose
    _is_verbose = verbose

def is_verbose() -> bool:
    return _is_verbose


def print_success(*args, **kwargs):
    _print_in_color(_AnsiColor.GREEN, *args, **kwargs)

def print_verbose_success(*args, **kwargs):
    if _is_verbose:
        _print_in_color(_AnsiColor.BLUE, *args, **kwargs)

def print_verbose(*args, **kwargs):
    if _is_verbose:
        _print_in_color(_AnsiColor.DIM, *args, **kwargs)

def print_warning(*args, **kwargs):
    _print_in_color(_AnsiColor.YELLOW, *args, **dict({'file' : stderr}, **kwargs))

def print_error(*args, **kwargs):
    _print_in_color(_AnsiColor.RED, *args, **dict({'file' : stderr}, **kwargs))

#
# Service
#

def _print_in_color(color: _AnsiColor, *args, **kwargs):
    print(f"{'' if _is_color_disabled else color.value}{args[0]}{'' if _is_color_disabled else _AnsiColor.DEFAULT.value}", *(args[1:]), **kwargs)
