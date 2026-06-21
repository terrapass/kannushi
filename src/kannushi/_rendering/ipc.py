import pickle
import sys
from typing import Protocol
from multiprocessing import get_start_method
from multiprocessing.shared_memory import SharedMemory

from .._vars import TemplateVariables

#
# Protocols
#

class TemplateVariablesTransport(Protocol):
    def __enter__(self) -> "TemplateVariablesTransport": ...

    def __exit__(self, *exc: object) -> None: ...

    def retrieve_vars(self) -> TemplateVariables: ...

#
# Service types
#

class _DirectTemplateVariablesTransport:
    def __init__(self, vars: TemplateVariables):
        self.__vars = vars

    def __enter__(self) -> "TemplateVariablesTransport":
        return self

    def __exit__(self, *exc: object) -> None:
        pass

    def retrieve_vars(self) -> TemplateVariables:
        return self.__vars

class _SharedMemoryTemplateVariablesTransport:
    def __init__(self, vars: TemplateVariables):
        payload = pickle.dumps(vars, protocol=pickle.HIGHEST_PROTOCOL)
        assert len(payload) > 0
        self.__shm  = SharedMemory(create=True, size=len(payload))
        self.__name = self.__shm.name
        self.__size = len(payload)
        buf = self.__shm.buf
        assert buf is not None
        buf[:self.__size] = payload

    def __enter__(self) -> "TemplateVariablesTransport":
        return self

    def __exit__(self, *exc: object) -> None:
        assert self.__shm is not None, "worker process must never enter here"
        self.__shm.close()
        self.__shm.unlink()

    def retrieve_vars(self) -> TemplateVariables:
        shm = _attach_shared_memory(self.__name)
        try:
            buf = shm.buf
            assert buf is not None
            return pickle.loads(bytes(buf[:self.__size]))
        finally:
            shm.close()

    def __getstate__(self) -> dict:
        return {'name': self.__name, 'size': self.__size}

    def __setstate__(self, state: dict) -> None:
        self.__name = state['name']
        self.__size = state['size']
        self.__shm  = None

#
# Interface
#

def make_template_variables_transport(vars: TemplateVariables) -> TemplateVariablesTransport:
    if get_start_method() == 'fork':
        return _DirectTemplateVariablesTransport(vars)
    return _SharedMemoryTemplateVariablesTransport(vars)

#
# Service
#

def _attach_shared_memory(name: str) -> SharedMemory:
    if sys.version_info >= (3, 13):
        return SharedMemory(name=name, track=False)
    return SharedMemory(name=name)
