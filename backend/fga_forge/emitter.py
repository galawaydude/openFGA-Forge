from .types import (
    FGAModel, RelationExpression,
    DirectGrant, RelationRef, FromTraversal, UnionExpr, IntersectionExpr,
)


def emit_expression(expr: RelationExpression) -> str:
    if expr.kind == "direct":
        items = []
        for grant in expr.grants:
            s = grant.type
            if grant.wildcard:
                s += ":*"
            elif grant.relation:
                s += f"#{grant.relation}"
            if grant.condition:
                s += f" with {grant.condition}"
            items.append(s)
        return "[" + ", ".join(items) + "]"

    if expr.kind == "ref":
        return expr.relation

    if expr.kind == "from":
        return f"{expr.source_relation} from {expr.parent_relation}"

    if expr.kind == "union":
        parts = [emit_expression(child) for child in expr.children]
        return " or ".join(parts)

    if expr.kind == "intersection":
        parts = []
        for child in expr.children:
            if child.kind == "union":
                parts.append(f"({emit_expression(child)})")
            else:
                parts.append(emit_expression(child))
        return " and ".join(parts)

    raise ValueError(f"Unknown expression kind: {expr.kind}")


def emit(model: FGAModel) -> str:
    lines = ["model", "  schema 1.1"]

    for type_def in model.types:
        if type_def.comment:
            for comment_line in type_def.comment.split("\n"):
                lines.append(f"# {comment_line}")
        lines.append(f"type {type_def.name}")
        if type_def.relations:
            lines.append("  relations")
            for rel in type_def.relations:
                if rel.comment:
                    for comment_line in rel.comment.split("\n"):
                        lines.append(f"    # {comment_line}")
                expr_str = emit_expression(rel.expression)
                lines.append(f"    define {rel.name}: {expr_str}")

    for cond in model.conditions:
        params = ", ".join(f"{p.name}: {p.type}" for p in cond.parameters)
        lines.append(f"condition {cond.name}({params}) {{")
        for expr_line in cond.expression.split("\n"):
            lines.append(f"  {expr_line}")
        lines.append("}")

    return "\n".join(lines) + "\n"
