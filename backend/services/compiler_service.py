from dataclasses import asdict
from fga_forge import (
    compile as fga_compile,
    decompile as fga_decompile,
    validate as fga_validate,
    emit as fga_emit,
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
                name=rd["name"],
                expression=_dict_to_expr(rd["expression"]),
                comment=rd.get("comment"),
            ))
        model.types.append(type_def)
    for cd in data.get("conditions", []):
        model.conditions.append(ConditionDef(
            name=cd["name"],
            parameters=[ConditionParam(name=p["name"], type=p["type"]) for p in cd.get("parameters", [])],
            expression=cd.get("expression", ""),
        ))
    return model


def _dict_to_expr(data: dict):
    kind = data["kind"]
    if kind == "direct":
        return DirectGrant(grants=[
            TypeRestriction(
                type=g["type"],
                relation=g.get("relation"),
                wildcard=g.get("wildcard", False),
                condition=g.get("condition"),
            )
            for g in data.get("grants", [])
        ])
    if kind == "ref":
        return RelationRef(relation=data["relation"])
    if kind == "from":
        # Support both camelCase (spec.md) and snake_case keys
        return FromTraversal(
            source_relation=data.get("source_relation") or data.get("sourceRelation", ""),
            parent_relation=data.get("parent_relation") or data.get("parentRelation", ""),
        )
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
