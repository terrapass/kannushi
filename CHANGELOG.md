# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased][unreleased]

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

[unreleased]: https://github.com/terrapass/kannushi/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/terrapass/kannushi/compare/v0.1.2...v0.2.0
[0.1.2]: https://github.com/terrapass/kannushi/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/terrapass/kannushi/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/terrapass/kannushi/releases/tag/v0.1.0
