from enum import Enum
from typing import Protocol, Callable
from timeit import default_timer

#
# Enums
#

class Stage(str, Enum):
    VARS_LOADING     = "YAML variables loading"
    VARS_PROCESSING  = "Variables post-processing"
    RENDER_POOL_INIT = "Render pool initialization"
    JINJA_RENDER     = "Jinja templates rendering"

#
# Protocols
#

class ProgressListener(Protocol):
    def on_stage_started(self, stage: Stage): ...

    def on_stage_ended(self, stage: Stage, errors_count: int, was_interrupted: bool): ...

#
# Types
#

class NullProgressListener(ProgressListener):
    def on_stage_started(self, stage: Stage):
        pass

    def on_stage_ended(self, stage: Stage, errors_count: int, was_interrupted: bool):
        pass

class StageRuntimeReporter(ProgressListener):
    def __init__(self, is_verbose: bool, print_impl: Callable[[str], None] = print):
        self.__init_time_seconds         = default_timer()
        self.__stage_start_times_seconds = dict()
        self.__stage_end_times_seconds   = dict()
        self.__stage_errors_counts       = dict()
        self.__interrupted_stages        = set()
        self.__is_verbose                = is_verbose
        self.__print_impl                = print_impl

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
        self.__print_impl(f"{stage.value} {stage_verb_str} {self.__stage_time_seconds(stage):.1f} seconds{self.__format_errors_count(' with {0}', errors_count)}")

    def log_summary(self):
        total_runtime_seconds = default_timer() - self.__init_time_seconds
        self.__print_impl(f"Total runtime: {total_runtime_seconds:.1f} seconds{', stages:' if self.__is_verbose else ''}")
        if not self.__is_verbose:
            return
        for stage in Stage:
            stage_time_seconds = self.__stage_time_seconds(stage)
            if stage_time_seconds is None:
                continue
            assert stage in self.__stage_errors_counts
            stage_stats_str = self.__format_stage_stats(stage_time_seconds, self.__stage_errors_counts[stage], stage in self.__interrupted_stages)
            self.__print_impl(f"- {stage.value:<27}{stage_stats_str}")

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
        errors_count_str          = StageRuntimeReporter.__format_errors_count(errors_count_str_template, errors_count)
        secondary_stats_str       = errors_count_str if len(errors_count_str) > 0 else (" (interrupted)" if was_interrupted else "")
        return f"{stage_time_seconds:.1f}s{secondary_stats_str}"
