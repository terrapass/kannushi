__all__ = [
    "TemplateVariables"
]

from typing import Any

#
# Types
#

# A convenience wrapper around dict, allowing values to be accessed
# both by key via [] and as attributes via dot notation.
#
# Attribute access proxies to the underlying dict, so real dict methods
# (items, keys, update, ...) are not shadowed on the Python side.
# In Jinja templates (with TemplateVariables as context) values are accessed by key.
class TemplateVariables(dict):
    def __init__(self, vars: dict = {}):
        super().__init__(vars)

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name)

    def __setattr__(self, name: str, value: Any) -> None:
        self[name] = value

    def __delattr__(self, name: str) -> None:
        try:
            del self[name]
        except KeyError:
            raise AttributeError(name)
