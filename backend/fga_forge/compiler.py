from .types import FGAModel, CompileSuccess, CompileError
from .validator import validate
from .emitter import emit


def compile(model: FGAModel) -> CompileSuccess | CompileError:
    errors = validate(model)
    if errors:
        return CompileError(errors=errors)
    return CompileSuccess(dsl=emit(model))
