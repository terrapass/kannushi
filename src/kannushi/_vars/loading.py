import signal
from pathlib import Path
from typing import Any
from multiprocessing import Pool
from itertools import repeat

import glob
import yaml

from ..timing import Stage, ProgressListener, NullProgressListener
from .._logging import print_warning

from . import TemplateVariables

#
# Constants
#

_VARS_ENCODING = 'utf-8' # This correctly handles UTF-8 with or without BOM for YAML files with variables

#
# Interface
#

def load_vars_from_yaml_files(vars_files_glob: str, jobs_count: int, progress_listener: ProgressListener = NullProgressListener()) -> TemplateVariables:
    var_files_paths = glob.glob(vars_files_glob, recursive=True)
    var_files_count = len(var_files_paths)

    if var_files_count <= 0:
        print_warning(f"warning: {vars_files_glob} didn't match any files; skipping vars loading")
        return TemplateVariables()

    adjusted_jobs_count = min(var_files_count, jobs_count)
    print(f"Loading template variables from {len(var_files_paths)} files matching {vars_files_glob} in {adjusted_jobs_count} parallel jobs...")

    yaml_loader_class  = _select_yaml_loader_class()
    progress_listener.on_stage_started(Stage.VARS_LOADING)
    try:
        with Pool(adjusted_jobs_count, signal.signal, (signal.SIGINT, signal.SIG_IGN)) as process_pool:
            vars_parts = process_pool.starmap(_load_dict_from_yaml_file, zip(var_files_paths, repeat(yaml_loader_class)))

        vars = TemplateVariables()
        for vars_part in vars_parts:
            _merge_in_vars(vars, vars_part)

        progress_listener.on_stage_ended(Stage.VARS_LOADING, 0, False)

        return vars
    except BaseException as e:
        is_keyboard_interrupt = isinstance(e, KeyboardInterrupt)
        progress_listener.on_stage_ended(Stage.VARS_LOADING, 0 if is_keyboard_interrupt else 1, is_keyboard_interrupt)
        raise

def inject_service_var(vars: TemplateVariables, name: str, value: Any):
    if name in vars:
        raise ValueError(f'service variable name {name} is already used')
    vars[name] = value

#
# Service
#

def _select_yaml_loader_class() -> type:
    try:
        # Use the faster loader from LibYAML bindings
        return yaml.CLoader
    except AttributeError:
        print_warning('warning: Using the slower Python-based YAML loader')
        print('hint: install LibYAML bindings to switch to the faster C-based loader')
        return yaml.Loader

def _load_dict_from_yaml_file(yaml_file_path: Path, yaml_loader_class: type) -> dict:
    with open(yaml_file_path, 'r', encoding=_VARS_ENCODING) as yaml_file:
        return yaml.load(yaml_file, Loader=yaml_loader_class)

def _merge_in_vars(vars: TemplateVariables, new_vars: dict):
    duplicate_keys = vars.keys() & new_vars
    if len(duplicate_keys) > 0:
        first_duplicate_key = next(iter(duplicate_keys))
        raise ValueError(f'encountered duplicate variable {first_duplicate_key}')

    vars.update(new_vars)
