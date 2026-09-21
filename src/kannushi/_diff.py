import difflib
from pathlib import Path
from enum import Enum
from typing import Callable, Iterable, Iterator
from collections import Counter
from dataclasses import dataclass

from ._rendering import RenderHandler, RenderTemplateContext
from ._logging import print_warning

#
# Constants
#

# Upper bound on difflib's estimated line-matching work for a single file. Past it, lines repeated
# too often are excluded from matching, since difflib is superlinear in this estimate and takes
# hours on large, highly repetitive generated files.
_DEFAULT_DIFF_MATCH_WORK_BUDGET = 5_000_000

_DIFF_CONTEXT_LINES_COUNT = 3

# difflib.SequenceMatcher's own autojunk heuristic, mirrored so the estimate above reflects
# what difflib actually indexes.
_DIFFLIB_AUTOJUNK_MIN_LINES_COUNT = 200
_DIFFLIB_AUTOJUNK_RATIO_DIVISOR   = 100

#
# Types
#

class TargetFileStatus(int, Enum):
    CURRENT  = 0
    MODIFIED = 1
    MISSING  = 2

@dataclass
class TargetDiff:
    status:       TargetFileStatus
    unified_diff: str | None # None when the diff wasn't computed (status-only handler)

class DiffRenderResultObserver:
    def __init__(self, must_collect_unified_diff: bool, must_warn_on_inconsistency: bool):
        self.target_file_statuses: dict[Path, TargetFileStatus] = {}
        self.__diffs:              dict[Path, str]              = {}
        self.__must_collect_unified_diff                        = must_collect_unified_diff
        self.__must_warn_on_inconsistency                       = must_warn_on_inconsistency

    def __call__(self, target_file_path: Path, render_handler_result: TargetDiff):
        self.target_file_statuses[target_file_path] = render_handler_result.status
        if self.__must_collect_unified_diff and render_handler_result.unified_diff:
            self.__diffs[target_file_path] = render_handler_result.unified_diff
        if self.__must_warn_on_inconsistency:
            _warn_on_inconsistency(target_file_path, render_handler_result.status)

    @property
    def unified_diff(self) -> str | None:
        if not self.__must_collect_unified_diff:
            return None
        return ''.join(self.__diffs[target_file_path] for target_file_path in sorted(self.__diffs))

#
# Interface
#

def make_diff_render_pipeline_step(must_collect_unified_diff: bool, must_warn_on_inconsistency: bool) -> tuple[RenderHandler, DiffRenderResultObserver]:
    handler = _diff_render_handler if must_collect_unified_diff else _status_only_diff_render_handler
    return (handler, DiffRenderResultObserver(must_collect_unified_diff, must_warn_on_inconsistency))

#
# Service
#

def _diff_render_handler(context: RenderTemplateContext) -> TargetDiff:
    target_file_status = _determine_target_file_status(context)
    if target_file_status == TargetFileStatus.CURRENT:
        return TargetDiff(target_file_status, '')
    return TargetDiff(target_file_status, _make_unified_diff(context.target_current_content, context.rendered_content, context.target_file_relative_path))

def _status_only_diff_render_handler(context: RenderTemplateContext) -> TargetDiff:
    return TargetDiff(_determine_target_file_status(context), None)

def _determine_target_file_status(context: RenderTemplateContext) -> TargetFileStatus:
    if context.target_current_content is None:
        return TargetFileStatus.MISSING
    return TargetFileStatus.CURRENT if context.target_current_content == context.rendered_content else TargetFileStatus.MODIFIED

def _make_unified_diff(current_content: str | None, rendered_content: str, target_file_relative_path: str) -> str:
    from_lines = (current_content or '').splitlines(keepends=True)
    to_lines   = rendered_content.splitlines(keepends=True)
    from_file  = '/dev/null' if current_content is None else f'a/{target_file_relative_path}'
    to_file    = f'b/{target_file_relative_path}'
    is_junk    = _try_make_repeated_line_junk_predicate(from_lines, to_lines)
    return ''.join(_with_no_newline_markers(_unified_diff(from_lines, to_lines, from_file, to_file, is_junk)))

def _try_make_repeated_line_junk_predicate(from_lines: list[str], to_lines: list[str]) -> Callable[[str], bool] | None:
    to_line_counts = Counter(to_lines)
    threshold      = _try_select_repeated_line_threshold(Counter(from_lines), to_line_counts, len(to_lines))
    if threshold is None:
        return None
    return lambda line: to_line_counts[line] > threshold

def _try_select_repeated_line_threshold(from_line_counts: Counter[str], to_line_counts: Counter[str], to_lines_count: int) -> int | None:
    """Returns the largest number of repetitions to still match on, or None if no line needs excluding."""
    autojunk_cutoff = to_lines_count // _DIFFLIB_AUTOJUNK_RATIO_DIVISOR if to_lines_count >= _DIFFLIB_AUTOJUNK_MIN_LINES_COUNT else to_lines_count
    match_work_by_repeats: dict[int, int] = {}
    for line, to_count in to_line_counts.items():
        if to_count > autojunk_cutoff:
            continue
        if (match_work := from_line_counts[line] * to_count) > 0:
            match_work_by_repeats[to_count] = match_work_by_repeats.get(to_count, 0) + match_work

    if sum(match_work_by_repeats.values()) <= _DEFAULT_DIFF_MATCH_WORK_BUDGET:
        return None

    selected_threshold   = 0
    selected_match_work  = 0
    for repeats in sorted(match_work_by_repeats):
        if selected_match_work + match_work_by_repeats[repeats] > _DEFAULT_DIFF_MATCH_WORK_BUDGET:
            break
        selected_threshold   = repeats
        selected_match_work += match_work_by_repeats[repeats]

    # Never exclude unique lines: that would leave difflib nothing to anchor on.
    return max(selected_threshold, 1)

def _unified_diff(from_lines: list[str], to_lines: list[str], from_file: str, to_file: str, is_junk: Callable[[str], bool] | None) -> Iterator[str]:
    """Mirrors difflib.unified_diff, which hardcodes SequenceMatcher(None, ...) and exposes no isjunk hook."""
    matcher = difflib.SequenceMatcher(is_junk, from_lines, to_lines)
    started = False
    for group in matcher.get_grouped_opcodes(_DIFF_CONTEXT_LINES_COUNT):
        if not started:
            started = True
            yield f'--- {from_file}\n'
            yield f'+++ {to_file}\n'

        first, last = group[0], group[-1]
        from_range  = _format_unified_diff_range(first[1], last[2])
        to_range    = _format_unified_diff_range(first[3], last[4])
        yield f'@@ -{from_range} +{to_range} @@\n'

        for tag, from_start, from_stop, to_start, to_stop in group:
            if tag == 'equal':
                yield from (' ' + line for line in from_lines[from_start:from_stop])
                continue
            if tag in ('replace', 'delete'):
                yield from ('-' + line for line in from_lines[from_start:from_stop])
            if tag in ('replace', 'insert'):
                yield from ('+' + line for line in to_lines[to_start:to_stop])

def _format_unified_diff_range(start: int, stop: int) -> str:
    beginning = start + 1 # lines are numbered from one
    length    = stop - start
    if length == 1:
        return str(beginning)
    if not length:
        beginning -= 1 # empty ranges begin at the line just before the range
    return f'{beginning},{length}'

def _with_no_newline_markers(diff_lines: Iterable[str]) -> Iterator[str]:
    for diff_line in diff_lines:
        yield diff_line
        if diff_line and diff_line[0] in ' +-' and not diff_line.endswith('\n'):
            yield '\n\\ No newline at end of file\n'

def _warn_on_inconsistency(target_file_path: Path, status: TargetFileStatus):
    if status == TargetFileStatus.MODIFIED:
        print_warning(f"\twarning: {target_file_path} contains manual modifications or is out of date")
    elif status == TargetFileStatus.MISSING:
        print_warning(f"\twarning: {target_file_path} is missing")
