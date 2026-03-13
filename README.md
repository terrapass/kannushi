# kannushi
[![PyPI - Version](https://img.shields.io/pypi/v/kannushi)](https://pypi.org/project/kannushi/)
[![Python Version from PEP 621 TOML](https://img.shields.io/python/required-version-toml?tomlFilePath=https%3A%2F%2Fraw.githubusercontent.com%2Fterrapass%2Fkannushi%2Frefs%2Fheads%2Fmaster%2Fpyproject.toml)](https://www.python.org/downloads/)
[![GitHub branch check runs](https://img.shields.io/github/check-runs/terrapass/kannushi/master?logo=github)](https://github.com/terrapass/kannushi/actions?query=branch%3Amaster)


**kannushi** is a command line utility for batch rendering of [Jinja](https://jinja.palletsprojects.com/en/stable/) templates.

In a nutshell, it takes a directory containing `*.jinja` files and recursively renders those templates into a given target directory, mirroring the folder structure.

For example:
```sh
kannushi -j8 --vars "config/**/*.yml" src_templates/ src/
```
...will render Jinja template files in 8 parallel jobs (`-j8`) from `src_templates/` into `src/`, based on data from YAML files inside `config/`.\
Each rendered file will have the same name as its source template, minus the `.jinja` extension. It will also be placed at the same path relative to `src/` as its source template is relative to `src_templates/`. So, for example `src_templates/some/path/filename.ext.jinja` will be rendered into `src/some/path/filename.ext`.\
Existing files in `src/` that reside at paths corresponding to templates will be overwritten. All other files in `src/` will be left untouched.

As the above example suggests, extensive template-based code generation is the use case that **kannushi** is primarily geared towards.

---

Optionally, for cases where using static data from YAML files doesn't quite cut it, custom Python code can also be provided to **kannushi** by means of the `--vars-processor` argument, which can be used either alone or in combination with `--vars`.\
For example, suppose we have a Python file like this, called `processor.py`, in the directory where `kannushi` is run from:
```py
# processor.py
import math
...

def custom_function_exposed_to_templates(arg):
    ...

def process_vars(vars):
    """`process_vars()` will be called by kannushi before any templates are rendered.
    `vars` is a dict-like object that will utlimately be used as the context for rendering.

    If `--vars` is given, `vars` will be pre-populated with data loaded from YAML files.
    """
    # (assuming some_variable was loaded from YAML given by --vars)
    vars.some_variable_squared = vars.some_variable * vars.some_variable
    vars.utils = {
        "custom_function": custom_function_exposed_to_templates,
        "distance": math.dist
    }
    ...
```

We can have **kannushi** make use of it like so (building on the previous example):
```sh
kannushi -j8 --vars "config/**/*.yml" --vars-processor processor.py src_templates/ src/
```
In this case the dictionary of input data will be read from YAML files under `config/` and passed as the `vars` argument to the `process_vars()` function in `processor.py`, where it can undergo arbitrary modifications, before being used as the context for rendering of Jinja templates from `src_templates/`.

As seen in the `process_vars()` code example above, besides calculating some template variables on the fly, this mechanism can also be used to expose custom Python functions to the Jinja code in the rendered templates.

It's also possible to use `--vars-processor` alone, without `--vars`, provided the script's code doesn't rely on data loaded from YAML. In that case the dictionary passed as argument to `process_vars()` will start off empty and can be populated entirely by Python code.

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
  --seed RANDOM_SEED    RNG seed to use for any randomization within templates
  -j, --jobs JOBS_COUNT
                        number of parallel jobs (defaults to the number of logical
                        CPU cores)
  -v, --verbose         output processed file paths and their render times to
                        stdout
  --no-color            disable output coloring
```
