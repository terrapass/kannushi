from typing import List, NoReturn

from jinja2 import nodes
from jinja2.ext import Extension
from jinja2.parser import Parser
from jinja2.exceptions import TemplateRuntimeError

class ErrorExtension(Extension):
    tags = {"error"}

    def parse(self, parser: Parser) -> nodes.Node | List[nodes.Node]:
        lineno  = next(parser.stream).lineno
        message = parser.parse_expression()
        args    = [nodes.Const(parser.name), nodes.Const(lineno), message]
        return nodes.CallBlock(
            self.call_method("_raise_error", args), [], [], []
        ).set_lineno(lineno)

    def _raise_error(self, name: str, lineno: int, message: str, *args, **kwargs) -> NoReturn:
        raise TemplateRuntimeError(f"{message}\n\tat line {lineno} in {name}")
