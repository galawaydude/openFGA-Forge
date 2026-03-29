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
