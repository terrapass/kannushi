import random
import signal
from pathlib import Path
from os import path, cpu_count
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, Sequence, cast
from multiprocessing import Pool
from multiprocessing.pool import AsyncResult
from functools import cached_property, partial
from sys import stdout
from timeit import default_timer

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from ..extensions import ErrorExtension
from ..exceptions import InvalidSourcePathError, TargetPathKindMismatchError
from ..timing import Stage, ProgressListener, NullProgressListener
from .._vars import TemplateVariables
from .._vars.loading import inject_service_var
from .._logging import print_verbose_success, print_warning, print_error
from .ipc import TemplateVariablesTransport, make_template_variables_transport

#
# Constants
#

SOURCE_ENCODING = 'utf-8' # Treating both source and rendered content as regular UTF-8 handles BOM correctly.
TARGET_ENCODING = 'utf-8' #

_TEMPLATE_EXTENSION = '.jinja'
_TEMPLATE_GLOB      = '**/*' + _TEMPLATE_EXTENSION

_TEMPLATE_PATH_VAR = '_template_path' # Name of the template variable to be set to the currently rendered template's path

_ASYNC_POLLING_INTERVAL_SECONDS = 0.5 # Primarily determines the reaction time to Ctrl-C (KeyboardInterrupt)

#
# Protocols
#

class RenderHandler(Protocol):
    def __call__(self, context: "RenderTemplateContext") -> Any: ...

class RenderResultObserver(Protocol):
    def __call__(self, target_file_path: Path, render_handler_result: Any): ...

#
# Interface types
#

@dataclass
class RenderTemplateContext:
    template_path:    Path
    target_dir_path:  Path
    target_file_path: Path
    rendered_content: str

    @property
    def target_file_relative_path(self) -> str:
        return _replace_backslashes(path.relpath(self.target_file_path, self.target_dir_path))

    @cached_property
    def target_current_content(self) -> str | None:
        if not self.target_file_path.is_file():
            return None
        return self.target_file_path.read_text(encoding=TARGET_ENCODING)

@dataclass
class RenderConfig:
    source_path:          Path
    target_path:          Path
    skip_glob:            str | None
    random_seed:          int | None
    requested_jobs_count: int | None

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
class RenderResult:
    selected_templates_count:    int                       = 0
    rendered_templates_count:    int                       = 0
    errors_by_target_file_path:  dict[Path, BaseException] = field(default_factory=dict)
    was_interrupted:             bool                      = False

    @property
    def errors_count(self) -> int:
        return len(self.errors_by_target_file_path)

    @property
    def skipped_count(self) -> int:
        result = self.selected_templates_count - self.rendered_templates_count - self.errors_count
        assert result >= 0
        return result

    @property
    def is_successful(self) -> bool:
        return self.selected_templates_count == self.rendered_templates_count

#
# Service types
#

@dataclass
class _RenderableTemplate:
    template_path:    Path
    template_name:    str
    target_file_path: Path

@dataclass
class _RenderTemplateResult:
    target_file_path:      Path
    render_time_seconds:   float
    render_handler_result: Any = None

class _CompositeRenderHandler:
    # A picklable (unlike a closure) callable, since the handler is sent to worker processes.
    def __init__(self, handlers: Sequence[RenderHandler]):
        self.__handlers = list(handlers)

    def __call__(self, context: RenderTemplateContext) -> tuple[Any, ...]:
        return tuple(handler(context) for handler in self.__handlers)

#
# Globals
#

_jinja_env = None
_vars      = None

#
# Interface
#

def writing_render_handler(context: RenderTemplateContext) -> None:
    context.target_file_path.parent.mkdir(exist_ok=True, parents=True)
    with open(context.target_file_path, 'w', encoding=TARGET_ENCODING) as target_file:
        target_file.write(context.rendered_content)

def composite_render_pipeline(
    steps: Sequence[tuple[RenderHandler, RenderResultObserver | None]]
) -> tuple[RenderHandler, RenderResultObserver | None]:
    """Collapses a list of (handler, observer) steps into a single (handler, observer) pair."""
    assert len(steps) >= 1
    if len(steps) == 1:
        return steps[0]

    handlers  = [handler  for handler,  _ in steps]
    observers = [observer for _, observer in steps]

    def composite_observer(target_file_path: Path, render_handler_result: Any):
        for observer, result in zip(observers, render_handler_result):
            if observer is not None:
                observer(target_file_path, result)

    return (_CompositeRenderHandler(handlers), composite_observer)

def validate_render_paths(config: RenderConfig) -> None:
    source_is_dir  = config.source_path.is_dir()
    source_is_file = config.source_path.is_file()
    if not source_is_dir and not source_is_file:
        raise InvalidSourcePathError(config.source_path)
    if config.target_path.exists():
        if source_is_dir and not config.target_path.is_dir():
            raise TargetPathKindMismatchError(config.source_path, config.target_path, source_is_dir=True)
        if source_is_file and not config.target_path.is_file():
            raise TargetPathKindMismatchError(config.source_path, config.target_path, source_is_dir=False)

def render(
    config:                 RenderConfig,
    vars:                   TemplateVariables,
    render_handler:         RenderHandler               = writing_render_handler,
    render_result_observer: RenderResultObserver | None = None,
    progress_listener:      ProgressListener            = NullProgressListener()
) -> RenderResult:
    validate_render_paths(config)
    (source_root, target_dir_path, renderable_templates) = _select_renderable_templates(config)
    return _render_templates(config, source_root, target_dir_path, renderable_templates, vars, render_handler, render_result_observer, progress_listener)

#
# Service
#

def _select_renderable_templates(config: RenderConfig) -> tuple[Path, Path, list[_RenderableTemplate]]:
    if config.source_path.is_file():
        source_root     = config.source_path.parent
        target_dir_path = config.target_path.parent
        if config.skip_glob is not None and config.source_path.match(config.skip_glob):
            renderable_templates: list[_RenderableTemplate] = []
        else:
            renderable_templates = [_RenderableTemplate(config.source_path, config.source_path.name, config.target_path)]
        return (source_root, target_dir_path, renderable_templates)

    source_root     = config.source_path
    target_dir_path = config.target_path
    skipped_paths   = set(source_root.glob(config.skip_glob)) if config.skip_glob is not None else set()
    renderable_templates = [
        _RenderableTemplate(template_path, *_convert_template_path(source_root, target_dir_path, template_path))
        for template_path in source_root.glob(_TEMPLATE_GLOB)
        if template_path not in skipped_paths
    ]
    return (source_root, target_dir_path, renderable_templates)

def _render_templates(
    config:                 RenderConfig,
    source_root:            Path,
    target_dir_path:        Path,
    renderable_templates:   list[_RenderableTemplate],
    vars:                   TemplateVariables,
    render_handler:         RenderHandler,
    render_result_observer: RenderResultObserver | None,
    progress_listener:      ProgressListener
) -> RenderResult:
    if len(renderable_templates) <= 0:
        return _handle_no_templates_to_render(config.source_path, config.skip_glob)
    if len(renderable_templates) == 1:
        return _render_templates_sequential(source_root, target_dir_path, renderable_templates, vars, render_handler, render_result_observer, config.random_seed, progress_listener)
    return _render_templates_concurrent(config, source_root, target_dir_path, renderable_templates, vars, render_handler, render_result_observer, progress_listener)

def _render_templates_sequential(
    source_root:            Path,
    target_dir_path:        Path,
    renderable_templates:   list[_RenderableTemplate],
    vars:                   TemplateVariables,
    render_handler:         RenderHandler,
    render_result_observer: RenderResultObserver | None,
    random_seed:            int | None,
    progress_listener:      ProgressListener
) -> RenderResult:
    result = RenderResult()
    result.selected_templates_count = len(renderable_templates)

    jinja_env = _make_jinja_env(source_root)
    progress_listener.on_stage_started(Stage.JINJA_RENDER)
    for renderable_template in renderable_templates:
        try:
            template_result = _render_template(
                jinja_env, vars, renderable_template.template_path, renderable_template.template_name,
                target_dir_path, renderable_template.target_file_path, render_handler, random_seed
            )
        except KeyboardInterrupt:
            result.was_interrupted = True
            break
        except Exception as e:
            _on_template_render_error(result, renderable_template.target_file_path, e)
        else:
            _on_template_render_success(result, template_result, render_result_observer)
    progress_listener.on_stage_ended(Stage.JINJA_RENDER, result.errors_count, result.was_interrupted)

    return result

def _render_templates_concurrent(
    config:                 RenderConfig,
    source_root:            Path,
    target_dir_path:        Path,
    renderable_templates:   list[_RenderableTemplate],
    vars:                   TemplateVariables,
    render_handler:         RenderHandler,
    render_result_observer: RenderResultObserver | None,
    progress_listener:      ProgressListener
) -> RenderResult:
    current_stage = None
    def change_stage(stage: Stage | None, current_stage_errors_count: int = 0, was_interrupted: bool = False):
        nonlocal current_stage
        if current_stage is not None:
            progress_listener.on_stage_ended(current_stage, current_stage_errors_count, was_interrupted)
        current_stage = stage
        if current_stage is not None:
            progress_listener.on_stage_started(current_stage)

    actual_jobs_count = min(config.effective_jobs_count, len(renderable_templates))

    print(f'Initializing {actual_jobs_count} render processes...')
    change_stage(Stage.RENDER_POOL_INIT)

    def job_success_callback(template_result: _RenderTemplateResult):
        _on_template_render_success(result, template_result, render_result_observer)

    def job_error_callback(renderable_template: _RenderableTemplate, e: BaseException):
        _on_template_render_error(result, renderable_template.target_file_path, e)

    result = RenderResult()
    result.selected_templates_count = len(renderable_templates)
    with make_template_variables_transport(vars) as vars_transport:
        with Pool(actual_jobs_count, _init_render_template_process, (source_root, vars_transport)) as process_pool:
            def render_template_async(renderable_template: _RenderableTemplate) -> AsyncResult:
                return process_pool.apply_async(
                    _render_template_job,
                    (target_dir_path, renderable_template, render_handler, config.random_seed),
                    callback=job_success_callback,
                    error_callback=cast(Callable[[BaseException], None], partial(job_error_callback, renderable_template))
                )
            try:
                async_results = [render_template_async(renderable_template) for renderable_template in renderable_templates]
                process_pool.close()

                change_stage(Stage.JINJA_RENDER)
                print(f'Rendering {len(renderable_templates)} templates in {actual_jobs_count} parallel jobs...')

                for async_result in async_results:
                    while not async_result.ready():
                        async_result.wait(_ASYNC_POLLING_INTERVAL_SECONDS)

                process_pool.join()
            except KeyboardInterrupt:
                result.was_interrupted = True
                process_pool.terminate()

    change_stage(None, result.errors_count if current_stage == Stage.JINJA_RENDER else 0, result.was_interrupted)

    return result

def _handle_no_templates_to_render(source_path: Path, skip_glob: str | None) -> RenderResult:
    print_warning(f"warning: No{' (non-skipped)' if skip_glob is not None else ''} templates to render in {source_path}", file=stdout)
    return RenderResult()

def _on_template_render_success(result: RenderResult, template_result: _RenderTemplateResult, render_result_observer: RenderResultObserver | None):
    result.rendered_templates_count += 1
    print_verbose_success(f'[{template_result.render_time_seconds:4.2f}s] {template_result.target_file_path}')
    if render_result_observer is not None:
        render_result_observer(template_result.target_file_path, template_result.render_handler_result)

def _on_template_render_error(result: RenderResult, target_file_path: Path, error: BaseException):
    assert target_file_path not in result.errors_by_target_file_path
    result.errors_by_target_file_path[target_file_path] = error
    print_error(f'[ERROR] {target_file_path}')
    print_error(f'\terror: {error}')

def _make_jinja_env(source_root: Path) -> Environment:
    return Environment(
        loader=FileSystemLoader(source_root, encoding=SOURCE_ENCODING),
        extensions=['jinja2.ext.do', ErrorExtension],
        autoescape=False,
        undefined=StrictUndefined
    )

def _init_render_template_process(source_root: Path, vars_transport: TemplateVariablesTransport):
    # Prevent Ctrl-C from raising KeyboardInterrupt in child processes
    signal.signal(signal.SIGINT, signal.SIG_IGN)

    global _jinja_env, _vars
    _jinja_env = _make_jinja_env(source_root)
    _vars      = vars_transport.retrieve_vars()

def _render_template_job(target_dir_path: Path, renderable_template: _RenderableTemplate, render_handler: RenderHandler, random_seed: int | None) -> _RenderTemplateResult:
    """This function is the entry point for individual template rendering jobs run in parallel"""

    assert isinstance(_jinja_env, Environment)
    assert isinstance(_vars, dict)

    return _render_template(
        _jinja_env, _vars, renderable_template.template_path, renderable_template.template_name,
        target_dir_path, renderable_template.target_file_path, render_handler, random_seed
    )

def _render_template(
    jinja_env:        Environment,
    vars:             TemplateVariables,
    template_path:    Path,
    template_name:    str,
    target_dir_path:  Path,
    target_file_path: Path,
    render_handler:   RenderHandler,
    random_seed:      int | None
) -> _RenderTemplateResult:
    render_start_time_seconds = default_timer()

    inject_service_var(vars, _TEMPLATE_PATH_VAR, _replace_backslashes(template_path))
    try:
        random.seed(random_seed)
        rendered_content      = _render_template_impl(jinja_env, template_name, vars)
        context               = RenderTemplateContext(template_path, target_dir_path, target_file_path, rendered_content)
        render_handler_result = render_handler(context)
    finally:
        del vars[_TEMPLATE_PATH_VAR]

    return _RenderTemplateResult(target_file_path, default_timer() - render_start_time_seconds, render_handler_result)

def _render_template_impl(jinja_env: Environment, template_name: str, vars: dict) -> str:
    template = jinja_env.get_template(template_name)
    return template.render(vars)

def _convert_template_path(source_dir_path: Path, target_dir_path: Path, template_path: Path) -> tuple[str, Path]:
    template_name    = _source_template_path_to_name(source_dir_path, template_path)
    target_file_path = _template_name_to_target_file_path(target_dir_path, template_name)
    return (template_name, target_file_path)

def _source_template_path_to_name(source_dir_path: Path, template_path: Path) -> str:
    return _replace_backslashes(path.relpath(template_path, source_dir_path))

def _replace_backslashes(path_str: str | Path) -> str:
    return str(path_str).replace('\\', '/')

def _template_name_to_target_file_path(target_dir_path: Path, template_name: str) -> Path:
    assert template_name.endswith(_TEMPLATE_EXTENSION)
    return target_dir_path / template_name[:-len(_TEMPLATE_EXTENSION)]
