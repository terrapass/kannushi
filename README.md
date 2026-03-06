# kannushi
[![PyPI - Version](https://img.shields.io/pypi/v/kannushi)](https://pypi.org/project/kannushi/)

**kannushi** is a command line utility for batch rendering of [Jinja](https://jinja.palletsprojects.com/en/stable/) templates from one directory into files in another directory.

# Synopsis
```
usage: python -m kannushi [-h] [--skip SKIP_GLOB] [--vars VARS_YAML_GLOB]
                              [--vars-processor VARS_PROCESSOR_MODULE]
                              [--vars-processor-func VARS_PROCESSOR_FUNCTION]
                              [--seed RANDOM_SEED] [-j JOBS_COUNT] [-v]
                              [--no-color]
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
