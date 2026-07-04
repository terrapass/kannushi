from enum import Enum
from sys import stderr
from os import get_terminal_size

#
# Constants
#

_STATUS_LINE_FALLBACK_MAX_LENGTH = 79

#
# Types
#

class _AnsiColor(str, Enum):
    DEFAULT = '\033[0m'
    DIM     = '\033[2m'
    RED     = '\033[31m'
    GREEN   = '\033[32m'
    YELLOW  = '\033[33m'
    BLUE    = '\033[94m'

#
# Globals
#

_is_color_disabled                      = False
_is_verbose                             = False
_status_line_text:           str | None = None
_status_line_printed_length: int        = 0

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

def is_status_line_active() -> bool:
    return _status_line_text is not None

def try_activate_status_line(text: str):
    global _status_line_text
    if not _is_status_line_available():
        return
    _print_status_line(text)
    _status_line_text = text # published only after the initial print, so try_update_status_line() callers cannot print concurrently with this thread

def try_update_status_line(text: str):
    global _status_line_text
    if _status_line_text is None: # not activated (or the status line is unavailable) - see try_activate_status_line()
        return
    _status_line_text = text
    _print_status_line(text)

def try_flush_status_line():
    global _status_line_text, _status_line_printed_length
    if _status_line_text is None:
        return
    _print_status_line(_status_line_text)
    stderr.write('\n')
    stderr.flush()
    _status_line_text           = None
    _status_line_printed_length = 0


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
    status_line_text = _status_line_text
    if status_line_text is not None:
        _erase_status_line()
        kwargs.setdefault('flush', True) # makes sure the message reaches the terminal before the status line is redrawn on stderr
    _print_in_color_impl(color, *args, **kwargs)
    if status_line_text is not None:
        _print_status_line(status_line_text)

def _print_in_color_impl(color: _AnsiColor, *args, **kwargs):
    print(f"{'' if _is_color_disabled else color.value}{args[0]}{'' if _is_color_disabled else _AnsiColor.DEFAULT.value}", *(args[1:]), **kwargs)

def _is_status_line_available() -> bool:
    return not _is_verbose and stderr.isatty()

def _print_status_line(text: str):
    global _status_line_printed_length
    max_length = _get_status_line_max_length()
    text       = text[:max_length]
    stderr.write('\r' + text.ljust(min(_status_line_printed_length, max_length)))
    stderr.flush()
    _status_line_printed_length = len(text)

def _erase_status_line():
    stderr.write('\r' + ' ' * _status_line_printed_length + '\r')
    stderr.flush()

def _get_status_line_max_length() -> int:
    # truncating to columns - 1 prevents auto-wrap, which would break \r-based overwriting.
    try:
        return max(get_terminal_size(stderr.fileno()).columns - 1, 1)
    except (OSError, ValueError):
        return _STATUS_LINE_FALLBACK_MAX_LENGTH
