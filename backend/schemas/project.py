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
