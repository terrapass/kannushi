from enum import Enum
from sys import stderr

#
# Types
#

class _AnsiColor(str, Enum):
    DEFAULT = '\033[0m'
    RED     = '\033[31m'
    GREEN   = '\033[32m'
    YELLOW  = '\033[33m'

#
# Globals
#

_is_color_disabled = False

#
# Interface
#

def set_color_disabled(is_color_disabled: bool):
    global _is_color_disabled
    _is_color_disabled = is_color_disabled


def print_success(*args, **kwargs):
    _print_in_color(_AnsiColor.GREEN, *args, **kwargs)

def print_warning(*args, **kwargs):
    _print_in_color(_AnsiColor.YELLOW, *args, **dict({'file' : stderr}, **kwargs))

def print_error(*args, **kwargs):
    _print_in_color(_AnsiColor.RED, *args, **dict({'file' : stderr}, **kwargs))

#
# Service
#

def _print_in_color(color: _AnsiColor, *args, **kwargs):
    print(f"{'' if _is_color_disabled else color.value}{args[0]}{'' if _is_color_disabled else _AnsiColor.DEFAULT.value}", *(args[1:]), **kwargs)
