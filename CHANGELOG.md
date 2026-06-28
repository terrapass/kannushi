# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

<!-- ## [Unreleased][unreleased] -->

## [1.0.0-rc.4][1.0.0-rc.4] - 2026-06-28

### Added

- Single-template mode: `SOURCE_PATH` and `TARGET_PATH` may now be regular files, rendering one template to one output file. The source file may have any name; in this mode `--skip` is still honored (the file is skipped if it matches). If `TARGET_PATH` already exists, its kind (file vs. directory) must match `SOURCE_PATH`'s.
- Support for the `-e` (`--ext`) CLI argument, which sets the template file extension to look for when `SOURCE_PATH` is a directory (the argument is ignored otherwise). Defaults to `jinja`.
- `validate_render_paths` to the package API, to verify that `SOURCE_PATH` and `TARGET_PATH` are valid without invoking `render`.
- 3 new exceptions associated with paths validation: `RenderPathError`, `InvalidSourcePathError` and `TargetPathKindMismatchError` (all in `kannushi.exceptions`).
- `template_extension` field (defaults to `jinja`) in `RenderConfig`.

### Changed

- The public `render_dir` function has been replaced by a unified `render`, which validates the source/target paths and then renders either a single file or a directory tree.
- A `TARGET_PATH` that already exists with a kind not matching `SOURCE_PATH` (e.g. a file target for a directory source) is now reported with a dedicated error, generalizing the previous "target is not a directory" check.
- If there is only a single template to render, it is now rendered directly without spawning parallel render workers.

### Removed

- The public `render_dir` function and `RenderDirResult` type (replaced by `render` and `RenderResult` respectively).
- The `--seed` CLI argument and the `random_seed` field of `RenderConfig`. (In cases where templates involve RNG calls, seeding can be accomplished by means of `--vars-processor`.)

## [1.0.0-rc.3][1.0.0-rc.3] - 2026-06-24

### Added

- Support for the `--ignore-absent-vars-files` CLI argument. When given, a `--vars` glob that matches no files is tolerated (a warning is logged and rendering proceeds with no variables) instead of being treated as an error.

### Changed

- Renamed `post_process_vars` to `pre_process_vars`.
- Generally resolved vars pre-/post-processing terminology ambiguity in favor of "pre-processing" - across the codebase and CLI output, including `--help`.
- A `--vars` glob that matches no files is now treated as an error, unless an explicit `--ignore-absent-vars-files` CLI argument is given.
- The tool now exits with an error if `SOURCE_PATH` does not exist or is not a directory.
- The tool now exits with an error if `TARGET_PATH` is a regular file.
- A warning is now logged if `TARGET_PATH` does not exist. (It will still get created in the absence of `--check`.)
- A warning is now logged and the tool exits early when `SOURCE_PATH` contains no (non-skipped) templates.
- The tool will no longer spawn more render worker processes than there are templates to render, regardless of `--jobs`.
- Verbose per-file render times are now printed in blue.

## [1.0.0-rc.2][1.0.0-rc.2] - 2026-06-22

### Added

- Support for the `-V` (`--version`) CLI argument, which prints the program name and version and exits.

## [1.0.0-rc.1][1.0.0-rc.1] - 2026-06-22

### Changed

- Sped up render pool initialization stage for highly parallelized cases (high/default `--jobs` value on high CPU core count systems).
- On Linux the pre-3.14 default of [`fork` start method](https://docs.python.org/3.14/library/multiprocessing.html#multiprocessing-start-methods) for `multiprocessing` is now forced in CLI on Python 3.14+ (does not affect **kannushi**'s public package API behavior).
- On other platforms (Windows, macOS) template variables are now communicated to the render worker processes via shared memory instead of being pickled separately for each worker, thus reducing render pool initialization overhead for high parallel job count cases.

### Fixed

- Template variables whose name coincides with a `dict` method name (e.g. `items`, `keys`, `update`) no longer break rendering on Linux (Python 3.14+) and other platforms.

## [0.8.0][0.8.0] - 2026-06-21

### Changed

- The `--log` YAML output now includes an `input` section echoing the run's arguments: `source_path`, `target_path`, `vars_glob`, `skip_glob`, `vars_processor` and `vars_processor_func` (the optional ones are `null` when not given; `vars_processor_func` is `null` in the absence of `vars_processor`).
- `--verbose` output is now dimmed (unless `--no-color` was given).

### Removed

- The `is_verbose` and `is_color_disabled` fields from `RenderConfig`.

## [0.7.0][0.7.0] - 2026-06-20

### Added

- Support for the `--diff` CLI argument. When given, the tool creates a file at the given path, containing a unified diff between the current target files and their newly rendered coutent. It can be combined with `--check` or used in its absence. Missing target files are included in the diff as created. If all target files are up to date, an empty diff file is created.
- Support for the `--junit` CLI argument, which can be used only in combination with `--check` to write a JUnit XML report - primarily intended to be used as `artifacts:junit` job output in GitLab CI. The following "test cases" are recorded: `vars_read_successful`, `vars_processing_successful`, `no_jinja_render_errors`, `all_target_files_exist`, `existing_target_files_current`, `all_target_files_current`. This argument is only supported when the optional `junit` extra is installed (`pip install "kannushi[junit]"`).
- Package API now exposes `RenderTemplateContext`, `composite_render_pipeline`, `make_diff_render_pipeline_step`, `TargetDiff` and `DiffRenderResultObserver`.

### Changed

- Render handlers now receive a single `RenderTemplateContext` argument (replacing the separate target path and rendered content parameters).

### Removed

- `verification_render_handler` and `verification_render_result_observer` - their role is now covered by `make_diff_render_pipeline_step` and `DiffRenderResultObserver`.
- `render_handler_results` collection from `RenderDirResult`.

## [0.6.0][0.6.0] - 2026-04-14

### Added

- Support for `--log` CLI argument. If given, the tool, when exiting, will output a YAML log file at the path specified as the argument's value. The log will contain errors (if any) from the vars loading and processing steps, as well as render and render errors summmary and, in the presence of `--check`, verification results. The file is not written, if the run gets interrupted by the user (via `SIGINT`/Ctrl-C). If writing of the log fails for any reason, a warning will be written to stderr, but the application's exit code will remain unchanged.

## [0.5.0][0.5.0] - 2026-04-04

### Changed

- Render errors in `--check` mode are now also considered verification errors and are included in verification result logging.
- A warning is now logged instead of the verification summary in case of user interruption in `--check` mode.
- List of render errors at the end of the run now follows the same non-verbose cutoff ("and X more") logic as modified/missing files lists from verification result logs.
- Minor log wording and formatting changes.
- `RenderDirResult` now exposes `errors_by_target_file_path` dict (mapping `Path` to `BaseException`) instead of the `failed_template_paths` list.

## [0.4.1][0.4.1] - 2026-04-04

### Added

- Documented `--check` mode in `README.md`.

### Changed

- Significantly restructured `README.md`, added headings.

## [0.4.0][0.4.0] - 2026-04-04

### Added

- Support for `--check` CLI argument. In this mode the tool doesn't write anything to disk but simply verifies that target files are consistent with their source templates, i.e. that all of them already exist and none contain "manual" modifications or are otherwise out of date relative to freshly rendered templates; if this is not the case, the tool logs any inconsistencies found to stderr and exits non-zero.
- Package API now exposes `RenderHandler` and `RenderResultObserver` protocols, `TargetFileStatus` enum, as well as `writing_render_handler`, `verification_render_handler` and `verification_render_handler_result_observer` functions.

## [0.3.0][0.3.0] - 2026-03-13

### Added

- `{% error %}` tag message now also includes the template name and line number.
- Package API now exposes the custom `extensions.ErrorExtension`.

### Changed

- Switched to a custom implementation of the `{% error %}` tag extension, instead of the one provided by `jinja2-error`.

### Removed

- Removed the dependency on `jinja2-error` (which transitively also removes `arrow`, `python-dateutil`. `tzdata`, `six` dependencies).

## [0.2.1][0.2.1] - 2026-03-09

### Added

- An additional usage example demonstrating `--vars-processor` in README.md .

### Changed

- Made minor updates to `--help` output for `--seed` and `--verbose` CLI options.
- Updated project description on PyPI.

## [0.2.0][0.2.0] - 2026-03-08

### Fixed

- Fixed the issue where a rendered file would fail to be created if any of its target path's parent directories don't exist.

### Changed

- The `kannushi` package API has been narrowed to only expose the minimum that would be sufficient for implementing an alternative CLI or GUI. Implementation details and standard/thirdparty reimports are no longer exposed by the package interface.
- Dependency version requirements: Jinja2 to `>=3.0.0,<4.0.0`; PyYAML to `>=3.0.0,<4.0.0`; jinja2-error to `~=0.1.0`.
- Program name is now displayed as `kannushi` in usage info and `--help` output.

### Added

- MIT license in [`LICENSE`](https://github.com/terrapass/kannushi/blob/master/LICENSE).
- Extended README.md with a basic usage example and explanation of the tool's purpose, as well as installation commands for `pip` and `uv`.

### Removed

- The `_program_name` service variable is no longer exposed to rendered templates.

## [0.1.2][0.1.2] - 2026-03-07

### Fixed

- Fixed the issue on Python v3.13 and older where `NameError` would be raised on launch.

## [0.1.1][0.1.1] - 2026-03-06

### Fixed

- Fixed the issue where color output would be printed in spite of the `--no-color` argument being provided.

### Added

- [CHANGELOG.md](https://github.com/terrapass/kannushi/blob/master/CHANGELOG.md) URL to package metadata.

## [0.1.0][0.1.0] - 2026-03-06

Initial public release of **kannushi** - a command line utility for batch rendering of [Jinja](https://jinja.palletsprojects.com/en/stable/) templates.

<sup>Originally developed as part of internal tooling used by the modding team working on [Godherja: The Dying World](https://steamcommunity.com/workshop/filedetails/?id=2326030123) - a total conversion mod for Crusader Kings III.</sup>

### Added

- CLI allowing to render Jinja templates from a source directory into a target directory, preserving folder structure.
- Multiple templates are rendered in parallel by means of Python's standard `multiprocessing` package.
- `-j` (`--jobs`) argument controls the number of rendering processes, defaults to the number of logical CPU cores (as obtained by `sys.cpu_count()`).
- Variables for rendering can be loaded from YAML file(s) given by the `--vars` argument (may be a glob e.g. `variables/**/*.yml`).
- Additional post-processing logic can be run on the variables dictionary by means of an arbitrary Python file/module given by `--vars-processor`.
- Helper (include/import-only) templates can be excluded from rendering by means of a glob given as `--skip`.
- RNG seed can be fixed for template rendering by means of `--seed` for deterministic output (useful if `--vars-processor` exposes RNG-based utilities to templates).
- `Jinja2`'s [`StrictUndefined`](https://jinja.palletsprojects.com/en/stable/api/#jinja2.StrictUndefined) is used for undefined values in templates to produce an error if a template tries to make use of an undefined variable.
- [`{% do %}` expression statements](https://jinja.palletsprojects.com/en/stable/extensions/#expression-statement) are supported in templates by means of the standard `jinja2.ext.do` Jinja2 extension.
- [`jinja2-error`](https://pypi.org/project/jinja2-error/) extension is integrated to allow for use of the `{% error %}` Jinja tag to raise errors from template code.

[unreleased]: https://github.com/terrapass/kannushi/compare/v1.0.0-rc.4...HEAD
[1.0.0-rc.4]: https://github.com/terrapass/kannushi/compare/v1.0.0-rc.3...v1.0.0-rc.4
[1.0.0-rc.3]: https://github.com/terrapass/kannushi/compare/v1.0.0-rc.2...v1.0.0-rc.3
[1.0.0-rc.2]: https://github.com/terrapass/kannushi/compare/v1.0.0-rc.1...v1.0.0-rc.2
[1.0.0-rc.1]: https://github.com/terrapass/kannushi/compare/v0.8.0...v1.0.0-rc.1
[0.8.0]: https://github.com/terrapass/kannushi/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/terrapass/kannushi/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/terrapass/kannushi/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/terrapass/kannushi/compare/v0.4.1...v0.5.0
[0.4.1]: https://github.com/terrapass/kannushi/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/terrapass/kannushi/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/terrapass/kannushi/compare/v0.2.1...v0.3.0
[0.2.1]: https://github.com/terrapass/kannushi/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/terrapass/kannushi/compare/v0.1.2...v0.2.0
[0.1.2]: https://github.com/terrapass/kannushi/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/terrapass/kannushi/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/terrapass/kannushi/releases/tag/v0.1.0
