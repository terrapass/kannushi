import argparse
import traceback
import atexit
import signal
from pathlib import Path
from os import system
from enum import Enum
from dataclasses import dataclass
from sys import stdout, platform as sys_platform
from io import TextIOWrapper

from . import (
    TemplateVariables, RenderConfig, RenderDirResult, TargetFileStatus, load_vars_from_yaml_files, post_process_vars,
    render_dir, writing_render_handler, verification_render_handler, verification_render_result_observer
)
from .exceptions import ModuleExecutionException, InvalidVarsProcessorInterface
from .timing import StageRuntimeReporter
from ._logging import set_color_disabled, print_success, print_warning, print_error

#
# Constants
#

_CLI_DESCRIPTION = """
Renders all Jinja templates in a directory into files in another directory, preserving the folder structure.
Templates must use UTF-8 (with or without BOM), rendered files will reflect their source templates' BOM or lack thereof.
"""

_VARS_PROCESSOR_MODULE_ARG   = '--vars-processor'
_VARS_PROCESSOR_FUNCTION_ARG = '--vars-processor-func'

_DEFAULT_VARS_PROCESSOR_FUNCTION_NAME = 'process_vars'

_SIGNAL_EXIT_CODE_OFFSET = 128

_MAX_FILE_PATHS_LOGGED_NON_VERBOSE = 5

#
# Command line arguments
#

def _make_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='kannushi', description=_CLI_DESCRIPTION)

    parser.add_argument('source_path', metavar='SOURCE_PATH', type=Path, help='root directory containing Jinja templates')
    parser.add_argument('target_path', metavar='TARGET_PATH', type=Path, help='target root directory for rendered files')

    parser.add_argument('--skip', dest='skip_glob', metavar='SKIP_GLOB', type=str, help='glob for template files to skip when rendering (relative to SOURCE_PATH)')
    parser.add_argument('--vars', dest='vars_glob', metavar='VARS_YAML_GLOB', type=str, help='YAML file(s) containing template variable definitions')

    parser.add_argument(
        _VARS_PROCESSOR_MODULE_ARG, dest='vars_processor_module_locator', metavar='VARS_PROCESSOR_MODULE', type=str, help='Python file/module to use for variables dictionary post-processing'
    )
    parser.add_argument(
        _VARS_PROCESSOR_FUNCTION_ARG, dest='vars_processor_function_name', metavar='VARS_PROCESSOR_FUNCTION', type=str, default=_DEFAULT_VARS_PROCESSOR_FUNCTION_NAME,
        help=f'single-parameter function in VARS_PROCESSOR_MODULE which vars dictionary will be passed to (defaults to {_DEFAULT_VARS_PROCESSOR_FUNCTION_NAME})'
    )

    parser.add_argument('--seed', dest='random_seed', metavar='RANDOM_SEED', type=int, help='RNG seed to use for any randomization within templates')

    parser.add_argument('-j', '--jobs', dest='jobs_count', metavar='JOBS_COUNT', type=int, help='number of parallel jobs (defaults to the number of logical CPU cores)')

    parser.add_argument(
        '--check', action='store_const', dest='mode', const=_Mode.VERIFICATION, default=_Mode.WRITING,
        help='check if files under TARGET_PATH are consistent with their templates from SOURCE_PATH, make no changes on disk, exit non-zero if any inconsistencies are found'
    )

    parser.add_argument('-v', '--verbose', action='store_true', dest='is_verbose', help='output processed file paths and their render times to stdout')
    parser.add_argument('--no-color', action='store_true', dest='is_color_disabled', help='disable output coloring')

    return parser

#
# Types
#

class _Mode(Enum):
    WRITING      = 0
    VERIFICATION = 1

@dataclass
class _VerificationResult:
    modified_file_paths: list[Path]
    missing_file_paths:  list[Path]

    @staticmethod
    def from_render_handler_results(verification_render_handler_results: dict[Path, TargetFileStatus]) -> "_VerificationResult":
        modified_file_paths = []
        missing_file_paths  = []
        for file_path, file_status in verification_render_handler_results.items():
            if file_status == TargetFileStatus.MODIFIED:
                modified_file_paths.append(file_path)
            elif file_status == TargetFileStatus.MISSING:
                missing_file_paths.append(file_path)
        return _VerificationResult(modified_file_paths, missing_file_paths)

    @property
    def modified_files_count(self) -> int:
        return len(self.modified_file_paths)

    @property
    def missing_files_count(self) -> int:
        return len(self.missing_file_paths)

    @property
    def total_inconsistencies(self) -> int:
        return self.modified_files_count + self.missing_files_count

    @property
    def is_successful(self) -> bool:
        return self.total_inconsistencies <= 0

class _MainExitCode(int, Enum):
    SUCCESS                = 0
    UNKNOWN_ERROR          = 1
    VARS_LOADING_FAILED    = 2
    VARS_PROCESSING_FAILED = 3
    JINJA_RENDER_ERRORS    = 4
    VERIFICATION_FAILED    = 5
    INTERRUPTED            = _SIGNAL_EXIT_CODE_OFFSET + signal.SIGINT

    @staticmethod
    def from_results(render_dir_result: RenderDirResult, verification_result: _VerificationResult | None) -> "_MainExitCode":
        if render_dir_result.was_interrupted:
            return _MainExitCode.INTERRUPTED
        if render_dir_result.errors_count > 0:
            return _MainExitCode.JINJA_RENDER_ERRORS
        if verification_result is not None and not verification_result.is_successful:
            return _MainExitCode.VERIFICATION_FAILED
        return _MainExitCode.SUCCESS

#
# Service
#

def _make_render_config_from_args(args: argparse.Namespace) -> RenderConfig:
    return RenderConfig(
        source_path=args.source_path,
        target_path=args.target_path,
        skip_glob=args.skip_glob,
        random_seed=args.random_seed,
        requested_jobs_count=args.jobs_count,
        is_verbose=args.is_verbose,
        is_color_disabled=args.is_color_disabled,
    )

def _try_log_verification_result(verification_result: _VerificationResult | None, is_verbose: bool):
    if verification_result is None:
        return
    if verification_result.is_successful:
        print_success("All existing files are consistent with their source templates")
        return
    summary_str = ', '.join(
        summary_part for summary_part in [
            f"{verification_result.modified_files_count} modified" if verification_result.modified_files_count > 0 else None,
            f"{verification_result.missing_files_count} missing"   if verification_result.missing_files_count > 0  else None,
        ] if summary_part is not None
    )
    print_error(
        f"error: Found {verification_result.total_inconsistencies} inconsistent file{'' if verification_result.total_inconsistencies == 1 else 's'} ({summary_str})\n"
    )
    _try_log_inconsistent_file_paths(
        f"contain{'s' if verification_result.modified_files_count == 1 else ''} manual modifications or {'is' if verification_result.modified_files_count == 1 else 'are'} out of date",
        verification_result.modified_file_paths,
        is_verbose
    )
    _try_log_inconsistent_file_paths(
        f"{'is' if verification_result.missing_files_count == 1 else 'are'} deleted or missing",
        verification_result.missing_file_paths,
        is_verbose
    )

def _try_log_inconsistent_file_paths(inconsistency_explanation: str, file_paths: list[Path], is_verbose: bool):
    file_paths_count = len(file_paths)
    if file_paths_count <= 0:
        return
    print_error(f"{file_paths_count} file{'' if file_paths_count == 1 else 's'} {inconsistency_explanation}:")
    for file_path in file_paths[:(file_paths_count if is_verbose else _MAX_FILE_PATHS_LOGGED_NON_VERBOSE)]:
        print_error(str(file_path))
    if not is_verbose and file_paths_count > _MAX_FILE_PATHS_LOGGED_NON_VERBOSE:
        print_error(f"# (and {file_paths_count - _MAX_FILE_PATHS_LOGGED_NON_VERBOSE} more; re-run with --verbose for the full list)")
    print_error("")

#
# Main
#

def main():
    parser = _make_cli_parser()
    args   = parser.parse_args()

    set_color_disabled(args.is_color_disabled)
    if not args.is_color_disabled and sys_platform == 'win32':
        system('color')

    config  = _make_render_config_from_args(args)

    render_handler         = verification_render_handler         if args.mode == _Mode.VERIFICATION else writing_render_handler
    render_result_observer = verification_render_result_observer if args.mode == _Mode.VERIFICATION and config.is_verbose else None

    if isinstance(stdout, TextIOWrapper):
        stdout.reconfigure(line_buffering=True)

    stage_time_reporter = StageRuntimeReporter(config.is_verbose)
    atexit.register(lambda: stage_time_reporter.log_summary())

    try:
        vars = load_vars_from_yaml_files(args.vars_glob, config.effective_jobs_count, stage_time_reporter) if args.vars_glob is not None else TemplateVariables()
    except KeyboardInterrupt:
        print_warning('warning: Interrupted by the user')
        exit(_MainExitCode.INTERRUPTED)
    except BaseException as e:
        print_error('\n'.join(traceback.format_exception_only(e)))
        exit(_MainExitCode.VARS_LOADING_FAILED)

    if args.vars_processor_module_locator is not None:
        try:
            post_process_vars(vars, args.vars_processor_module_locator, args.vars_processor_function_name, stage_time_reporter)
        except KeyboardInterrupt:
            print_warning('warning: Interrupted by the user')
            exit(_MainExitCode.INTERRUPTED)
        except ModuleExecutionException as e:
            print_error('\n'.join(traceback.format_exception(e.original_exception)))
            print_error(f"error: Failed to load module {args.vars_processor_module_locator} due to the exception above")
            exit(_MainExitCode.VARS_PROCESSING_FAILED)
        except ImportError as e:
            print_error(f"error: {e}")
            print(f'hint: make sure a valid Python module name or .py file path is given via {_VARS_PROCESSOR_MODULE_ARG}')
            exit(_MainExitCode.VARS_PROCESSING_FAILED)
        except InvalidVarsProcessorInterface as e:
            print_error(f"error: {e}")
            exit(_MainExitCode.VARS_PROCESSING_FAILED)
        except BaseException as e:
            print_error('\n'.join(traceback.format_exception(e)))
            print_error(f"error: Failed to process variables using {args.vars_processor_module_locator} due to the exception above")
            exit(_MainExitCode.VARS_PROCESSING_FAILED)
    elif args.vars_processor_function_name != _DEFAULT_VARS_PROCESSOR_FUNCTION_NAME:
        print_warning(f"warning: Ignoring {_VARS_PROCESSOR_FUNCTION_ARG} in the absence of {_VARS_PROCESSOR_MODULE_ARG}")

    render_result       = render_dir(config, vars, render_handler, render_result_observer, progress_listener=stage_time_reporter)
    verification_result = _VerificationResult.from_render_handler_results(render_result.render_handler_results) if args.mode == _Mode.VERIFICATION else None
    if render_result.was_interrupted:
        print_warning(f"warning: Interrupted by the user ({render_result.skipped_count} template{'s' if render_result.skipped_count != 1 else ''} skipped)")
    if render_result.errors_count > 0:
        if config.is_verbose:
            failed_template_paths_str  = '\n'.join(map(str, render_result.failed_template_paths))
            print_error(f"error: The following {render_result.errors_count} template{'s' if render_result.errors_count != 1 else ''} failed to render (see individual errors above):")
            print_error(failed_template_paths_str)
        else:
            print_error(f"error: {render_result.errors_count} template{'s' if render_result.errors_count != 1 else ''} failed to render (see individual errors above)")
    elif not render_result.was_interrupted:
        assert render_result.is_successful
        is_verification_failed = verification_result is not None and not verification_result.is_successful
        (print_warning if is_verification_failed else print_success)(
            f"All {render_result.rendered_templates_count} templates rendered without errors{' but there are inconsistencies' if is_verification_failed else ''}",
            file=stdout
        )
    _try_log_verification_result(verification_result, config.is_verbose)
    exit(_MainExitCode.from_results(render_result, verification_result))
