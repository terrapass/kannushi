import argparse
import importlib
import importlib.util
import random
import signal
from pathlib import Path
from os import path, cpu_count
from dataclasses import dataclass, field
from enum import Enum
from types import ModuleType
from typing import Any, Callable, cast
from multiprocessing import Pool
from multiprocessing.pool import AsyncResult
from functools import partial
from itertools import repeat
from sys import stdout, stderr, modules as sys_modules, path as sys_path
from timeit import default_timer

import glob
import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from jinja2_error import ErrorExtension

#
# Constants
#

VARS_ENCODING = 'utf-8' # This correctly handles UTF-8 with or without BOM for YAML files with variables

SOURCE_ENCODING = 'utf-8' # Treating both source and rendered content as regular UTF-8 handles BOM correctly.
TARGET_ENCODING = 'utf-8' #

TEMPLATE_EXTENSION = '.jinja'
TEMPLATE_GLOB      = '**/*' + TEMPLATE_EXTENSION

TEMPLATE_PATH_VAR    = '_template_path' # Name of the template variable to be set to the currently rendered template's path
TEMPLATE_PROGRAM_VAR = '_program_name'  # ...to be set to this script's name

ASYNC_POLLING_INTERVAL_SECONDS = 0.5 # Primarily determines the reaction time to Ctrl-C (KeyboardInterrupt)

#
# Types
#

# A convenience wrapper around dict, allowing to acess values
# both by key via [] or as regular attributes via dot notation.
class TemplateVariables(dict):
     def __init__(self, vars: dict = {}):
         super().__init__(vars)
         self.__dict__ = self

class ModuleExecutionException(ImportError):
    def __init__(self, original_exception: BaseException):
        self.original_exception = original_exception

class InvalidVarsProcessorInterface(Exception):
    def __init__(self, vars_processor_module_locator: str, vars_processor_function_name: str):
        super().__init__(f"module '{vars_processor_module_locator}' does not expose the required {vars_processor_function_name}(vars: TemplateVariables) function")

@dataclass
class RenderConfig:
    source_path:          Path
    target_path:          Path
    skip_glob:            str | None
    random_seed:          int | None
    requested_jobs_count: int | None
    is_verbose:           bool
    is_color_disabled:    bool

    def __init__(self, args: argparse.Namespace):
        self.source_path          = args.source_path
        self.target_path          = args.target_path
        self.skip_glob            = args.skip_glob
        self.random_seed          = args.random_seed
        self.requested_jobs_count = self.__try_cap_jobs_count(args.jobs_count)
        self.is_verbose           = args.is_verbose
        self.is_color_disabled    = args.is_color_disabled

    @property
    def effective_jobs_count(self) -> int:
        return self.requested_jobs_count or cpu_count() or 1

    @staticmethod
    def __try_cap_jobs_count(jobs_count: int | None) -> int | None:
        if jobs_count is None:
            return None
        if (cpu_count_value := cpu_count()) is not None and jobs_count > cpu_count_value:
            print_warning(f"warning: Capping the number of jobs to {cpu_count_value} - the number of logical CPU cores", file=stdout)
            return cpu_count_value
        return jobs_count

@dataclass
class RenderDirResult:
    selected_templates_count: int        = 0
    rendered_templates_count: int        = 0
    failed_template_paths:    list[Path] = field(default_factory=list)
    was_interrupted:          bool       = False

    @property
    def errors_count(self) -> int:
        return len(self.failed_template_paths)

    @property
    def skipped_count(self) -> int:
        result = self.selected_templates_count - self.rendered_templates_count - self.errors_count
        assert result >= 0
        return result

    @property
    def is_successful(self) -> bool:
        return self.selected_templates_count == self.rendered_templates_count

@dataclass
class RenderTemplateResult:
    target_file_path:    Path
    render_time_seconds: float

class AnsiColor(str, Enum):
    DEFAULT = '\033[0m'
    RED     = '\033[31m'
    GREEN   = '\033[32m'
    YELLOW  = '\033[33m'

class Stage(str, Enum):
    VARS_LOADING     = "YAML variables loading"
    VARS_PROCESSING  = "Variables post-processing"
    RENDER_POOL_INIT = "Render pool initialization"
    JINJA_RENDER     = "Jinja templates rendering"

class PerformanceLogger:
    def __init__(self, is_verbose: bool):
        self.__init_time_seconds         = default_timer()
        self.__stage_start_times_seconds = dict()
        self.__stage_end_times_seconds   = dict()
        self.__stage_errors_counts       = dict()
        self.__interrupted_stages        = set()
        self.__is_verbose                = is_verbose

    @property
    def current_stage(self) -> Stage | None:
        unfinished_stages = self.__stage_start_times_seconds.keys() - self.__stage_end_times_seconds.keys()
        assert len(unfinished_stages) <= 1, "must not have multiple unfinished stages simultaneously"
        try:
            return next(iter(unfinished_stages))
        except StopIteration:
            return None

    def on_stage_started(self, stage: Stage):
        assert stage not in self.__stage_start_times_seconds
        self.__stage_start_times_seconds[stage] = default_timer()

    def on_stage_ended(self, stage: Stage, errors_count: int, was_interrupted: bool):
        assert stage in self.__stage_start_times_seconds and stage not in self.__stage_end_times_seconds
        assert stage not in self.__stage_errors_counts
        self.__stage_end_times_seconds[stage] = default_timer()
        self.__stage_errors_counts[stage]     = errors_count
        if was_interrupted:
            self.__interrupted_stages.add(stage)
        stage_verb_str = "interrupted after" if was_interrupted else "completed in"
        print(f"{stage.value} {stage_verb_str} {self.__stage_time_seconds(stage):.1f} seconds{self.__format_errors_count(' with {0}', errors_count)}")

    def log_summary(self):
        total_runtime_seconds = default_timer() - self.__init_time_seconds
        print(f"Total runtime: {total_runtime_seconds:.1f} seconds{', stages:' if self.__is_verbose else ''}")
        if not self.__is_verbose:
            return
        for stage in Stage:
            stage_time_seconds = self.__stage_time_seconds(stage)
            if stage_time_seconds is None:
                continue
            assert stage in self.__stage_errors_counts
            stage_stats_str = self.__format_stage_stats(stage_time_seconds, self.__stage_errors_counts[stage], stage in self.__interrupted_stages)
            print(f"- {stage.value:<27}{stage_stats_str}")

    def __stage_time_seconds(self, stage: Stage) -> float | None:
        if stage not in self.__stage_start_times_seconds or stage not in self.__stage_end_times_seconds:
            return None
        return self.__stage_end_times_seconds[stage] - self.__stage_start_times_seconds[stage]

    @staticmethod
    def __format_errors_count(format: str, errors_count: int) -> str:
        return format.format(f"{errors_count} error{'s' if errors_count != 1 else ''}") if errors_count > 0 else ""

    @staticmethod
    def __format_stage_stats(stage_time_seconds: float, errors_count: int, was_interrupted: bool) -> str:
        errors_count_str_template = ' ({0}, interrupted)' if was_interrupted else ' ({0})'
        errors_count_str          = PerformanceLogger.__format_errors_count(errors_count_str_template, errors_count)
        secondary_stats_str       = errors_count_str if len(errors_count_str) > 0 else (" (interrupted)" if was_interrupted else "")
        return f"{stage_time_seconds:.1f}s{secondary_stats_str}"

#
# Globals
#

_jinja_env         = None
_is_color_disabled = False
_vars              = None

#
# Service
#

def load_vars_from_yaml_files(vars_files_glob: str, jobs_count: int, performance_logger: PerformanceLogger) -> TemplateVariables:
    var_files_paths = glob.glob(vars_files_glob, recursive=True)
    var_files_count = len(var_files_paths)

    if var_files_count <= 0:
        print_warning(f"warning: {vars_files_glob} didn't match any files; skipping vars loading")
        return TemplateVariables()

    adjusted_jobs_count = min(var_files_count, jobs_count)
    print(f"Loading template variables from {len(var_files_paths)} files matching {vars_files_glob} in {adjusted_jobs_count} parallel jobs...")

    yaml_loader_class  = select_yaml_loader_class()
    performance_logger.on_stage_started(Stage.VARS_LOADING)
    try:
        with Pool(adjusted_jobs_count, signal.signal, (signal.SIGINT, signal.SIG_IGN)) as process_pool:
            vars_parts = process_pool.starmap(load_dict_from_yaml_file, zip(var_files_paths, repeat(yaml_loader_class)))

        vars = TemplateVariables()
        for vars_part in vars_parts:
            merge_in_vars(vars, vars_part)

        performance_logger.on_stage_ended(Stage.VARS_LOADING, 0, False)

        return vars
    except BaseException as e:
        is_keyboard_interrupt = isinstance(e, KeyboardInterrupt)
        performance_logger.on_stage_ended(Stage.VARS_LOADING, 0 if is_keyboard_interrupt else 1, is_keyboard_interrupt)
        raise

def select_yaml_loader_class() -> type:
    try:
        # Use the faster loader from LibYAML bindings
        return yaml.CLoader
    except AttributeError:
        print_warning('warning: Using the slower Python-based YAML loader')
        print('hint: install LibYAML bindings to switch to the faster C-based loader')
        return yaml.Loader

def load_dict_from_yaml_file(yaml_file_path: Path, yaml_loader_class: type) -> dict:
    with open(yaml_file_path, 'r', encoding=VARS_ENCODING) as yaml_file:
        return yaml.load(yaml_file, Loader=yaml_loader_class)

def merge_in_vars(vars: TemplateVariables, new_vars: dict):
    duplicate_keys = vars.keys() & new_vars
    if len(duplicate_keys) > 0:
        first_duplicate_key = next(iter(duplicate_keys))
        raise ValueError(f'encountered duplicate variable {first_duplicate_key}')

    vars.update(new_vars)

def inject_service_var(vars: TemplateVariables, name: str, value: Any):
    if name in vars:
        raise ValueError(f'service variable name {name} is already used')
    vars[name] = value

def post_process_vars(vars: TemplateVariables, vars_processor_module_locator: str, vars_processor_function_name: str, performance_logger: PerformanceLogger):
    vars_processor_module = load_module(vars_processor_module_locator)
    assert isinstance(vars_processor_module, ModuleType)

    if not hasattr(vars_processor_module, vars_processor_function_name):
        raise InvalidVarsProcessorInterface(vars_processor_module_locator, vars_processor_function_name)
    vars_processor_function = vars_processor_module.__getattribute__(vars_processor_function_name)
    if not callable(vars_processor_function):
        raise InvalidVarsProcessorInterface(vars_processor_module_locator, vars_processor_function_name)

    print(f'Post-processing template variables dictionary using {vars_processor_module.__name__}.{vars_processor_function_name}()')
    performance_logger.on_stage_started(Stage.VARS_PROCESSING)
    try:
        vars_processor_function(vars)
    except BaseException as e:
        is_keyboard_interrupt = isinstance(e, KeyboardInterrupt)
        performance_logger.on_stage_ended(Stage.VARS_PROCESSING, 0 if is_keyboard_interrupt else 1, is_keyboard_interrupt)
        raise
    else:
        performance_logger.on_stage_ended(Stage.VARS_PROCESSING, 0, False)

def load_module(module_locator: str) -> ModuleType:
    try:
        module = importlib.import_module(module_locator)
        print(f"Found Python module '{module_locator}' in the environment")
    except ModuleNotFoundError:
        module = None
        pass

    if module is None:
        module = load_module_from_file(Path(module_locator))
        print(f"Loaded Python module '{module.__name__}' from {module_locator}")

    return module

def load_module_from_file(module_path: Path) -> ModuleType:
    module_name = module_path.stem
    if not module_path.is_file():
        raise ModuleNotFoundError(f"module {module_path} not found", path=str(module_path))
    module_spec = importlib.util.spec_from_file_location(module_name, module_path)
    if module_spec is None:
        raise ImportError(f"failed to import Python module from file {module_path}", path=str(module_path))

    module = importlib.util.module_from_spec(module_spec)
    if (module_spec.name in sys_modules):
        print_warning(f"warning: Replacing existing module '{module_spec.name}' with the one loaded from {module_path}")
    sys_modules[module_spec.name] = module
    sys_path.insert(0, str(module_path.parent))

    assert module_spec.loader is not None
    try:
        module_spec.loader.exec_module(module)
    except BaseException as e:
        raise ModuleExecutionException(e)

    return module

def render_dir(config: RenderConfig, vars: TemplateVariables, performance_logger: PerformanceLogger) -> RenderDirResult:
    templates_paths = config.source_path.glob(TEMPLATE_GLOB)
    skipped_paths   = config.source_path.glob(config.skip_glob) if config.skip_glob is not None else []
    selected_paths  = [template_path for template_path in templates_paths if template_path not in skipped_paths]

    print(f'Initializing {config.effective_jobs_count} render processes...')
    performance_logger.on_stage_started(Stage.RENDER_POOL_INIT)

    def job_success_callback(template_result: RenderTemplateResult):
        result.rendered_templates_count += 1
        if config.is_verbose:
            print(f'[{template_result.render_time_seconds:4.2f}s] {template_result.target_file_path}')

    def job_error_callback(template_path: Path, e: BaseException):
        result.failed_template_paths.append(template_path)
        (_, target_file_path) = convert_template_path(config.source_path, config.target_path, template_path)
        print_error(f'[ERROR] {target_file_path}')
        print_error(f'\terror: {e}')

    result = RenderDirResult()
    result.selected_templates_count = len(selected_paths)
    with Pool(config.effective_jobs_count, init_render_template_process, (config.source_path, vars, config.is_color_disabled)) as process_pool:
        def render_template_async(template_path) -> AsyncResult:
            return process_pool.apply_async(
                render_template_job,
                (config, template_path),
                callback=job_success_callback,
                error_callback=cast(Callable[[BaseException], None], partial(job_error_callback, template_path))
            )
        try:
            async_results = [render_template_async(template_path) for template_path in selected_paths]
            process_pool.close()
            performance_logger.on_stage_ended(Stage.RENDER_POOL_INIT, 0, False)

            print(f'Rendering {len(selected_paths)} templates in {config.effective_jobs_count} parallel jobs...')
            performance_logger.on_stage_started(Stage.JINJA_RENDER)

            for async_result in async_results:
                while not async_result.ready():
                    async_result.wait(ASYNC_POLLING_INTERVAL_SECONDS)

            process_pool.join()
        except KeyboardInterrupt:
            result.was_interrupted = True
            process_pool.terminate()

    if (current_stage := performance_logger.current_stage) is not None:
        performance_logger.on_stage_ended(current_stage, result.errors_count, result.was_interrupted)

    return result

def init_render_template_process(source_path: Path, vars: TemplateVariables, is_color_disabled: bool):
    # Prevent Ctrl-C from raising KeyboardInterrupt in child processes
    signal.signal(signal.SIGINT, signal.SIG_IGN)

    global _jinja_env, _is_color_disabled, _vars
    _jinja_env = Environment(
        loader=FileSystemLoader(source_path, encoding=SOURCE_ENCODING),
        extensions=['jinja2.ext.do', ErrorExtension],
        autoescape=False,
        undefined=StrictUndefined
    )
    _is_color_disabled = is_color_disabled
    _vars              = vars

def render_template_job(config: RenderConfig, template_path: Path) -> RenderTemplateResult:
    """This function is the entry point for individual template rendering jobs run in parallel"""

    assert isinstance(_jinja_env, Environment)
    assert isinstance(_vars, dict)

    render_start_time_seconds = default_timer()

    (template_name, target_file_path) = convert_template_path(config.source_path, config.target_path, template_path)

    inject_service_var(_vars, TEMPLATE_PATH_VAR, replace_backslashes(template_path))
    try:
        random.seed(config.random_seed)
        render_template(_jinja_env, template_name, target_file_path, _vars)
    finally:
        del _vars[TEMPLATE_PATH_VAR]

    return RenderTemplateResult(target_file_path, default_timer() - render_start_time_seconds)

def render_template(jinja_env: Environment, template_name: str, target_file_path: Path, vars: dict):
    template = jinja_env.get_template(template_name)
    rendered_content = template.render(vars)
    with open(target_file_path, 'w', encoding=TARGET_ENCODING) as target_file:
        target_file.write(rendered_content)

def convert_template_path(source_dir_path: Path, target_dir_path: Path, template_path: Path) -> tuple[str, Path]:
    template_name    = source_template_path_to_name(source_dir_path, template_path)
    target_file_path = template_name_to_target_file_path(target_dir_path, template_name)
    return (template_name, target_file_path)

def source_template_path_to_name(source_dir_path: Path, template_path: Path) -> str:
    return replace_backslashes(path.relpath(template_path, source_dir_path))

def replace_backslashes(path_str: str | Path) -> str:
    return str(path_str).replace('\\', '/')

def template_name_to_target_file_path(target_dir_path: Path, template_name: str) -> Path:
    assert template_name.endswith(TEMPLATE_EXTENSION)
    return target_dir_path / template_name[:-len(TEMPLATE_EXTENSION)]

def print_success(*args, **kwargs):
    print_in_color(AnsiColor.GREEN, *args, **kwargs)

def print_warning(*args, **kwargs):
    print_in_color(AnsiColor.YELLOW, *args, **dict({'file' : stderr}, **kwargs))

def print_error(*args, **kwargs):
    print_in_color(AnsiColor.RED, *args, **dict({'file' : stderr}, **kwargs))

def print_in_color(color: AnsiColor, *args, **kwargs):
    print(f"{'' if _is_color_disabled else color.value}{args[0]}{'' if _is_color_disabled else AnsiColor.DEFAULT.value}", *(args[1:]), **kwargs)

#
# Interface
#

def set_color_disabled(is_color_disabled: bool):
    global _is_color_disabled
    _is_color_disabled = is_color_disabled
