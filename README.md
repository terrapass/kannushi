# kannushi
[![PyPI - Version](https://img.shields.io/pypi/v/kannushi)](https://pypi.org/project/kannushi/)
[![Python Version from PEP 621 TOML](https://img.shields.io/python/required-version-toml?tomlFilePath=https%3A%2F%2Fraw.githubusercontent.com%2Fterrapass%2Fkannushi%2Frefs%2Fheads%2Fmaster%2Fpyproject.toml)](https://www.python.org/downloads/)


**kannushi** is a command line utility for batch rendering of [Jinja2](https://jinja.palletsprojects.com/en/stable/) templates.

In a nutshell, it takes a directory containing `*.jinja` files and recursively renders those templates into a given target directory, mirroring the folder structure.

For example:
```sh
kannushi -j8 --vars "config/**/*.yml" src_templates/ src/
```
...will render Jinja template files in 8 parallel jobs (`-j8`) from `src_templates/` into `src/`, based on data from YAML files inside `config/`.\
Each rendered file will have the same name as its source template, minus the `.jinja` extension. It will also be placed at the same path relative to `src/` as its source template is relative to `src_templates/`. So, for example `src_templates/some/path/filename.ext.jinja` will be rendered into `src/some/path/filename.ext`.\
Existing files in `src/` that reside at paths corresponding to templates will be overwritten. All other files in `src/` will be left untouched.

As the above example suggests, extensive template-based code generation is the use case that **kannushi** is primarily geared towards.

While there are several existing solutions for rendering Jinja in the command line, notably [`jinja2-cli`](https://pypi.org/project/jinja2-cli/) and [`j2cli`](https://pypi.org/project/j2cli/)<sup>(unmaintained)</sup>, their interfaces typically deal with individual template files - so some additional scripting would be involved in cases where rendering of an entire directory structure is required.

## Installation

Via `pip`:
```sh
pip install kannushi
```

Via `uv`:
```sh
uv tool install kannushi
```

## Synopsis
```
usage: kannushi [-h] [--skip SKIP_GLOB] [--vars VARS_YAML_GLOB]
                [--vars-processor VARS_PROCESSOR_MODULE]
                [--vars-processor-func VARS_PROCESSOR_FUNCTION]
                [--seed RANDOM_SEED] [-j JOBS_COUNT] [-v] [--no-color]
                SOURCE_PATH TARGET_PATH

Renders all Jinja templates in a directory into files in another directory,
preserving the folder structure. Templates must use UTF-8 (with or without BOM),
rendered files will reflect their source templates' BOM or lack thereof.

positional arguments:
  SOURCE_PATH           root directory containing Jinja templates
  TARGET_PATH           target root directory for rendered files

options:
  -h, --help            show this help message and exit
  --skip SKIP_GLOB      glob for template files to skip when rendering (relative to
                        SOURCE_PATH)
  --vars VARS_YAML_GLOB
                        YAML file(s) containing template variable definitions
  --vars-processor VARS_PROCESSOR_MODULE
                        Python file/module to use for variables dictionary post-
                        processing
  --vars-processor-func VARS_PROCESSOR_FUNCTION
                        single-parameter function in VARS_PROCESSOR_MODULE which
                        vars dictionary will be passed to (defaults to
                        process_vars)
  --seed RANDOM_SEED    RNG seed to use for any randomization
  -j, --jobs JOBS_COUNT
                        number of parallel jobs (defaults to the number of logical
                        CPU cores)
  -v, --verbose         output processed file paths to stdout
  --no-color            disable output coloring
  ```
