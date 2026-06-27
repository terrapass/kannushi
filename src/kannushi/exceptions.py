from pathlib import Path

#
# Types
#

class ModuleExecutionException(ImportError):
    def __init__(self, original_exception: BaseException):
        self.original_exception = original_exception

class InvalidVarsProcessorInterface(Exception):
    def __init__(self, vars_processor_module_locator: str, vars_processor_function_name: str):
        super().__init__(f"module '{vars_processor_module_locator}' does not expose the required {vars_processor_function_name}(vars: TemplateVariables) function")

class NoVarsFilesMatchedError(Exception):
    def __init__(self, vars_files_glob: str):
        super().__init__(f"{vars_files_glob} didn't match any files")
        self.vars_files_glob = vars_files_glob

class RenderPathError(Exception):
    pass

class InvalidSourcePathError(RenderPathError):
    def __init__(self, source_path: Path):
        super().__init__(f"source path {source_path} does not exist or is neither a file nor a directory")
        self.source_path = source_path

class TargetPathKindMismatchError(RenderPathError):
    def __init__(self, source_path: Path, target_path: Path, source_is_dir: bool):
        source_path_kind_str = 'directory' if source_is_dir else 'file'
        super().__init__(
            f"target path {target_path} already exists but is not a {source_path_kind_str}, "
            f"so source {source_path_kind_str} {source_path} cannot be rendered to this path"
        )
        self.source_path   = source_path
        self.target_path   = target_path
        self.source_is_dir = source_is_dir
