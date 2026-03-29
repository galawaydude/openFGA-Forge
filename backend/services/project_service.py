from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from models.project import Project

DEFAULT_MODEL = {"schema_version": "1.1", "types": [], "conditions": []}


async def list_projects(
    db: AsyncSession,
    search: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Project]:
    stmt = select(Project).order_by(Project.updated_at.desc())
    if search:
        stmt = stmt.where(Project.name.ilike(f"%{search}%"))
    result = await db.execute(stmt.limit(limit).offset(offset))
    return list(result.scalars().all())


async def get_project(db: AsyncSession, project_id: str) -> Project | None:
    result = await db.execute(select(Project).where(Project.id == project_id))
    return result.scalar_one_or_none()


async def create_project(
    db: AsyncSession,
    name: str,
    description: str = "",
    model_json: dict | None = None,
    canvas_state: dict | None = None,
) -> Project:
    project = Project(
        name=name,
        description=description,
        model_json=model_json or DEFAULT_MODEL,
        canvas_state=canvas_state or {},
    )
    db.add(project)
    await db.commit()
    await db.refresh(project)
    return project


async def update_project(
    db: AsyncSession,
    project: Project,
    name: str | None = None,
    description: str | None = None,
    model_json: dict | None = None,
    canvas_state: dict | None = None,
) -> Project:
    if name is not None:
        project.name = name
    if description is not None:
        project.description = description
    if model_json is not None:
        project.model_json = model_json
    if canvas_state is not None:
        project.canvas_state = canvas_state
    await db.commit()
    await db.refresh(project)
    return project


async def delete_project(db: AsyncSession, project: Project) -> None:
    await db.delete(project)
    await db.commit()


async def duplicate_project(db: AsyncSession, project: Project) -> Project:
    return await create_project(
        db,
        name=f"{project.name} (copy)",
        description=project.description,
        model_json=project.model_json,
        canvas_state=project.canvas_state,
    )
