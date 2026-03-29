from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from database import get_db
from services import template_service, project_service

router = APIRouter(prefix="/api/templates", tags=["templates"])


class UseTemplateBody(BaseModel):
    name: str | None = None


@router.get("")
async def list_templates():
    return template_service.list_templates()


@router.get("/{tid}")
async def get_template(tid: str):
    t = template_service.get_template(tid)
    if not t:
        raise HTTPException(404, "Template not found")
    return t


@router.post("/{tid}/use", status_code=201)
async def use_template(
    tid: str,
    body: UseTemplateBody | None = None,
    db: AsyncSession = Depends(get_db),
):
    t = template_service.get_template(tid)
    if not t:
        raise HTTPException(404, "Template not found")
    name = body.name if body and body.name else t["name"]
    p = await project_service.create_project(
        db, name=name, model_json=t["model"], canvas_state=t.get("canvas_state", {})
    )
    return {
        "id": str(p.id),
        "name": p.name,
        "description": p.description,
        "model_json": t["model"],
        "canvas_state": t.get("canvas_state", {}),
        "created_at": p.created_at,
        "updated_at": p.updated_at,
    }
