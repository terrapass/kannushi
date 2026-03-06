import importlib
import importlib.util
import random
import signal
from pathlib import Path
from os import path, cpu_count
from dataclasses import dataclass, field
from types import ModuleType
from typing import Any, Callable, cast
from multiprocessing import Pool
from multiprocessing.pool import AsyncResult
from functools import cached_property, partial
from itertools import repeat
from sys import stdout, stderr, modules as sys_modules, path as sys_path
from timeit import default_timer

import glob
import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from jinja2_error import ErrorExtension

from . import exceptions
from .timing import Stage, ProgressListener, NullProgressListener
from ._logging import *

#
# Constants
#

VARS_ENCODING = 'utf-8' # This correctly handles UTF-8 with or without BOM for YAML files with variables

SOURCE_ENCODING = 'utf-8' # Treating both source and rendered content as regular UTF-8 handles BOM correctly.
TARGET_ENCODING = 'utf-8' #

TEMPLATE_EXTENSION = '.jinja'
TEMPLATE_GLOB      = '**/*' + TEMPLATE_EXTENSION

TEMPLATE_PATH_VAR = '_template_path' # Name of the template variable to be set to the currently rendered template's path

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

@dataclass
class RenderConfig:
    source_path:          Path
    target_path:          Path
    skip_glob:            str | None
    random_seed:          int | None
    requested_jobs_count: int | None
    is_verbose:           bool
    is_color_disabled:    bool

    @cached_property
    def effective_jobs_count(self) -> int:
        return self.__try_cap_jobs_count(self.requested_jobs_count) or cpu_count() or 1

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

#
# Globals
#

_jinja_env = None
_vars      = None

#
# Service
#

def load_vars_from_yaml_files(vars_files_glob: str, jobs_count: int, progress_listener: ProgressListener = NullProgressListener()) -> TemplateVariables:
    var_files_paths = glob.glob(vars_files_glob, recursive=True)
    var_files_count = len(var_files_paths)

    if var_files_count <= 0:
        print_warning(f"warning: {vars_files_glob} didn't match any files; skipping vars loading")
        return TemplateVariables()

    adjusted_jobs_count = min(var_files_count, jobs_count)
    print(f"Loading template variables from {len(var_files_paths)} files matching {vars_files_glob} in {adjusted_jobs_count} parallel jobs...")

    yaml_loader_class  = select_yaml_loader_class()
    progress_listener.on_stage_started(Stage.VARS_LOADING)
    try:
        with Pool(adjusted_jobs_count, signal.signal, (signal.SIGINT, signal.SIG_IGN)) as process_pool:
            vars_parts = process_pool.starmap(load_dict_from_yaml_file, zip(var_files_paths, repeat(yaml_loader_class)))

        vars = TemplateVariables()
        for vars_part in vars_parts:
            merge_in_vars(vars, vars_part)

        progress_listener.on_stage_ended(Stage.VARS_LOADING, 0, False)

        return vars
    except BaseException as e:
        is_keyboard_interrupt = isinstance(e, KeyboardInterrupt)
        progress_listener.on_stage_ended(Stage.VARS_LOADING, 0 if is_keyboard_interrupt else 1, is_keyboard_interrupt)
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

def post_process_vars(
        vars:                          TemplateVariables,
        vars_processor_module_locator: str,
        vars_processor_function_name:  str,
        progress_listener:             ProgressListener = NullProgressListener()
    ):
    vars_processor_module = load_module(vars_processor_module_locator)
    assert isinstance(vars_processor_module, ModuleType)

    if not hasattr(vars_processor_module, vars_processor_function_name):
        raise exceptions.InvalidVarsProcessorInterface(vars_processor_module_locator, vars_processor_function_name)
    vars_processor_function = vars_processor_module.__getattribute__(vars_processor_function_name)
    if not callable(vars_processor_function):
        raise exceptions.InvalidVarsProcessorInterface(vars_processor_module_locator, vars_processor_function_name)

    print(f'Post-processing template variables dictionary using {vars_processor_module.__name__}.{vars_processor_function_name}()')
    progress_listener.on_stage_started(Stage.VARS_PROCESSING)
    try:
        vars_processor_function(vars)
    except BaseException as e:
        is_keyboard_interrupt = isinstance(e, KeyboardInterrupt)
        progress_listener.on_stage_ended(Stage.VARS_PROCESSING, 0 if is_keyboard_interrupt else 1, is_keyboard_interrupt)
        raise
    else:
        progress_listener.on_stage_ended(Stage.VARS_PROCESSING, 0, False)

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
        raise exceptions.ModuleExecutionException(e)

    return module

def render_dir(config: RenderConfig, vars: TemplateVariables, progress_listener: ProgressListener) -> RenderDirResult:
    templates_paths = config.source_path.glob(TEMPLATE_GLOB)
    skipped_paths   = config.source_path.glob(config.skip_glob) if config.skip_glob is not None else []
    selected_paths  = [template_path for template_path in templates_paths if template_path not in skipped_paths]

    current_stage = None
    def change_stage(stage: Stage | None, current_stage_errors_count: int = 0, was_interrupted: bool = False):
        nonlocal current_stage
        if current_stage is not None:
            progress_listener.on_stage_ended(current_stage, current_stage_errors_count, was_interrupted)
        current_stage = stage
        if current_stage is not None:
            progress_listener.on_stage_started(current_stage)

    print(f'Initializing {config.effective_jobs_count} render processes...')
    change_stage(Stage.RENDER_POOL_INIT)

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

            change_stage(Stage.JINJA_RENDER)
            print(f'Rendering {len(selected_paths)} templates in {config.effective_jobs_count} parallel jobs...')

            for async_result in async_results:
                while not async_result.ready():
                    async_result.wait(ASYNC_POLLING_INTERVAL_SECONDS)

            process_pool.join()
        except KeyboardInterrupt:
            result.was_interrupted = True
            process_pool.terminate()

    change_stage(None, result.errors_count if current_stage == Stage.JINJA_RENDER else 0, result.was_interrupted)

    return result

def init_render_template_process(source_path: Path, vars: TemplateVariables, is_color_disabled: bool):
    # Prevent Ctrl-C from raising KeyboardInterrupt in child processes
    signal.signal(signal.SIGINT, signal.SIG_IGN)

    global _jinja_env, _vars
    _jinja_env = Environment(
        loader=FileSystemLoader(source_path, encoding=SOURCE_ENCODING),
        extensions=['jinja2.ext.do', ErrorExtension],
        autoescape=False,
        undefined=StrictUndefined
    )
    _vars = vars
    set_color_disabled(is_color_disabled)

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
