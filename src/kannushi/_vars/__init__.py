
#
# Types
#

# A convenience wrapper around dict, allowing to acess values
# both by key via [] or as regular attributes via dot notation.
class TemplateVariables(dict):
     def __init__(self, vars: dict = {}):
         super().__init__(vars)
         self.__dict__ = self
