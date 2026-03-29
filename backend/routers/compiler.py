from fastapi import APIRouter
from schemas.compiler import (
    CompileRequest, CompileResponse,
    ParseRequest, ParseResponse,
    ValidateRequest, ValidateResponse,
    FormatRequest, FormatResponse,
)
from services import compiler_service

router = APIRouter(prefix="/api/compiler", tags=["compiler"])


@router.post("/compile", response_model=CompileResponse)
async def compile_model(body: CompileRequest):
    return compiler_service.compile_model(body.model)


@router.post("/parse", response_model=ParseResponse)
async def parse_dsl(body: ParseRequest):
    return compiler_service.parse_dsl(body.dsl)


@router.post("/validate", response_model=ValidateResponse)
async def validate_model(body: ValidateRequest):
    return compiler_service.validate_model(body.model)


@router.post("/format", response_model=FormatResponse)
async def format_dsl(body: FormatRequest):
    return compiler_service.format_dsl(body.dsl)
