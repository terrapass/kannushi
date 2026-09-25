from pathlib import Path
from enum import Enum
from dataclasses import dataclass

from .._rendering import RenderHandler, RenderTemplateContext
from .._logging import print_warning

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
    unified_diff: str | None # None when the handler didn't compute the diff (status-only, or staged for git)

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
    if not must_collect_unified_diff:
        return (status_only_diff_render_handler, DiffRenderResultObserver(False, must_warn_on_inconsistency))

    # Imported here, since the backends themselves depend on this module.
    from .git_backend import StagingDiffRenderHandler, GitDiffRenderResultObserver, try_make_git_diff_staging
    from .difflib_backend import difflib_diff_render_handler

    git_diff_staging = try_make_git_diff_staging()
    if git_diff_staging is None:
        return (difflib_diff_render_handler, DiffRenderResultObserver(True, must_warn_on_inconsistency))
    return (StagingDiffRenderHandler(git_diff_staging.root_path), GitDiffRenderResultObserver(git_diff_staging, must_warn_on_inconsistency))

def status_only_diff_render_handler(context: RenderTemplateContext) -> TargetDiff:
    return TargetDiff(determine_target_file_status(context), None)

def determine_target_file_status(context: RenderTemplateContext) -> TargetFileStatus:
    if context.target_current_content is None:
        return TargetFileStatus.MISSING
    return TargetFileStatus.CURRENT if context.target_current_content == context.rendered_content else TargetFileStatus.MODIFIED

#
# Service
#

def _warn_on_inconsistency(target_file_path: Path, status: TargetFileStatus):
    if status == TargetFileStatus.MODIFIED:
        print_warning(f"\twarning: {target_file_path} contains manual modifications or is out of date")
    elif status == TargetFileStatus.MISSING:
        print_warning(f"\twarning: {target_file_path} is missing")
