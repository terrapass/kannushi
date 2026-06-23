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
