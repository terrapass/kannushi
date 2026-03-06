import argparse
import traceback
import atexit
import signal
from pathlib import Path
from os import system
from enum import Enum
from sys import platform as sys_platform
from io import TextIOWrapper

from . import *

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

#
# Command line arguments
#

def _make_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=_CLI_DESCRIPTION)

    parser.add_argument('source_path', metavar='SOURCE_PATH', type=Path, help='root directory containing Jinja templates')
    parser.add_argument('target_path', metavar='TARGET_PATH', type=Path, help='target root directory for rendered files')

    parser.add_argument('--skip', dest='skip_glob', metavar='SKIP_GLOB', type=str, help='glob for template files to skip when rendering (relative to SOURCE_PATH)')
    parser.add_argument('--vars', dest='vars_glob', metavar='VARS_YAML_GLOB', type=str, help='YAML file(s) containing template variable definitions')

    parser.add_argument(
        _VARS_PROCESSOR_MODULE_ARG, dest='vars_processor_module_locator', metavar='VARS_PROCESSOR_MODULE', type=str, help='Python file/module to use for variables dictionary post-processing')
    parser.add_argument(
        _VARS_PROCESSOR_FUNCTION_ARG, dest='vars_processor_function_name', metavar='VARS_PROCESSOR_FUNCTION', type=str, default=_DEFAULT_VARS_PROCESSOR_FUNCTION_NAME,
        help=f'single-parameter function in VARS_PROCESSOR_MODULE which vars dictionary will be passed to (defaults to {_DEFAULT_VARS_PROCESSOR_FUNCTION_NAME})'
    )

    parser.add_argument('--seed', dest='random_seed', metavar='RANDOM_SEED', type=int, help='RNG seed to use for any randomization')

    parser.add_argument('-j', '--jobs', dest='jobs_count', metavar='JOBS_COUNT', type=int, help='number of parallel jobs (defaults to the number of logical CPU cores)')

    parser.add_argument('-v', '--verbose', action='store_true', dest='is_verbose', help='output processed file paths to stdout')
    parser.add_argument('--no-color', action='store_true', dest='is_color_disabled', help='disable output coloring')

    return parser

#
# Types
#

class _MainExitCode(int, Enum):
    SUCCESS                = 0
    UNKNOWN_ERROR          = 1
    VARS_LOADING_FAILED    = 2
    VARS_PROCESSING_FAILED = 3
    JINJA_RENDER_ERRORS    = 4
    INTERRUPTED            = _SIGNAL_EXIT_CODE_OFFSET + signal.SIGINT

    @staticmethod
    def from_render_dir_result(render_dir_result: RenderDirResult) -> _MainExitCode:
        if render_dir_result.was_interrupted:
            return _MainExitCode.INTERRUPTED
        if render_dir_result.errors_count > 0:
            return _MainExitCode.JINJA_RENDER_ERRORS
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

#
# Main
#

def main():
    parser = _make_cli_parser()
    args   = parser.parse_args()

    set_color_disabled(args.is_color_disabled)
    if not args.is_color_disabled and sys_platform == 'win32':
        system('color')

    config = _make_render_config_from_args(args)

    if isinstance(stdout, TextIOWrapper):
        stdout.reconfigure(line_buffering=True)

    performance_logger = PerformanceLogger(config.is_verbose)
    atexit.register(lambda: performance_logger.log_summary())

    try:
        vars = load_vars_from_yaml_files(args.vars_glob, config.effective_jobs_count, performance_logger) if not args.vars_glob is None else TemplateVariables()
    except KeyboardInterrupt:
        print_warning('warning: Interrupted by the user')
        exit(_MainExitCode.INTERRUPTED)
    except BaseException as e:
        print_error('\n'.join(traceback.format_exception_only(e)))
        exit(_MainExitCode.VARS_LOADING_FAILED)

    inject_service_var(vars, TEMPLATE_PROGRAM_VAR, parser.prog)

    if args.vars_processor_module_locator is not None:
        try:
            post_process_vars(vars, args.vars_processor_module_locator, args.vars_processor_function_name, performance_logger)
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

    result = render_dir(config, vars, performance_logger)
    if result.was_interrupted:
        print_warning(f"warning: Interrupted by the user ({result.skipped_count} template{'s' if result.skipped_count != 1 else ''} skipped)")
    if result.errors_count > 0:
        if config.is_verbose:
            failed_template_paths_str  = '\n'.join(map(str, result.failed_template_paths))
            print_error(f"error: The following {result.errors_count} template{'s' if result.errors_count != 1 else ''} failed to render (see individual errors above):")
            print_error(failed_template_paths_str)
        else:
            print_error(f"error: {result.errors_count} template{'s' if result.errors_count != 1 else ''} failed to render (see individual errors above)")
    elif not result.was_interrupted:
        assert result.is_successful
        print_success(f"All {result.rendered_templates_count} templates rendered without errors")
    exit(_MainExitCode.from_render_dir_result(result))