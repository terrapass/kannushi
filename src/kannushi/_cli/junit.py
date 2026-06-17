from pathlib import Path
from typing import TYPE_CHECKING

from .._rendering import RenderDirResult

if TYPE_CHECKING:
    from .main import _VerificationResult

try:
    import junit_xml
    IS_JUNIT_AVAILABLE = True
except ImportError:
    junit_xml           = None
    IS_JUNIT_AVAILABLE = False

#
# Constants
#

_SUITE_NAME          = 'kannushi'
_TEST_CASE_CLASSNAME = 'kannushi.check'

_REPORT_ENCODING = 'utf-8'

#
# Interface
#

class JunitReportWriter:
    def __init__(self):
        self.__vars_loading_requested:    bool                       = False
        self.__vars_loading_error:        str | None                 = None
        self.__vars_loading_elapsed:      float | None               = None
        self.__vars_processing_requested: bool                       = False
        self.__vars_processing_error:     str | None                 = None
        self.__vars_processing_elapsed:   float | None               = None
        self.__render_result:             RenderDirResult | None     = None
        self.__render_elapsed:            float | None               = None
        self.__verification_result:       _VerificationResult | None = None

    def with_vars_loading(self, *, requested: bool, error: str | None, elapsed_sec: float | None = None) -> "JunitReportWriter":
        self.__vars_loading_requested = requested
        self.__vars_loading_error     = error
        self.__vars_loading_elapsed   = elapsed_sec
        return self

    def with_vars_processing(self, *, requested: bool, error: str | None, elapsed_sec: float | None = None) -> "JunitReportWriter":
        self.__vars_processing_requested = requested
        self.__vars_processing_error     = error
        self.__vars_processing_elapsed   = elapsed_sec
        return self

    def with_rendering(self, result: RenderDirResult | None, *, elapsed_sec: float | None = None) -> "JunitReportWriter":
        self.__render_result  = result
        self.__render_elapsed = elapsed_sec
        return self

    def with_verification(self, result: "_VerificationResult | None") -> "JunitReportWriter":
        self.__verification_result = result
        return self

    def write(self, junit_xml_path: Path):
        assert junit_xml is not None, "JunitReportWriter.write() requires the 'junit' extra to be installed"
        test_suite = junit_xml.TestSuite(_SUITE_NAME, self.__make_test_cases())
        with open(junit_xml_path, 'w', encoding=_REPORT_ENCODING) as junit_xml_file:
            junit_xml.to_xml_report_file(junit_xml_file, [test_suite], encoding=_REPORT_ENCODING)

    def __make_test_cases(self) -> list:
        vars_loading_failed = self.__vars_loading_error is not None
        any_vars_failed     = vars_loading_failed or self.__vars_processing_error is not None
        render_ran          = self.__render_result is not None
        render_failed       = render_ran and self.__render_result.errors_count > 0 # type: ignore[union-attr]
        verification        = self.__verification_result

        consistency_skip_reason = self.__consistency_skip_reason(any_vars_failed, render_ran, render_failed, verification is not None)

        return [
            self.__vars_read_successful(vars_loading_failed),
            self.__vars_processing_successful(vars_loading_failed),
            self.__no_jinja_render_errors(any_vars_failed, render_ran, render_failed),
            self.__all_target_files_exist(consistency_skip_reason, verification),
            self.__existing_target_files_current(consistency_skip_reason, verification),
            self.__all_target_files_current(consistency_skip_reason, verification),
        ]

    #
    # Test cases
    #

    def __vars_read_successful(self, vars_loading_failed: bool):
        test_case = self.__make_test_case('vars_read_successful', self.__vars_loading_elapsed)
        if not self.__vars_loading_requested:
            test_case.add_skipped_info('no --vars given; variables loading was not performed')
        elif vars_loading_failed:
            test_case.add_failure_info(self.__vars_loading_error)
        return test_case

    def __vars_processing_successful(self, vars_loading_failed: bool):
        test_case = self.__make_test_case('vars_processing_successful', self.__vars_processing_elapsed)
        if vars_loading_failed:
            test_case.add_skipped_info('skipped because variables loading failed')
        elif not self.__vars_processing_requested:
            test_case.add_skipped_info('no --vars-processor given; variables post-processing was not performed')
        elif self.__vars_processing_error is not None:
            test_case.add_failure_info(self.__vars_processing_error)
        return test_case

    def __no_jinja_render_errors(self, any_vars_failed: bool, render_ran: bool, render_failed: bool):
        test_case = self.__make_test_case('no_jinja_render_errors', self.__render_elapsed)
        if any_vars_failed or not render_ran:
            test_case.add_skipped_info('skipped because rendering did not run')
        elif render_failed:
            assert self.__render_result is not None
            test_case.add_failure_info(_format_render_errors(self.__render_result))
        return test_case

    def __all_target_files_exist(self, skip_reason: str | None, verification: "_VerificationResult | None"):
        test_case = self.__make_test_case('all_target_files_exist', None)
        if skip_reason is not None:
            test_case.add_skipped_info(skip_reason)
        else:
            assert verification is not None
            if verification.missing_files_count > 0:
                test_case.add_failure_info(_format_paths(verification.missing_file_paths))
        return test_case

    def __existing_target_files_current(self, skip_reason: str | None, verification: "_VerificationResult | None"):
        test_case = self.__make_test_case('existing_target_files_current', None)
        if skip_reason is not None:
            test_case.add_skipped_info(skip_reason)
        else:
            assert verification is not None
            if verification.modified_files_count > 0:
                test_case.add_failure_info(_format_paths(verification.modified_file_paths))
        return test_case

    def __all_target_files_current(self, skip_reason: str | None, verification: "_VerificationResult | None"):
        test_case = self.__make_test_case('all_target_files_current', None)
        if skip_reason is not None:
            test_case.add_skipped_info(skip_reason)
        else:
            assert verification is not None
            if not verification.is_successful:
                test_case.add_failure_info(_format_inconsistencies(verification))
        return test_case

    #
    # Service
    #

    @staticmethod
    def __consistency_skip_reason(any_vars_failed: bool, render_ran: bool, render_failed: bool, verification_ran: bool) -> str | None:
        if any_vars_failed or not render_ran:
            return 'skipped because rendering did not run'
        if render_failed:
            return 'skipped because some templates failed to render (consistency is inconclusive)'
        if not verification_ran:
            return 'skipped because consistency was not checked (run with --check)'
        return None

    @staticmethod
    def __make_test_case(name: str, elapsed_sec: float | None):
        assert junit_xml is not None
        return junit_xml.TestCase(name, classname=_TEST_CASE_CLASSNAME, elapsed_sec=elapsed_sec)

#
# Service
#

def _format_render_errors(render_result: RenderDirResult) -> str:
    return '\n'.join(f"{path}: {error}" for path, error in render_result.errors_by_target_file_path.items())

def _format_paths(paths: list[Path]) -> str:
    return '\n'.join(str(path) for path in paths)

def _format_inconsistencies(verification: "_VerificationResult") -> str:
    parts = []
    if verification.missing_files_count > 0:
        parts.append(f"{verification.missing_files_count} missing:\n{_format_paths(verification.missing_file_paths)}")
    if verification.modified_files_count > 0:
        parts.append(f"{verification.modified_files_count} modified/out-of-date:\n{_format_paths(verification.modified_file_paths)}")
    return '\n\n'.join(parts)
