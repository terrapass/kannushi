import os
import re
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from sys import platform as sys_platform

from .._rendering import RenderTemplateContext
from .._rendering.core import TARGET_ENCODING
from .._logging import print_warning, print_verbose
from . import TargetFileStatus, TargetDiff, DiffRenderResultObserver, determine_target_file_status
from .difflib_backend import DIFF_CONTEXT_LINES_COUNT, make_difflib_unified_diff

#
# Constants
#

_STAGING_DIR_PREFIX = 'kannushi-diff-'

# Together with --no-prefix, these directory names make git print a/<path> and b/<path> natively.
_CURRENT_DIR_NAME  = 'a'
_RENDERED_DIR_NAME = 'b'

_GIT_DIFF_ARGS = [
    'diff', '--no-index', '--no-prefix', '--text', '--no-color', '--no-ext-diff', '--no-textconv',
    f'--unified={DIFF_CONTEXT_LINES_COUNT}', '--diff-algorithm=myers',
    '--', _CURRENT_DIR_NAME, _RENDERED_DIR_NAME
]
_GIT_DIFF_DIFFERENCES_EXIT_CODE = 1

# Under --no-prefix, git names both sides of a new file after b/<path>; git diff proper prints a/<path> first.
# Diff content lines always start with ' ', '+', '-' or '\', so only headers can match.
_NEW_FILE_HEADER_REGEX       = re.compile(f'^diff --git {_RENDERED_DIR_NAME}/', re.MULTILINE)
_NEW_FILE_HEADER_REPLACEMENT = f'diff --git {_CURRENT_DIR_NAME}/'

_LINUX_IN_MEMORY_TEMP_DIR_PATH = '/dev/shm'

_SLOW_DIFF_WARNING = 'warning: Using the slower Python-based diff implementation'

#
# Types
#

class GitDiffStaging:
    """Temporary tree of inconsistent files' current (a/) and rendered (b/) content, diffed by a single git run."""

    def __init__(self, git_executable_path: str, staging_dir: TemporaryDirectory[str]):
        self.__git_executable_path = git_executable_path
        self.__staging_dir         = staging_dir

    @property
    def root_path(self) -> str:
        return self.__staging_dir.name

    def try_make_git_unified_diff(self) -> str | None:
        print_verbose(f"Computing unified diff using {self.__git_executable_path}...")
        try:
            result = subprocess.run([self.__git_executable_path, *_GIT_DIFF_ARGS], cwd=self.root_path, capture_output=True)
        except OSError as e:
            print_warning(f"{_SLOW_DIFF_WARNING} (failed to run git: {e})")
            return None
        if result.returncode != _GIT_DIFF_DIFFERENCES_EXIT_CODE:
            git_error = next(iter(result.stderr.decode(errors='replace').strip().splitlines()), '')
            print_warning(f"{_SLOW_DIFF_WARNING} (git diff exited with code {result.returncode}{f': {git_error}' if git_error else ''})")
            return None
        return _NEW_FILE_HEADER_REGEX.sub(_NEW_FILE_HEADER_REPLACEMENT, result.stdout.decode(TARGET_ENCODING, errors='replace'))

    def make_difflib_unified_diff(self) -> str:
        current_root_path  = Path(self.root_path) / _CURRENT_DIR_NAME
        rendered_root_path = Path(self.root_path) / _RENDERED_DIR_NAME
        rendered_file_paths = sorted(file_path for file_path in rendered_root_path.rglob('*') if file_path.is_file())
        unified_diffs: list[str] = []
        for rendered_file_path in rendered_file_paths:
            relative_path     = rendered_file_path.relative_to(rendered_root_path).as_posix()
            current_file_path = current_root_path / relative_path
            unified_diffs.append(make_difflib_unified_diff(
                _read_staged_file(current_file_path) if current_file_path.is_file() else None,
                _read_staged_file(rendered_file_path),
                relative_path
            ))
        return ''.join(unified_diffs)

    def cleanup(self):
        self.__staging_dir.cleanup()

class StagingDiffRenderHandler:
    """Module-level class rather than a closure, so that it stays picklable under spawn."""

    def __init__(self, staging_root_path: str):
        self.__staging_root_path = staging_root_path

    def __call__(self, context: RenderTemplateContext) -> TargetDiff:
        target_file_status = determine_target_file_status(context)
        if target_file_status == TargetFileStatus.MODIFIED:
            assert context.target_current_content is not None
            self.__stage(_CURRENT_DIR_NAME, context.target_file_relative_path, context.target_current_content)
        if target_file_status != TargetFileStatus.CURRENT:
            self.__stage(_RENDERED_DIR_NAME, context.target_file_relative_path, context.rendered_content)
        return TargetDiff(target_file_status, None)

    def __stage(self, side_dir_name: str, relative_path: str, content: str):
        staged_file_path = Path(self.__staging_root_path) / side_dir_name / relative_path
        staged_file_path.parent.mkdir(parents=True, exist_ok=True)
        # Staging the newline-normalized content that the status check compared keeps CRLF noise out of the diff.
        staged_file_path.write_text(content, encoding=TARGET_ENCODING, newline='')

class GitDiffRenderResultObserver(DiffRenderResultObserver):
    def __init__(self, staging: GitDiffStaging, must_warn_on_inconsistency: bool):
        super().__init__(must_collect_unified_diff=True, must_warn_on_inconsistency=must_warn_on_inconsistency)
        self.__staging                         = staging
        self.__unified_diff: str | None        = None

    @property
    def unified_diff(self) -> str | None:
        if self.__unified_diff is None:
            self.__unified_diff = self.__make_unified_diff()
        return self.__unified_diff

    def __make_unified_diff(self) -> str:
        try:
            if all(status == TargetFileStatus.CURRENT for status in self.target_file_statuses.values()):
                return ''
            git_unified_diff = self.__staging.try_make_git_unified_diff()
            return git_unified_diff if git_unified_diff is not None else self.__staging.make_difflib_unified_diff()
        finally:
            self.__staging.cleanup()

#
# Interface
#

def try_make_git_diff_staging() -> GitDiffStaging | None:
    git_executable_path = shutil.which('git')
    if git_executable_path is None:
        print_warning(_SLOW_DIFF_WARNING)
        print('hint: install git and add it to PATH to switch to the faster git-based diff')
        return None
    try:
        staging_dir = _make_staging_dir()
    except OSError as e:
        print_warning(f"{_SLOW_DIFF_WARNING} (failed to create a temporary directory for git: {e})")
        return None
    return GitDiffStaging(git_executable_path, staging_dir)

#
# Service
#

def _make_staging_dir() -> TemporaryDirectory[str]:
    staging_dir = TemporaryDirectory(prefix=_STAGING_DIR_PREFIX, dir=_select_staging_dir_parent_path())
    try:
        for side_dir_name in (_CURRENT_DIR_NAME, _RENDERED_DIR_NAME):
            (Path(staging_dir.name) / side_dir_name).mkdir()
    except OSError:
        staging_dir.cleanup()
        raise
    return staging_dir

def _select_staging_dir_parent_path() -> str | None:
    """Prefers RAM-backed tmpfs on Linux, returns None to let tempfile pick its default directory otherwise."""
    if sys_platform.startswith('linux') and os.path.isdir(_LINUX_IN_MEMORY_TEMP_DIR_PATH) and os.access(_LINUX_IN_MEMORY_TEMP_DIR_PATH, os.W_OK | os.X_OK):
        return _LINUX_IN_MEMORY_TEMP_DIR_PATH
    return None

def _read_staged_file(staged_file_path: Path) -> str:
    with open(staged_file_path, 'r', encoding=TARGET_ENCODING, newline='') as staged_file:
        return staged_file.read()
