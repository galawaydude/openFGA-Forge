from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession
from database import get_db
from schemas.project import ProjectCreate, ProjectUpdate, ProjectResponse, ProjectListItem
from services import project_service, compiler_service

router = APIRouter(prefix="/api/projects", tags=["projects"])


def _serialize(p) -> dict:
    return {
        "id": str(p.id),
        "name": p.name,
        "description": p.description,
        "model_json": p.model_json,
        "canvas_state": p.canvas_state,
        "created_at": p.created_at,
        "updated_at": p.updated_at,
    }


@router.get("", response_model=list[ProjectListItem])
async def list_projects(
    search: str | None = None,
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    projects = await project_service.list_projects(db, search, limit, offset)
    return [
        {"id": str(p.id), "name": p.name, "description": p.description,
         "created_at": p.created_at, "updated_at": p.updated_at}
        for p in projects
    ]


@router.post("", response_model=ProjectResponse, status_code=201)
async def create_project(body: ProjectCreate, db: AsyncSession = Depends(get_db)):
    p = await project_service.create_project(db, body.name, body.description, body.model_json, body.canvas_state)
    return _serialize(p)


@router.get("/{pid}", response_model=ProjectResponse)
async def get_project(pid: str, db: AsyncSession = Depends(get_db)):
    p = await project_service.get_project(db, pid)
    if not p:
        raise HTTPException(404, "Project not found")
    return _serialize(p)


@router.put("/{pid}", response_model=ProjectResponse)
async def update_project(pid: str, body: ProjectUpdate, db: AsyncSession = Depends(get_db)):
    p = await project_service.get_project(db, pid)
    if not p:
        raise HTTPException(404, "Project not found")
    p = await project_service.update_project(db, p, body.name, body.description, body.model_json, body.canvas_state)
    return _serialize(p)


@router.delete("/{pid}", status_code=204)
async def delete_project(pid: str, db: AsyncSession = Depends(get_db)):
    p = await project_service.get_project(db, pid)
    if not p:
        raise HTTPException(404, "Project not found")
    await project_service.delete_project(db, p)


@router.post("/{pid}/duplicate", response_model=ProjectResponse, status_code=201)
async def duplicate_project(pid: str, db: AsyncSession = Depends(get_db)):
    p = await project_service.get_project(db, pid)
    if not p:
        raise HTTPException(404, "Project not found")
    return _serialize(await project_service.duplicate_project(db, p))


@router.get("/{pid}/export")
async def export_project(pid: str, format: str = "dsl", db: AsyncSession = Depends(get_db)):
    p = await project_service.get_project(db, pid)
    if not p:
        raise HTTPException(404, "Project not found")
    if format == "dsl":
        result = compiler_service.compile_model(p.model_json)
        if not result["success"]:
            raise HTTPException(422, detail=result["errors"])
        return Response(content=result["dsl"], media_type="text/plain")
    if format == "json":
        return p.model_json
    if format == "full":
        return {
            "name": p.name,
            "description": p.description,
            "model_json": p.model_json,
            "canvas_state": p.canvas_state,
            "exported_at": datetime.now().isoformat(),
        }
    raise HTTPException(400, f"Unknown format: {format}")


@router.post("/import", status_code=201)
async def import_project(request: Request, db: AsyncSession = Depends(get_db)):
    import json as _json
    content_type = request.headers.get("content-type", "")
    body = await request.body()
    if "application/json" in content_type:
        try:
            data = _json.loads(body)
        except _json.JSONDecodeError as e:
            raise HTTPException(400, f"Invalid JSON: {e}")
        if "model_json" in data and "name" in data:
            p = await project_service.create_project(
                db, data["name"], data.get("description", ""),
                data["model_json"], data.get("canvas_state", {}),
            )
            return _serialize(p)
        # Accept both snake_case (backend IR) and camelCase (spec.md / OpenFGA native)
        has_ir_root = ("schema_version" in data or "schemaVersion" in data) and "types" in data
        if has_ir_root:
            p = await project_service.create_project(
                db,
                f"Imported ({datetime.now().strftime('%Y-%m-%d %H:%M')})",
                model_json=data,
            )
            return _serialize(p)
        raise HTTPException(422, "Unrecognized JSON format")
    dsl_text = body.decode("utf-8")
    parsed = compiler_service.parse_dsl(dsl_text)
    if not parsed["success"]:
        raise HTTPException(422, detail=parsed)
    p = await project_service.create_project(
        db,
        f"Imported ({datetime.now().strftime('%Y-%m-%d %H:%M')})",
        model_json=parsed["model"],
    )
    return _serialize(p)
