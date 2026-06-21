import argparse
import traceback
import atexit
import signal
import multiprocessing
import importlib.metadata
from pathlib import Path
from os import system
from enum import Enum
from dataclasses import dataclass
from typing import Any, NoReturn
from sys import stdout, platform as sys_platform
from io import TextIOWrapper

import yaml

from .. import (
    TemplateVariables, RenderConfig, RenderDirResult, TargetFileStatus, load_vars_from_yaml_files, post_process_vars,
    render_dir, composite_render_pipeline, writing_render_handler, make_diff_render_pipeline_step
)
from ..exceptions import ModuleExecutionException, InvalidVarsProcessorInterface
from ..timing import Stage, StageRuntimeReporter
from .._logging import set_color_disabled, set_verbose, is_verbose, print_success, print_verbose, print_warning, print_error
from .junit import IS_JUNIT_AVAILABLE, JunitReport, write_junit_report_to_xml_file

#
# Constants
#

_PROGRAM_NAME    = 'kannushi'
_CLI_DESCRIPTION = """
Renders all Jinja templates in a directory into files in another directory, preserving the folder structure.
Templates must use UTF-8 (with or without BOM), rendered files will reflect their source templates' BOM or lack thereof.
"""

_VARS_PROCESSOR_MODULE_ARG   = '--vars-processor'
_VARS_PROCESSOR_FUNCTION_ARG = '--vars-processor-func'

_DEFAULT_VARS_PROCESSOR_FUNCTION_NAME = 'process_vars'

_SIGNAL_EXIT_CODE_OFFSET = 128

_MAX_FILE_PATHS_LOGGED_NON_VERBOSE = 5

_DIFF_ENCODING = 'utf-8'

#
# Command line arguments
#

def _make_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=_PROGRAM_NAME, description=_CLI_DESCRIPTION)

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

    parser.add_argument('-j', '--jobs', dest='jobs_count', metavar='JOBS_COUNT', type=int, help='max number of parallel jobs (defaults to the number of logical CPU cores)')

    parser.add_argument(
        '--check', action='store_const', dest='mode', const=_Mode.VERIFICATION, default=_Mode.WRITING,
        help='check if files under TARGET_PATH are consistent with their templates from SOURCE_PATH, make no changes on disk, exit non-zero if any inconsistencies are found'
    )

    parser.add_argument('--log', dest='log_yaml_path', metavar='LOG_YAML_PATH', type=Path, help='output log file path (logs written as YAML)')
    parser.add_argument('--diff', dest='diff_path', metavar='DIFF_PATH', type=Path, help='output path for unified diff between current and newly-rendered versions of target files')

    if IS_JUNIT_AVAILABLE:
        parser.add_argument(
            '--junit', dest='junit_xml_path', metavar='JUNIT_XML_PATH', type=Path,
            help="output path for a JUnit XML report (requires --check)"
        )

    parser.add_argument('-v', '--verbose', action='store_true', dest='is_verbose', help='output all processed file paths, render times and additional info to stdout')
    parser.add_argument('--no-color', action='store_true', dest='is_color_disabled', help='disable output coloring')

    parser.add_argument('-V', '--version', action='version', version=f'%(prog)s {importlib.metadata.version(_PROGRAM_NAME)}', help=f'print {_PROGRAM_NAME} version and exit')

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
    def from_target_file_statuses(target_file_statuses: dict[Path, TargetFileStatus]) -> "_VerificationResult":
        modified_file_paths = []
        missing_file_paths  = []
        for file_path, file_status in target_file_statuses.items():
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

    def to_log_str(self) -> str:
        return self.name.lower()

class _MainContext:
    def __init__(self, args: argparse.Namespace, stage_time_reporter: StageRuntimeReporter):
        self.__args                                              = args
        self.__stage_time_reporter                               = stage_time_reporter
        self.__vars_loading_error:    str | None                 = None
        self.__vars_processing_error: str | None                 = None
        self.__render_dir_result:     RenderDirResult | None     = None
        self.__verification_result:   _VerificationResult | None = None
        self.__unified_diff:          str | None                 = None
        if args.log_yaml_path is not None:
            atexit.register(self.__write_yaml_log, args.log_yaml_path)
        if args.diff_path is not None:
            atexit.register(self.__write_diff, args.diff_path)
        junit_xml_path = getattr(args, 'junit_xml_path', None)
        if junit_xml_path is not None:
            atexit.register(self.__write_junit_xml_report, junit_xml_path)

    def on_user_interruption(self, added_note: str | None = None) -> NoReturn:
        atexit.unregister(self.__write_yaml_log)
        atexit.unregister(self.__write_diff)
        atexit.unregister(self.__write_junit_xml_report)
        print_warning(f"warning: Interrupted by the user{f' ({added_note})' if added_note is not None else ''}")
        self.__exit_with_code(_MainExitCode.INTERRUPTED)

    def on_vars_loading_error(self, error: str) -> NoReturn:
        self.__vars_loading_error = error
        print_error(error)
        self.__exit_with_code(_MainExitCode.VARS_LOADING_FAILED)

    def on_vars_processing_error(self, error: str, hint: str | None = None) -> NoReturn:
        self.__vars_processing_error = error
        print_error(error)
        if hint is not None:
            print(hint)
        self.__exit_with_code(_MainExitCode.VARS_PROCESSING_FAILED)

    def finish_with_results(self, render_dir_result: RenderDirResult, verification_result: _VerificationResult | None, unified_diff: str | None) -> NoReturn:
        self.__render_dir_result   = render_dir_result
        self.__verification_result = verification_result
        self.__unified_diff        = unified_diff
        self.__exit_with_code(_MainExitCode.from_results(self.__render_dir_result, self.__verification_result))

    def __exit_with_code(self, exit_code: _MainExitCode) -> NoReturn:
        self.__exit_code = exit_code
        exit(exit_code)

    def __write_yaml_log(self, log_yaml_path: Path):
        assert self.__exit_code != _MainExitCode.INTERRUPTED
        print_verbose(f"Writing log as YAML to {log_yaml_path}...")
        try:
            _MainContext.__write_yaml_log_impl(log_yaml_path, self.__to_log_dict())
        except BaseException as e:
            print_warning(f"warning: Failed to write log to {log_yaml_path} ({e})")

    def __write_diff(self, diff_path: Path):
        assert self.__exit_code != _MainExitCode.INTERRUPTED
        if self.__unified_diff is None:
            return
        print_verbose(f"Writing unified diff to {diff_path}...")
        try:
            diff_path.write_text(self.__unified_diff, encoding=_DIFF_ENCODING, newline='')
        except BaseException as e:
            print_warning(f"warning: Failed to write diff to {diff_path} ({e})")

    def __write_junit_xml_report(self, junit_xml_path: Path):
        assert self.__exit_code != _MainExitCode.INTERRUPTED
        print_verbose(f"Writing JUnit XML report to {junit_xml_path}...")
        try:
            report = JunitReport(
                vars_loading_requested    = self.__args.vars_glob is not None,
                vars_loading_error        = self.__vars_loading_error,
                vars_loading_elapsed      = self.__stage_time_reporter.stage_time_seconds(Stage.VARS_LOADING),
                vars_processing_requested = self.__args.vars_processor_module_locator is not None,
                vars_processing_error     = self.__vars_processing_error,
                vars_processing_elapsed   = self.__stage_time_reporter.stage_time_seconds(Stage.VARS_PROCESSING),
                render_result             = self.__render_dir_result,
                render_elapsed            = self.__stage_time_reporter.stage_time_seconds(Stage.JINJA_RENDER),
                verification_result       = self.__verification_result,
            )
            write_junit_report_to_xml_file(report, junit_xml_path)
        except BaseException as e:
            print_warning(f"warning: Failed to write JUnit XML report to {junit_xml_path} ({e})")

    @staticmethod
    def __write_yaml_log_impl(log_yaml_path: Path, log_dict: dict):
        with open(log_yaml_path, 'w') as log_yaml_file:
            yaml.dump(log_dict, log_yaml_file)

    def __to_log_dict(self) -> dict:
        return {
            "result":                self.__exit_code.to_log_str(),
            "input":                 self.__input_to_log_dict(),
            "vars_loading_error":    self.__vars_loading_error,
            "vars_processing_error": self.__vars_processing_error,
            "render":                self.__render_dir_result_to_log_dict(),
            "verification":          self.__verification_result_to_log_dict(),
        }

    def __input_to_log_dict(self) -> dict:
        return {
            "source_path":         str(self.__args.source_path),
            "target_path":         str(self.__args.target_path),
            "vars_glob":           self.__args.vars_glob,
            "skip_glob":           self.__args.skip_glob,
            "vars_processor":      self.__args.vars_processor_module_locator,
            "vars_processor_func": self.__args.vars_processor_function_name if self.__args.vars_processor_module_locator is not None else None,
        }

    def __render_dir_result_to_log_dict(self) -> dict | None:
        if self.__render_dir_result is None:
            return None
        return {
            "is_successful":            self.__render_dir_result.is_successful,
            "selected_templates_count": self.__render_dir_result.selected_templates_count,
            "rendered_templates_count": self.__render_dir_result.rendered_templates_count,
            "skipped_count":            self.__render_dir_result.skipped_count,
            "errors_count":             self.__render_dir_result.errors_count,
            "render_errors": [
                {"path": str(path), "error": str(error)} for path, error in self.__render_dir_result.errors_by_target_file_path.items()
            ]
        }

    def __verification_result_to_log_dict(self) -> dict | None:
        if self.__verification_result is None:
            return None
        def to_str_list(any_list: list[Any]) -> list[str]:
            return list(map(lambda value: str(value), any_list))
        return {
            "is_successful":         self.__verification_result.is_successful,
            "total_inconsistencies": self.__verification_result.total_inconsistencies,
            "modified_files_count":  self.__verification_result.modified_files_count,
            "missing_files_count":   self.__verification_result.missing_files_count,
            "modified_file_paths":   to_str_list(self.__verification_result.modified_file_paths),
            "missing_file_paths":    to_str_list(self.__verification_result.missing_file_paths),
        }

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
    )

def _try_select_multiprocessing_start_method():
    if sys_platform.startswith('linux') and 'fork' in multiprocessing.get_all_start_methods():
        multiprocessing.set_start_method('fork', force=True) # avoids pickling vars for Jinja render pool

def _try_log_verification_result(verification_result: _VerificationResult | None, render_result: RenderDirResult):
    assert not render_result.was_interrupted
    if verification_result is None:
        return
    if verification_result.is_successful and render_result.is_successful:
        print_success("All existing files are consistent with their source templates")
        return
    summary_str = ', '.join(
        summary_part for summary_part in [
            f"{verification_result.modified_files_count} modified/out-of-date" if verification_result.modified_files_count > 0 else None,
            f"{verification_result.missing_files_count} missing"               if verification_result.missing_files_count > 0  else None,
            f"{render_result.errors_count} failed to render"                   if render_result.errors_count > 0               else None,
        ] if summary_part is not None
    )
    total_reported_inconsistencies = verification_result.total_inconsistencies + render_result.errors_count
    print_error(
        f"error: Consistency check failed for {total_reported_inconsistencies} file{'' if total_reported_inconsistencies == 1 else 's'} ({summary_str})\n"
    )
    _try_log_file_list(
        f"contain{'s' if verification_result.modified_files_count == 1 else ''} manual modifications or {'is' if verification_result.modified_files_count == 1 else 'are'} out of date",
        verification_result.modified_file_paths
    )
    _try_log_file_list(
        f"{'is' if verification_result.missing_files_count == 1 else 'are'} missing",
        verification_result.missing_file_paths
    )

def _try_log_file_list(explanation: str, file_paths: list[Path]):
    file_paths_count = len(file_paths)
    if file_paths_count <= 0:
        return
    print_error(f"error: {file_paths_count} file{'' if file_paths_count == 1 else 's'} {explanation}:")
    for file_path in file_paths[:(file_paths_count if is_verbose() else _MAX_FILE_PATHS_LOGGED_NON_VERBOSE)]:
        print_error(str(file_path))
    if not is_verbose() and file_paths_count > _MAX_FILE_PATHS_LOGGED_NON_VERBOSE:
        print_error(f"# ...and {file_paths_count - _MAX_FILE_PATHS_LOGGED_NON_VERBOSE} more; re-run with --verbose for the full list")
    print_error("")

#
# Main
#

def main():
    parser = _make_cli_parser()
    args   = parser.parse_args()

    if IS_JUNIT_AVAILABLE and args.junit_xml_path is not None and args.mode != _Mode.VERIFICATION:
        parser.error('--junit requires --check')

    set_verbose(args.is_verbose)
    set_color_disabled(args.is_color_disabled)
    if not args.is_color_disabled and sys_platform == 'win32':
        system('color')

    _try_select_multiprocessing_start_method()

    config              = _make_render_config_from_args(args)
    stage_time_reporter = StageRuntimeReporter(args.is_verbose)
    context             = _MainContext(args, stage_time_reporter)

    must_diff = args.diff_path is not None
    if args.mode == _Mode.VERIFICATION:
        diff_render_handler, diff_result_observer = make_diff_render_pipeline_step(must_collect_unified_diff=must_diff, must_warn_on_inconsistency=args.is_verbose)
        render_pipeline_steps                     = [(diff_render_handler, diff_result_observer)]
    elif must_diff:
        diff_render_handler, diff_result_observer = make_diff_render_pipeline_step(must_collect_unified_diff=True, must_warn_on_inconsistency=False) # inconsistencies are expected when writing
        render_pipeline_steps                     = [(diff_render_handler, diff_result_observer), (writing_render_handler, None)]
    else:
        diff_result_observer  = None
        render_pipeline_steps = [(writing_render_handler, None)]
    render_handler, render_result_observer = composite_render_pipeline(render_pipeline_steps)

    if isinstance(stdout, TextIOWrapper):
        stdout.reconfigure(line_buffering=True)

    atexit.register(lambda: stage_time_reporter.log_summary())

    try:
        vars = load_vars_from_yaml_files(args.vars_glob, config.effective_jobs_count, stage_time_reporter) if args.vars_glob is not None else TemplateVariables()
    except KeyboardInterrupt:
        context.on_user_interruption()
    except BaseException as e:
        context.on_vars_loading_error('\n'.join(traceback.format_exception_only(e)))

    if args.vars_processor_module_locator is not None:
        try:
            post_process_vars(vars, args.vars_processor_module_locator, args.vars_processor_function_name, stage_time_reporter)
        except KeyboardInterrupt:
            context.on_user_interruption()
        except ModuleExecutionException as e:
            context.on_vars_processing_error(
                '\n'.join([
                    '\n'.join(traceback.format_exception(e.original_exception)),
                    f"error: Failed to load module {args.vars_processor_module_locator} due to the exception above",
                ])
            )
        except ImportError as e:
            context.on_vars_processing_error(f"error: {e}", f'hint: make sure a valid Python module name or .py file path is given via {_VARS_PROCESSOR_MODULE_ARG}')
        except InvalidVarsProcessorInterface as e:
            context.on_vars_processing_error(f"error: {e}")
        except BaseException as e:
            context.on_vars_processing_error(
                '\n'.join([
                    '\n'.join(traceback.format_exception(e)),
                    f"error: Failed to process variables using {args.vars_processor_module_locator} due to the exception above",
                ])
            )
    elif args.vars_processor_function_name != _DEFAULT_VARS_PROCESSOR_FUNCTION_NAME:
        print_warning(f"warning: Ignoring {_VARS_PROCESSOR_FUNCTION_ARG} in the absence of {_VARS_PROCESSOR_MODULE_ARG}")

    render_result = render_dir(config, vars, render_handler, render_result_observer, progress_listener=stage_time_reporter)

    verification_result = None
    if args.mode == _Mode.VERIFICATION:
        assert diff_result_observer is not None
        verification_result = _VerificationResult.from_target_file_statuses(diff_result_observer.target_file_statuses)

    if render_result.was_interrupted:
        context.on_user_interruption(f"{render_result.skipped_count} template{'s' if render_result.skipped_count != 1 else ''} skipped")
    if render_result.errors_count > 0:
        assert len(render_result.errors_by_target_file_path) > 0
        _try_log_file_list(f"failed to render from template{'' if len(render_result.errors_by_target_file_path) == 1 else 's'}", list(render_result.errors_by_target_file_path.keys()))
    elif not render_result.was_interrupted:
        assert render_result.is_successful
        is_verification_failed = verification_result is not None and not verification_result.is_successful
        (print_warning if is_verification_failed else print_success)(
            f"All {render_result.rendered_templates_count} templates rendered without errors{' but there are inconsistencies' if is_verification_failed else ''}",
            file=stdout
        )

    _try_log_verification_result(verification_result, render_result)
    context.finish_with_results(
        render_result,
        verification_result,
        diff_result_observer.unified_diff if diff_result_observer is not None else None
    )
