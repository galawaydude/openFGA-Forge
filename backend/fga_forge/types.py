from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, Union


@dataclass
class TypeRestriction:
    type: str
    relation: Optional[str] = None
    wildcard: bool = False
    condition: Optional[str] = None


@dataclass
class DirectGrant:
    kind: str = "direct"
    grants: list[TypeRestriction] = field(default_factory=list)


@dataclass
class RelationRef:
    kind: str = "ref"
    relation: str = ""


@dataclass
class FromTraversal:
    kind: str = "from"
    source_relation: str = ""
    parent_relation: str = ""


@dataclass
class UnionExpr:
    kind: str = "union"
    children: list[RelationExpression] = field(default_factory=list)


@dataclass
class IntersectionExpr:
    kind: str = "intersection"
    children: list[RelationExpression] = field(default_factory=list)


RelationExpression = Union[DirectGrant, RelationRef, FromTraversal, UnionExpr, IntersectionExpr]


@dataclass
class ConditionParam:
    name: str
    type: str


@dataclass
class ConditionDef:
    name: str
    parameters: list[ConditionParam] = field(default_factory=list)
    expression: str = ""


@dataclass
class RelationDef:
    name: str
    expression: Optional[RelationExpression] = None
    comment: Optional[str] = None


@dataclass
class TypeDef:
    name: str
    relations: list[RelationDef] = field(default_factory=list)
    comment: Optional[str] = None


@dataclass
class FGAModel:
    schema_version: str = "1.1"
    types: list[TypeDef] = field(default_factory=list)
    conditions: list[ConditionDef] = field(default_factory=list)


@dataclass
class ValidationError:
    code: str
    message: str
    type_name: Optional[str] = None
    relation_name: Optional[str] = None
    path: Optional[str] = None


@dataclass
class CompileSuccess:
    dsl: str


@dataclass
class CompileError:
    errors: list[ValidationError]


@dataclass
class ParseSuccess:
    model: FGAModel


@dataclass
class ParseError:
    error: str
    line: Optional[int] = None
    column: Optional[int] = None


VALID_CONDITION_PARAM_TYPES = {
    "string", "int", "uint", "double", "bool",
    "duration", "timestamp",
}
