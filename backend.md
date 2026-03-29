# FGA Forge — Backend Specification (FastAPI + Supabase Postgres)

## Purpose

This document is the complete backend spec for FGA Forge. Hand this to Claude Code along with `fga-forge-compiler.md` and `fga-forge-design.md`.

The backend is a **FastAPI** application that:

1. Manages projects (CRUD for authorization models)
2. Integrates the compiler (IR → DSL, DSL → IR, validation)
3. Serves a template library of pre-built models
4. Provides real-time compilation via WebSocket
5. Handles export/import in multiple formats

**Database:** Supabase Postgres (free tier). No Supabase Auth, no Storage, no Edge Functions — just the managed Postgres. Connection via `asyncpg` through SQLAlchemy async.

**No authentication.** FGA Forge is a local-first dev tool.

---

# Part 1 — Project Structure

```
backend/
├── main.py                     # FastAPI app entry point
├── config.py                   # Settings (Supabase DB URL, CORS, etc.)
├── database.py                 # SQLAlchemy async engine + session
├── models/
│   ├── __init__.py
│   └── project.py              # SQLAlchemy ORM model
├── schemas/
│   ├── __init__.py
│   ├── project.py              # Pydantic request/response schemas
│   ├── compiler.py             # Pydantic schemas for compile/parse
│   └── template.py             # Pydantic schemas for templates
├── routers/
│   ├── __init__.py
│   ├── projects.py             # /api/projects CRUD + export/import
│   ├── compiler.py             # /api/compiler/* endpoints
│   ├── templates.py            # /api/templates
│   └── ws.py                   # WebSocket /ws/compile
├── services/
│   ├── __init__.py
│   ├── project_service.py      # DB operations for projects
│   ├── compiler_service.py     # Wraps fga_forge compiler
│   └── template_service.py     # Loads and serves templates
├── templates/                  # Pre-built model JSON files
│   ├── github_rbac.json
│   ├── saas_entitlements.json
│   ├── multi_tenant.json
│   ├── team_hierarchy.json
│   ├── bank_transfers.json
│   ├── dynamic_roles.json
│   ├── lxd_server.json
│   └── asset_management.json
├── fga_forge/                  # Compiler package (from fga-forge-compiler.md)
│   ├── __init__.py
│   ├── types.py
│   ├── validator.py
│   ├── emitter.py
│   ├── parser.py
│   └── compiler.py
├── requirements.txt
├── Dockerfile
└── tests/
    ├── test_projects.py
    ├── test_compiler.py
    └── test_templates.py
```

---

# Part 2 — Supabase Setup

## 2.1 Create the Supabase Project

1. Go to https://supabase.com/dashboard → create a new project
2. From Settings → Database, note:
   - **Host**: `db.<project-ref>.supabase.co`
   - **Port**: `5432` (direct) or `6543` (pooler)
   - **Database**: `postgres`
   - **User**: `postgres`
   - **Password**: set during creation

3. Connection string for SQLAlchemy:
   ```
   postgresql+asyncpg://postgres:<password>@db.<project-ref>.supabase.co:5432/postgres
   ```

4. For connection pooling (recommended):
   ```
   postgresql+asyncpg://postgres.<project-ref>:<password>@aws-0-<region>.pooler.supabase.com:6543/postgres
   ```

## 2.2 Create the Table (run in Supabase SQL Editor)

```sql
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS projects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    description TEXT DEFAULT '',
    model_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    canvas_state JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_projects_updated_at ON projects (updated_at DESC);
CREATE INDEX idx_projects_name_trgm ON projects USING gin (name gin_trgm_ops);

CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_update_updated_at
    BEFORE UPDATE ON projects
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at();
```

**Key differences from SQLite version:**

- `JSONB` instead of `TEXT` — dicts go straight in/out, no `json.loads`/`json.dumps`
- `UUID` primary key with `gen_random_uuid()` — generated server-side
- `TIMESTAMPTZ` — timezone-aware
- `pg_trgm` for fuzzy name search
- Trigger for auto `updated_at`

---

# Part 3 — Configuration

## `config.py`

```python
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://postgres:password@db.xxxxx.supabase.co:5432/postgres"
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:3000"]
    host: str = "0.0.0.0"
    port: int = 8000

    class Config:
        env_prefix = "FGA_FORGE_"
        env_file = ".env"

settings = Settings()
```

## `.env` (gitignored)

```
FGA_FORGE_DATABASE_URL=postgresql+asyncpg://postgres:YOUR_PASSWORD@db.YOUR_REF.supabase.co:5432/postgres
```

---

# Part 4 — Database Layer

## `database.py`

```python
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from config import settings

class Base(DeclarativeBase):
    pass

engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_size=5,
    max_overflow=5,
    pool_pre_ping=True,
    pool_recycle=300,
)

async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

async def get_db():
    async with async_session() as session:
        yield session
```

**Supabase free tier notes:**
- `pool_size=5` — free tier has limited connections, don't hog them
- `pool_pre_ping=True` — Supabase may close idle connections
- `pool_recycle=300` — recycle every 5 min to avoid stale connections

## `models/project.py`

```python
import uuid
from datetime import datetime, timezone
from sqlalchemy import String, Text, DateTime
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column
from database import Base

class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    model_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    canvas_state: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
```

---

# Part 5 — Pydantic Schemas

## `schemas/project.py`

```python
from pydantic import BaseModel, Field
from datetime import datetime

class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str = ""
    model_json: dict = Field(default_factory=dict)
    canvas_state: dict = Field(default_factory=dict)

class ProjectUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    model_json: dict | None = None
    canvas_state: dict | None = None

class ProjectResponse(BaseModel):
    id: str
    name: str
    description: str
    model_json: dict
    canvas_state: dict
    created_at: datetime
    updated_at: datetime
    class Config:
        from_attributes = True

class ProjectListItem(BaseModel):
    id: str
    name: str
    description: str
    created_at: datetime
    updated_at: datetime
    class Config:
        from_attributes = True
```

## `schemas/compiler.py`

```python
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
```

## `schemas/template.py`

```python
from pydantic import BaseModel

class TemplateListItem(BaseModel):
    id: str
    name: str
    description: str
    tags: list[str]
    type_count: int
    relation_count: int

class TemplateDetail(BaseModel):
    id: str
    name: str
    description: str
    tags: list[str]
    model: dict
    canvas_state: dict
    dsl: str
```

---

# Part 6 — API Endpoints

## 6.1 Projects — `/api/projects`

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/projects` | List all projects (by `updated_at` desc) |
| `POST` | `/api/projects` | Create project |
| `GET` | `/api/projects/{id}` | Get project (full model + canvas) |
| `PUT` | `/api/projects/{id}` | Partial update |
| `DELETE` | `/api/projects/{id}` | Delete project |
| `POST` | `/api/projects/{id}/duplicate` | Duplicate project |
| `GET` | `/api/projects/{id}/export` | Export (`?format=dsl\|json\|full`) |
| `POST` | `/api/projects/import` | Import from DSL or JSON |

**`GET /api/projects`** — Query params: `search`, `limit` (50), `offset` (0). Returns `list[ProjectListItem]` without `model_json`/`canvas_state`.

**`POST /api/projects`** — Body: `ProjectCreate`. Empty `model_json` defaults to `{"schema_version":"1.1","types":[],"conditions":[]}`. Returns 201.

**`PUT /api/projects/{id}`** — Body: `ProjectUpdate`. Only provided fields update. Stores model regardless of validation (user may be mid-edit).

**`DELETE /api/projects/{id}`** — Returns 204.

**`POST /api/projects/{id}/duplicate`** — New UUID, name + " (copy)". Returns 201.

**`GET /api/projects/{id}/export`** — `format=dsl`: compiled DSL as `text/plain` (422 if invalid). `format=json`: IR only. `format=full`: model + canvas + metadata.

**`POST /api/projects/import`** — Content-Type `application/json` or `text/plain`. JSON: full export or bare IR. Text: DSL parsed first. Returns 201.

## 6.2 Compiler — `/api/compiler`

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/compiler/compile` | IR → DSL |
| `POST` | `/api/compiler/parse` | DSL → IR |
| `POST` | `/api/compiler/validate` | Validate IR |
| `POST` | `/api/compiler/format` | Parse + re-emit (prettify) |

Stateless — no DB. Wraps `fga_forge` compiler.

## 6.3 Templates — `/api/templates`

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/templates` | List templates |
| `GET` | `/api/templates/{id}` | Template detail + DSL preview |
| `POST` | `/api/templates/{id}/use` | Create project from template |

Loaded from JSON files at startup. Read-only.

## 6.4 WebSocket — `/ws/compile`

**Client → Server:**
```json
{"type": "compile", "request_id": "abc-123", "model": {...}}
```

**Server → Client (success):**
```json
{"type": "compile_result", "request_id": "abc-123", "success": true, "dsl": "..."}
```

**Server → Client (errors):**
```json
{"type": "compile_result", "request_id": "abc-123", "success": false, "errors": [...]}
```

Also supports `"type": "validate"` → `"type": "validate_result"`.

---

# Part 7 — Services

## 7.1 `compiler_service.py`

```python
from dataclasses import asdict
from fga_forge import (
    compile as fga_compile, decompile as fga_decompile,
    validate as fga_validate, emit as fga_emit,
    FGAModel, TypeDef, RelationDef, ConditionDef, ConditionParam,
    TypeRestriction, DirectGrant, RelationRef, FromTraversal,
    UnionExpr, IntersectionExpr, CompileSuccess, ParseSuccess,
)


def dict_to_model(data: dict) -> FGAModel:
    model = FGAModel(schema_version=data.get("schema_version", "1.1"))
    for td in data.get("types", []):
        type_def = TypeDef(name=td["name"], comment=td.get("comment"))
        for rd in td.get("relations", []):
            type_def.relations.append(RelationDef(
                name=rd["name"], expression=_dict_to_expr(rd["expression"]), comment=rd.get("comment")))
        model.types.append(type_def)
    for cd in data.get("conditions", []):
        model.conditions.append(ConditionDef(
            name=cd["name"],
            parameters=[ConditionParam(name=p["name"], type=p["type"]) for p in cd.get("parameters", [])],
            expression=cd.get("expression", "")))
    return model


def _dict_to_expr(data: dict):
    kind = data["kind"]
    if kind == "direct":
        return DirectGrant(grants=[TypeRestriction(
            type=g["type"], relation=g.get("relation"),
            wildcard=g.get("wildcard", False), condition=g.get("condition"))
            for g in data.get("grants", [])])
    if kind == "ref":
        return RelationRef(relation=data["relation"])
    if kind == "from":
        return FromTraversal(source_relation=data["source_relation"], parent_relation=data["parent_relation"])
    if kind == "union":
        return UnionExpr(children=[_dict_to_expr(c) for c in data["children"]])
    if kind == "intersection":
        return IntersectionExpr(children=[_dict_to_expr(c) for c in data["children"]])
    raise ValueError(f"Unknown expression kind: {kind}")


def model_to_dict(model: FGAModel) -> dict:
    return asdict(model)


def compile_model(data: dict) -> dict:
    result = fga_compile(dict_to_model(data))
    if isinstance(result, CompileSuccess):
        return {"success": True, "dsl": result.dsl}
    return {"success": False, "errors": [asdict(e) for e in result.errors]}


def parse_dsl(dsl: str) -> dict:
    result = fga_decompile(dsl)
    if isinstance(result, ParseSuccess):
        return {"success": True, "model": model_to_dict(result.model)}
    return {"success": False, "error": result.error, "line": result.line, "column": result.column}


def validate_model(data: dict) -> dict:
    errors = fga_validate(dict_to_model(data))
    return {"valid": len(errors) == 0, "errors": [asdict(e) for e in errors]}


def format_dsl(dsl: str) -> dict:
    parsed = fga_decompile(dsl)
    if isinstance(parsed, ParseSuccess):
        return {"success": True, "formatted": fga_emit(parsed.model)}
    return {"success": False, "error": parsed.error}
```

## 7.2 `project_service.py`

```python
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from models.project import Project

DEFAULT_MODEL = {"schema_version": "1.1", "types": [], "conditions": []}


async def list_projects(db: AsyncSession, search: str | None = None, limit: int = 50, offset: int = 0) -> list[Project]:
    stmt = select(Project).order_by(Project.updated_at.desc())
    if search:
        stmt = stmt.where(Project.name.ilike(f"%{search}%"))
    result = await db.execute(stmt.limit(limit).offset(offset))
    return list(result.scalars().all())


async def get_project(db: AsyncSession, project_id: str) -> Project | None:
    result = await db.execute(select(Project).where(Project.id == project_id))
    return result.scalar_one_or_none()


async def create_project(db: AsyncSession, name: str, description: str = "",
                         model_json: dict | None = None, canvas_state: dict | None = None) -> Project:
    project = Project(name=name, description=description,
                      model_json=model_json or DEFAULT_MODEL, canvas_state=canvas_state or {})
    db.add(project)
    await db.commit()
    await db.refresh(project)
    return project


async def update_project(db: AsyncSession, project: Project, name: str | None = None,
                         description: str | None = None, model_json: dict | None = None,
                         canvas_state: dict | None = None) -> Project:
    if name is not None: project.name = name
    if description is not None: project.description = description
    if model_json is not None: project.model_json = model_json
    if canvas_state is not None: project.canvas_state = canvas_state
    await db.commit()
    await db.refresh(project)
    return project


async def delete_project(db: AsyncSession, project: Project) -> None:
    await db.delete(project)
    await db.commit()


async def duplicate_project(db: AsyncSession, project: Project) -> Project:
    return await create_project(db, name=f"{project.name} (copy)", description=project.description,
                                model_json=project.model_json, canvas_state=project.canvas_state)
```

## 7.3 `template_service.py`

```python
import json
from pathlib import Path
from fga_forge import compile as fga_compile, CompileSuccess
from services.compiler_service import dict_to_model

TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
_templates: dict[str, dict] = {}


def load_templates() -> None:
    _templates.clear()
    if not TEMPLATE_DIR.exists(): return
    for path in sorted(TEMPLATE_DIR.glob("*.json")):
        with open(path) as f:
            data = json.load(f)
        tid = path.stem
        model = dict_to_model(data["model"])
        result = fga_compile(model)
        dsl = result.dsl if isinstance(result, CompileSuccess) else "# Compilation error"
        _templates[tid] = {**data, "id": tid, "dsl": dsl}


def list_templates() -> list[dict]:
    return [{
        "id": tid, "name": t["name"], "description": t["description"],
        "tags": t.get("tags", []), "type_count": len(t["model"].get("types", [])),
        "relation_count": sum(len(td.get("relations", [])) for td in t["model"].get("types", [])),
    } for tid, t in _templates.items()]


def get_template(template_id: str) -> dict | None:
    return _templates.get(template_id)
```

---

# Part 8 — Routers

## 8.1 `routers/projects.py`

```python
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession
from database import get_db
from schemas.project import ProjectCreate, ProjectUpdate, ProjectResponse, ProjectListItem
from services import project_service, compiler_service

router = APIRouter(prefix="/api/projects", tags=["projects"])

def _r(p) -> dict:
    return {"id": str(p.id), "name": p.name, "description": p.description,
            "model_json": p.model_json, "canvas_state": p.canvas_state,
            "created_at": p.created_at, "updated_at": p.updated_at}

@router.get("", response_model=list[ProjectListItem])
async def list_projects(search: str | None = None, limit: int = 50, offset: int = 0,
                        db: AsyncSession = Depends(get_db)):
    projects = await project_service.list_projects(db, search, limit, offset)
    return [{"id": str(p.id), "name": p.name, "description": p.description,
             "created_at": p.created_at, "updated_at": p.updated_at} for p in projects]

@router.post("", response_model=ProjectResponse, status_code=201)
async def create_project(body: ProjectCreate, db: AsyncSession = Depends(get_db)):
    p = await project_service.create_project(db, body.name, body.description, body.model_json, body.canvas_state)
    return _r(p)

@router.get("/{pid}", response_model=ProjectResponse)
async def get_project(pid: str, db: AsyncSession = Depends(get_db)):
    p = await project_service.get_project(db, pid)
    if not p: raise HTTPException(404, "Project not found")
    return _r(p)

@router.put("/{pid}", response_model=ProjectResponse)
async def update_project(pid: str, body: ProjectUpdate, db: AsyncSession = Depends(get_db)):
    p = await project_service.get_project(db, pid)
    if not p: raise HTTPException(404, "Project not found")
    p = await project_service.update_project(db, p, body.name, body.description, body.model_json, body.canvas_state)
    return _r(p)

@router.delete("/{pid}", status_code=204)
async def delete_project(pid: str, db: AsyncSession = Depends(get_db)):
    p = await project_service.get_project(db, pid)
    if not p: raise HTTPException(404, "Project not found")
    await project_service.delete_project(db, p)

@router.post("/{pid}/duplicate", response_model=ProjectResponse, status_code=201)
async def duplicate_project(pid: str, db: AsyncSession = Depends(get_db)):
    p = await project_service.get_project(db, pid)
    if not p: raise HTTPException(404, "Project not found")
    return _r(await project_service.duplicate_project(db, p))

@router.get("/{pid}/export")
async def export_project(pid: str, format: str = "dsl", db: AsyncSession = Depends(get_db)):
    p = await project_service.get_project(db, pid)
    if not p: raise HTTPException(404, "Project not found")
    if format == "dsl":
        result = compiler_service.compile_model(p.model_json)
        if not result["success"]: raise HTTPException(422, detail=result["errors"])
        return Response(content=result["dsl"], media_type="text/plain")
    if format == "json": return p.model_json
    if format == "full":
        return {"name": p.name, "description": p.description, "model_json": p.model_json,
                "canvas_state": p.canvas_state, "exported_at": datetime.now().isoformat()}
    raise HTTPException(400, f"Unknown format: {format}")

@router.post("/import", status_code=201)
async def import_project(request: Request, db: AsyncSession = Depends(get_db)):
    import json as _json
    content_type = request.headers.get("content-type", "")
    body = await request.body()
    if "application/json" in content_type:
        data = _json.loads(body)
        if "model_json" in data and "name" in data:
            p = await project_service.create_project(db, data["name"], data.get("description", ""),
                                                     data["model_json"], data.get("canvas_state", {}))
            return _r(p)
        if "schema_version" in data and "types" in data:
            p = await project_service.create_project(db, f"Imported ({datetime.now().strftime('%Y-%m-%d %H:%M')})",
                                                     model_json=data)
            return _r(p)
        raise HTTPException(422, "Unrecognized JSON format")
    dsl_text = body.decode("utf-8")
    parsed = compiler_service.parse_dsl(dsl_text)
    if not parsed["success"]: raise HTTPException(422, detail=parsed)
    p = await project_service.create_project(db, f"Imported ({datetime.now().strftime('%Y-%m-%d %H:%M')})",
                                             model_json=parsed["model"])
    return _r(p)
```

## 8.2 `routers/compiler.py`

```python
from fastapi import APIRouter
from schemas.compiler import *
from services import compiler_service

router = APIRouter(prefix="/api/compiler", tags=["compiler"])

@router.post("/compile", response_model=CompileResponse)
async def compile_model(body: CompileRequest): return compiler_service.compile_model(body.model)

@router.post("/parse", response_model=ParseResponse)
async def parse_dsl(body: ParseRequest): return compiler_service.parse_dsl(body.dsl)

@router.post("/validate", response_model=ValidateResponse)
async def validate_model(body: ValidateRequest): return compiler_service.validate_model(body.model)

@router.post("/format", response_model=FormatResponse)
async def format_dsl(body: FormatRequest): return compiler_service.format_dsl(body.dsl)
```

## 8.3 `routers/templates.py`

```python
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from database import get_db
from services import template_service, project_service

router = APIRouter(prefix="/api/templates", tags=["templates"])

class UseTemplateBody(BaseModel):
    name: str | None = None

@router.get("")
async def list_templates(): return template_service.list_templates()

@router.get("/{tid}")
async def get_template(tid: str):
    t = template_service.get_template(tid)
    if not t: raise HTTPException(404, "Template not found")
    return t

@router.post("/{tid}/use", status_code=201)
async def use_template(tid: str, body: UseTemplateBody | None = None, db: AsyncSession = Depends(get_db)):
    t = template_service.get_template(tid)
    if not t: raise HTTPException(404, "Template not found")
    name = (body.name if body and body.name else t["name"])
    p = await project_service.create_project(db, name=name, model_json=t["model"],
                                             canvas_state=t.get("canvas_state", {}))
    return {"id": str(p.id), "name": p.name, "description": p.description, "model_json": t["model"],
            "canvas_state": t.get("canvas_state", {}), "created_at": p.created_at, "updated_at": p.updated_at}
```

## 8.4 `routers/ws.py`

```python
import json
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from services import compiler_service

router = APIRouter()

@router.websocket("/ws/compile")
async def websocket_compile(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            raw = await ws.receive_text()
            try: msg = json.loads(raw)
            except json.JSONDecodeError:
                await ws.send_json({"type": "error", "request_id": None, "message": "Invalid JSON"})
                continue
            rid = msg.get("request_id")
            mt = msg.get("type")
            md = msg.get("model")
            if mt not in ("compile", "validate"):
                await ws.send_json({"type": "error", "request_id": rid, "message": f"Unknown type: {mt}"})
                continue
            if not md or not isinstance(md, dict):
                await ws.send_json({"type": "error", "request_id": rid, "message": "Missing model"})
                continue
            try:
                if mt == "compile":
                    await ws.send_json({"type": "compile_result", "request_id": rid, **compiler_service.compile_model(md)})
                else:
                    await ws.send_json({"type": "validate_result", "request_id": rid, **compiler_service.validate_model(md)})
            except Exception as e:
                await ws.send_json({"type": "error", "request_id": rid, "message": str(e)})
    except WebSocketDisconnect: pass
```

---

# Part 9 — App Entry Point

```python
# main.py
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from config import settings
from database import engine, Base
from routers import projects, compiler, templates, ws
from services.template_service import load_templates

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    load_templates()
    yield
    await engine.dispose()

app = FastAPI(title="FGA Forge", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins,
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.include_router(projects.router)
app.include_router(compiler.router)
app.include_router(templates.router)
app.include_router(ws.router)

@app.get("/health")
async def health(): return {"status": "ok"}

@app.exception_handler(Exception)
async def global_exc(request, exc):
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})
```

---

# Part 10 — Templates

Each file in `templates/` follows this format:

```json
{
  "name": "GitHub RBAC",
  "description": "Repository access control with role hierarchy.",
  "tags": ["rbac", "github", "repository"],
  "model": {
    "schema_version": "1.1",
    "types": [...],
    "conditions": []
  },
  "canvas_state": {
    "nodes": [{"id": "user", "position": {"x": 100, "y": 50}}],
    "viewport": {"x": 0, "y": 0, "zoom": 1}
  }
}
```

**Templates to create from studied models:**

| File | Pattern |
|------|---------|
| `github_rbac.json` | user → team → org → repo (role subsumption) |
| `saas_entitlements.json` | user → org → plan → feature (conditional limits) |
| `multi_tenant.json` | employee/customer/app → system → org → project → task |
| `team_hierarchy.json` | user → team → project → env → snapshot (ownership chain) |
| `bank_transfers.json` | customer/employee → bank → account (intersection + conditions) |
| `dynamic_roles.json` | user → group → role → org → document (role indirection) |
| `lxd_server.json` | identity/service_account → group → server → project → instance |
| `asset_management.json` | user → team → role → org → asset-category → asset |

---

# Part 11 — Dependencies & Docker

## `requirements.txt`

```
fastapi>=0.115.0
uvicorn[standard]>=0.30.0
sqlalchemy>=2.0
asyncpg>=0.30.0
pydantic>=2.0
pydantic-settings>=2.0
```

## `Dockerfile`

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

## Docker Compose

```yaml
services:
  backend:
    build: ./backend
    ports:
      - "8000:8000"
    env_file:
      - ./backend/.env

  frontend:
    build: ./frontend
    ports:
      - "5173:5173"
    depends_on:
      - backend
```

No local Postgres needed — Supabase hosts it.

---

# Part 12 — Error Handling

| Status | Meaning |
|--------|---------|
| 200 | Success |
| 201 | Created |
| 204 | Deleted |
| 400 | Bad request |
| 404 | Not found |
| 422 | Validation/parse error |
| 500 | Internal error |

---

# Part 13 — Frontend Integration Notes

- **Auto-save:** `PUT /api/projects/{id}` debounced 1-2s. JSONB columns mean canvas-only changes are cheap.
- **Live compilation:** `ws://localhost:8000/ws/compile`, send IR debounced 300ms, match on `request_id`.
- **Templates:** `GET /api/templates` for gallery, `POST /api/templates/{id}/use` to create project.
- **Import:** `.fga` (text) or `.json` → `POST /api/projects/import`.
- **Export:** "Copy DSL" (`?format=dsl`), "Download JSON" (`?format=json`), "Download Full" (`?format=full`).
- **Supabase stay-alive:** Any API call hitting the DB counts as activity. The 7-day pause only triggers on zero activity, so normal usage keeps it alive.
