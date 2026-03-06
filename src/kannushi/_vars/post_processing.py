import importlib
import importlib.util
from pathlib import Path
from types import ModuleType
from sys import modules as sys_modules, path as sys_path

from ..exceptions import InvalidVarsProcessorInterface, ModuleExecutionException
from ..timing import Stage, ProgressListener, NullProgressListener
from .._logging import print_warning

from . import TemplateVariables

def post_process_vars(
        vars:                          TemplateVariables,
        vars_processor_module_locator: str,
        vars_processor_function_name:  str,
        progress_listener:             ProgressListener = NullProgressListener()
    ):
    vars_processor_module = load_module(vars_processor_module_locator)
    assert isinstance(vars_processor_module, ModuleType)

    if not hasattr(vars_processor_module, vars_processor_function_name):
        raise InvalidVarsProcessorInterface(vars_processor_module_locator, vars_processor_function_name)
    vars_processor_function = vars_processor_module.__getattribute__(vars_processor_function_name)
    if not callable(vars_processor_function):
        raise InvalidVarsProcessorInterface(vars_processor_module_locator, vars_processor_function_name)

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
        raise ModuleExecutionException(e)

    return module