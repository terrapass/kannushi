import difflib
from pathlib import Path
from enum import Enum
from typing import Iterable, Iterator
from dataclasses import dataclass

from ._rendering import RenderHandler, RenderTemplateContext
from ._logging import print_warning

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
    return ''.join(_with_no_newline_markers(difflib.unified_diff(from_lines, to_lines, from_file, to_file, lineterm='\n')))

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
