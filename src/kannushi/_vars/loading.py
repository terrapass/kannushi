import signal
from enum import Enum
from pathlib import Path
from typing import Any
from multiprocessing import Pool
from itertools import repeat

import glob
import yaml

from ..timing import Stage, ProgressListener, NullProgressListener
from ..exceptions import NoVarsFilesMatchedError
from .._logging import print_warning

from . import TemplateVariables

#
# Constants
#

_VARS_ENCODING = 'utf-8' # This correctly handles UTF-8 with or without BOM for YAML files with variables

class VarsDuplicatesPolicy(Enum):
    REJECT = 'reject'
    MERGE  = 'merge'

DEFAULT_VARS_DUPLICATES_POLICY = VarsDuplicatesPolicy.REJECT

#
# Interface
#

def load_vars_from_yaml_files(
    vars_files_glob:     str,
    jobs_count:          int,
    ignore_absent_files: bool                  = False,
    duplicates_policy:   VarsDuplicatesPolicy  = DEFAULT_VARS_DUPLICATES_POLICY,
    progress_listener:   ProgressListener      = NullProgressListener()
) -> TemplateVariables:
    var_files_paths = glob.glob(vars_files_glob, recursive=True)
    var_files_count = len(var_files_paths)

    if var_files_count <= 0:
        if not ignore_absent_files:
            raise NoVarsFilesMatchedError(vars_files_glob)
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
            _merge_in_vars(vars, vars_part, duplicates_policy)

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

def _merge_in_vars(vars: TemplateVariables, new_vars: dict, duplicates_policy: VarsDuplicatesPolicy):
    for key, new_value in new_vars.items():
        if key not in vars:
            vars[key] = new_value
        elif duplicates_policy is VarsDuplicatesPolicy.MERGE:
            vars[key] = _deep_merge_vars(vars[key], new_value, key)
        else:
            assert duplicates_policy == VarsDuplicatesPolicy.REJECT
            raise ValueError(f'encountered duplicate variable {key}')

def _deep_merge_vars(vars: Any, new_vars: Any, path: str) -> Any:
    if isinstance(vars, list) and isinstance(new_vars, list):
        return vars + new_vars
    if isinstance(vars, dict) and isinstance(new_vars, dict):
        for key, new_value in new_vars.items():
            vars[key] = _deep_merge_vars(vars[key], new_value, f'{path}.{key}') if key in vars else new_value
        return vars
    (vars_type_name, new_vars_type_name) = (type(vars).__name__, type(new_vars).__name__)
    if vars_type_name != new_vars_type_name:
        raise ValueError(f'cannot merge ambiguously typed variable {path}: seen as both {vars_type_name} and {new_vars_type_name}')
    raise ValueError(f'duplicate variable {path} is of non-mergeable type {vars_type_name}')
