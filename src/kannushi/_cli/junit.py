from pathlib import Path
from dataclasses import dataclass
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

@dataclass
class JunitReport:
    vars_loading_requested:    bool                         = False
    vars_loading_error:        str | None                   = None
    vars_loading_elapsed:      float | None                 = None
    vars_processing_requested: bool                         = False
    vars_processing_error:     str | None                   = None
    vars_processing_elapsed:   float | None                 = None
    render_result:             RenderDirResult | None       = None
    render_elapsed:            float | None                 = None
    verification_result:       "_VerificationResult | None" = None

def write_junit_report_to_xml_file(report: JunitReport, xml_file_path: Path):
    assert junit_xml is not None, "write_junit_report_to_xml_file() requires the 'junit' extra to be installed"
    test_suite = junit_xml.TestSuite(_SUITE_NAME, _make_test_cases(report))
    with open(xml_file_path, 'w', encoding=_REPORT_ENCODING) as xml_file:
        junit_xml.to_xml_report_file(xml_file, [test_suite], encoding=_REPORT_ENCODING)

#
# Service
#

def _make_test_cases(report: JunitReport) -> list:
    vars_loading_failed = report.vars_loading_error is not None
    any_vars_failed     = vars_loading_failed or report.vars_processing_error is not None
    render_ran          = report.render_result is not None
    render_failed       = render_ran and report.render_result.errors_count > 0 # type: ignore[union-attr]

    consistency_skip_reason = _consistency_skip_reason(any_vars_failed, render_ran, render_failed, report.verification_result is not None)

    return [
        _vars_read_successful(report, vars_loading_failed),
        _vars_processing_successful(report, vars_loading_failed),
        _no_jinja_render_errors(report, any_vars_failed, render_ran, render_failed),
        _all_target_files_exist(consistency_skip_reason, report.verification_result),
        _existing_target_files_current(consistency_skip_reason, report.verification_result),
        _all_target_files_current(consistency_skip_reason, report.verification_result),
    ]

#
# Test cases
#

def _vars_read_successful(report: JunitReport, vars_loading_failed: bool):
    test_case = _make_test_case('vars_read_successful', report.vars_loading_elapsed)
    if not report.vars_loading_requested:
        test_case.add_skipped_info('no --vars given; variables loading was not performed')
    elif vars_loading_failed:
        test_case.add_failure_info(report.vars_loading_error)
    return test_case

def _vars_processing_successful(report: JunitReport, vars_loading_failed: bool):
    test_case = _make_test_case('vars_processing_successful', report.vars_processing_elapsed)
    if vars_loading_failed:
        test_case.add_skipped_info('skipped because variables loading failed')
    elif not report.vars_processing_requested:
        test_case.add_skipped_info('no --vars-processor given; variables post-processing was not performed')
    elif report.vars_processing_error is not None:
        test_case.add_failure_info(report.vars_processing_error)
    return test_case

def _no_jinja_render_errors(report: JunitReport, any_vars_failed: bool, render_ran: bool, render_failed: bool):
    test_case = _make_test_case('no_jinja_render_errors', report.render_elapsed)
    if any_vars_failed or not render_ran:
        test_case.add_skipped_info('skipped because rendering did not run')
    elif render_failed:
        assert report.render_result is not None
        test_case.add_failure_info(_format_render_errors(report.render_result))
    return test_case

def _all_target_files_exist(skip_reason: str | None, verification: "_VerificationResult | None"):
    test_case = _make_test_case('all_target_files_exist', None)
    if skip_reason is not None:
        test_case.add_skipped_info(skip_reason)
    else:
        assert verification is not None
        if verification.missing_files_count > 0:
            test_case.add_failure_info(_format_paths(verification.missing_file_paths))
    return test_case

def _existing_target_files_current(skip_reason: str | None, verification: "_VerificationResult | None"):
    test_case = _make_test_case('existing_target_files_current', None)
    if skip_reason is not None:
        test_case.add_skipped_info(skip_reason)
    else:
        assert verification is not None
        if verification.modified_files_count > 0:
            test_case.add_failure_info(_format_paths(verification.modified_file_paths))
    return test_case

def _all_target_files_current(skip_reason: str | None, verification: "_VerificationResult | None"):
    test_case = _make_test_case('all_target_files_current', None)
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

def _consistency_skip_reason(any_vars_failed: bool, render_ran: bool, render_failed: bool, verification_ran: bool) -> str | None:
    if any_vars_failed or not render_ran:
        return 'skipped because rendering did not run'
    if render_failed:
        return 'skipped because some templates failed to render (consistency is inconclusive)'
    if not verification_ran:
        return 'skipped because consistency was not checked (run with --check)'
    return None

def _make_test_case(name: str, elapsed_sec: float | None):
    assert junit_xml is not None
    return junit_xml.TestCase(name, classname=_TEST_CASE_CLASSNAME, elapsed_sec=elapsed_sec)

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
