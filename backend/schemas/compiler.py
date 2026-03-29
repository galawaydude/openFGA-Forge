from pydantic import BaseModel, Field


class CompileRequest(BaseModel):
    model: dict


class CompileResponse(BaseModel):
    success: bool
    dsl: str | None = None
    errors: list[dict] | None = None


class ParseRequest(BaseModel):
    dsl: str


class ParseResponse(BaseModel):
    success: bool
    model: dict | None = None
    error: str | None = None
    line: int | None = None
    column: int | None = None


class ValidateRequest(BaseModel):
    model: dict


class ValidateResponse(BaseModel):
    valid: bool
    errors: list[dict] = Field(default_factory=list)


class FormatRequest(BaseModel):
    dsl: str


class FormatResponse(BaseModel):
    success: bool
    formatted: str | None = None
    error: str | None = None
