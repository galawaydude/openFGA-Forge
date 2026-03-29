import re
from .types import (
    FGAModel, ValidationError, RelationExpression,
    DirectGrant, RelationRef, FromTraversal, UnionExpr, IntersectionExpr,
    VALID_CONDITION_PARAM_TYPES,
)

_TYPE_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]*$")
_RELATION_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")   # no hyphens
_CONDITION_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def _collect_target_types(
    expr: RelationExpression,
    type_name: str,
    rel_expr_map: dict[str, dict],
    visited: frozenset = frozenset(),
) -> set[str]:
    """
    Resolve all concrete object types this relation can point to.
    Follows RelationRef aliases within the same type to handle indirect grants
    like ``organization: org_ref`` where ``org_ref`` is the real DirectGrant.
    Uses a visited set to break cycles.
    """
    if expr.kind == "direct":
        return {g.type for g in expr.grants}

    if expr.kind == "ref":
        key = (type_name, expr.relation)
        if key in visited:
            return set()
        ref_expr = rel_expr_map.get(type_name, {}).get(expr.relation)
        if ref_expr is None:
            return set()
        return _collect_target_types(ref_expr, type_name, rel_expr_map, visited | {key})

    if expr.kind in ("union", "intersection"):
        result: set[str] = set()
        for child in expr.children:
            result |= _collect_target_types(child, type_name, rel_expr_map, visited)
        return result

    # FromTraversal — target type isn't statically resolvable here
    return set()


def _collect_self_from_deps(
    expr: RelationExpression,
    type_name: str,
    rel_name: str,
    type_target_map: dict[str, dict[str, set[str]]],
    deps: dict[str, set[str]],
) -> None:
    """
    Populate deps[rel_name] with the source relations that rel_name depends on
    via from traversals that loop back to the same type.
    """
    if expr.kind == "from":
        targets = type_target_map.get(type_name, {}).get(expr.parent_relation, set())
        if type_name in targets:
            deps[rel_name].add(expr.source_relation)
    elif expr.kind in ("union", "intersection"):
        for child in expr.children:
            _collect_self_from_deps(child, type_name, rel_name, type_target_map, deps)


def _check_circular_from_chains(
    td,
    type_target_map: dict[str, dict[str, set[str]]],
    errors: list[ValidationError],
) -> None:
    """
    Detect from-only cycles within a single type.

    Example: given type T with parent: [T], a: b from parent, b: a from parent —
    a depends on b (on T via parent) and b depends on a (on T via parent) → cycle.
    """
    type_name = td.name
    deps: dict[str, set[str]] = {rd.name: set() for rd in td.relations}

    for rd in td.relations:
        if rd.expression is not None:
            _collect_self_from_deps(rd.expression, type_name, rd.name, type_target_map, deps)

    # DFS cycle detection (three-colour: unvisited / in-stack / done)
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {}

    def dfs(node: str) -> bool:
        color[node] = GRAY
        for neighbour in deps.get(node, set()):
            state = color.get(neighbour, WHITE)
            if state == GRAY:
                return True   # back-edge → cycle
            if state == WHITE and dfs(neighbour):
                return True
        color[node] = BLACK
        return False

    for rel_name in deps:
        if color.get(rel_name, WHITE) == WHITE:
            if dfs(rel_name):
                errors.append(ValidationError(
                    code="CIRCULAR_FROM_CHAIN",
                    message=(
                        f"Circular 'from' chain detected in type '{type_name}' "
                        f"(starting at relation '{rel_name}')"
                    ),
                    type_name=type_name,
                    relation_name=rel_name,
                ))
                break  # one error per type is enough


def validate(model: FGAModel) -> list[ValidationError]:
    errors: list[ValidationError] = []
    type_names: set[str] = set()
    type_map: dict[str, set[str]] = {}   # type_name → set of relation names
    rel_expr_map: dict[str, dict[str, RelationExpression]] = {}  # type → rel → expr

    # ── Pass 1: collect types and relation names ────────────────────────────
    for td in model.types:
        if not td.name or not td.name.strip():
            errors.append(ValidationError(code="EMPTY_TYPE_NAME", message="Type name is empty"))
            continue
        if not _TYPE_NAME_RE.match(td.name):
            errors.append(ValidationError(
                code="INVALID_TYPE_NAME",
                message=f"Type name '{td.name}' is invalid",
                type_name=td.name,
            ))
        if td.name in type_names:
            errors.append(ValidationError(
                code="DUPLICATE_TYPE_NAME",
                message=f"Duplicate type name: '{td.name}'",
                type_name=td.name,
            ))
        type_names.add(td.name)
        rel_names: set[str] = set()
        rel_expr_map[td.name] = {}
        for rd in td.relations:
            # [Fix] enforce relation-name syntax (no hyphens allowed)
            if not _RELATION_NAME_RE.match(rd.name):
                errors.append(ValidationError(
                    code="INVALID_RELATION_NAME",
                    message=f"Relation name '{rd.name}' is invalid (hyphens not allowed in relation names)",
                    type_name=td.name,
                    relation_name=rd.name,
                ))
            if rd.name in rel_names:
                errors.append(ValidationError(
                    code="DUPLICATE_RELATION_NAME",
                    message=f"Duplicate relation '{rd.name}' in type '{td.name}'",
                    type_name=td.name,
                    relation_name=rd.name,
                ))
            rel_names.add(rd.name)
            if rd.expression is not None:
                rel_expr_map[td.name][rd.name] = rd.expression
        type_map[td.name] = rel_names

    condition_names = {c.name for c in model.conditions}

    # ── Validate conditions ─────────────────────────────────────────────────
    for cond in model.conditions:
        if not _CONDITION_NAME_RE.match(cond.name):
            errors.append(ValidationError(
                code="INVALID_CONDITION_NAME",
                message=f"Condition name '{cond.name}' is invalid",
            ))
        for param in cond.parameters:
            base_type = param.type
            while base_type.startswith("list<") or base_type.startswith("map<"):
                base_type = base_type[5:-1] if base_type.startswith("list<") else base_type[4:-1]
            if base_type not in VALID_CONDITION_PARAM_TYPES:
                errors.append(ValidationError(
                    code="INVALID_CONDITION_PARAM_TYPE",
                    message=f"Unknown param type '{param.type}' in condition '{cond.name}'",
                ))

    # ── Pass 2: build type-target map for from-traversal checking ───────────
    # type_target_map[type_name][rel_name] = object types rel_name resolves to
    # (follows RelationRef aliases so indirect grants are included)
    type_target_map: dict[str, dict[str, set[str]]] = {}
    for type_name, rel_map in rel_expr_map.items():
        type_target_map[type_name] = {}
        for rel_name, expr in rel_map.items():
            type_target_map[type_name][rel_name] = _collect_target_types(
                expr, type_name, rel_expr_map
            )

    # ── Pass 2b: detect circular from chains ────────────────────────────────
    for td in model.types:
        _check_circular_from_chains(td, type_target_map, errors)

    # ── Pass 3: validate each relation expression ───────────────────────────
    for td in model.types:
        if td.name not in type_map:
            continue
        for rd in td.relations:
            if rd.expression is None:
                errors.append(ValidationError(
                    code="EMPTY_EXPRESSION",
                    message=f"Relation '{rd.name}' on type '{td.name}' has no expression",
                    type_name=td.name,
                    relation_name=rd.name,
                ))
                continue
            _validate_expression(
                rd.expression, td.name, rd.name,
                type_map, condition_names, type_target_map,
                errors, path="",
            )

    return errors


def _validate_expression(
    expr: RelationExpression,
    type_name: str,
    relation_name: str,
    type_map: dict[str, set[str]],
    condition_names: set[str],
    type_target_map: dict[str, dict[str, set[str]]],
    errors: list[ValidationError],
    path: str,
) -> None:
    if expr.kind == "direct":
        if not expr.grants:
            errors.append(ValidationError(
                code="EMPTY_DIRECT_GRANT",
                message=f"DirectGrant on '{type_name}.{relation_name}' has no grants",
                type_name=type_name, relation_name=relation_name, path=path,
            ))
            return
        for i, grant in enumerate(expr.grants):
            gpath = f"{path}.grants[{i}]"
            if grant.type not in type_map:
                errors.append(ValidationError(
                    code="UNDEFINED_TYPE_REF",
                    message=f"Type '{grant.type}' not defined",
                    type_name=type_name, relation_name=relation_name, path=gpath,
                ))
            elif grant.relation and grant.relation not in type_map.get(grant.type, set()):
                errors.append(ValidationError(
                    code="UNDEFINED_GRANT_RELATION",
                    message=f"Relation '{grant.relation}' not defined on type '{grant.type}'",
                    type_name=type_name, relation_name=relation_name, path=gpath,
                ))
            if grant.condition and grant.condition not in condition_names:
                errors.append(ValidationError(
                    code="UNDEFINED_CONDITION",
                    message=f"Condition '{grant.condition}' not defined",
                    type_name=type_name, relation_name=relation_name, path=gpath,
                ))

    elif expr.kind == "ref":
        # [Fix] reject self-referential relation references
        if expr.relation == relation_name:
            errors.append(ValidationError(
                code="SELF_REFERENCE_RELATION",
                message=f"Relation '{relation_name}' on type '{type_name}' references itself",
                type_name=type_name, relation_name=relation_name, path=path,
            ))
        elif expr.relation not in type_map.get(type_name, set()):
            errors.append(ValidationError(
                code="UNDEFINED_RELATION_REF",
                message=f"Relation '{expr.relation}' not defined on type '{type_name}'",
                type_name=type_name, relation_name=relation_name, path=path,
            ))

    elif expr.kind == "from":
        own_relations = type_map.get(type_name, set())
        if expr.parent_relation not in own_relations:
            errors.append(ValidationError(
                code="UNDEFINED_FROM_PARENT",
                message=f"Parent relation '{expr.parent_relation}' not defined on type '{type_name}'",
                type_name=type_name, relation_name=relation_name, path=path,
            ))
        else:
            # [Fix] also check source_relation on every type that parent_relation points to
            target_types = type_target_map.get(type_name, {}).get(expr.parent_relation, set())
            for target_type in target_types:
                if target_type in type_map and expr.source_relation not in type_map[target_type]:
                    errors.append(ValidationError(
                        code="UNDEFINED_FROM_SOURCE",
                        message=(
                            f"Relation '{expr.source_relation}' not defined on type '{target_type}' "
                            f"(resolved via '{type_name}.{expr.parent_relation}')"
                        ),
                        type_name=type_name, relation_name=relation_name, path=path,
                    ))

    elif expr.kind == "union":
        if len(expr.children) < 2:
            errors.append(ValidationError(
                code="UNION_CHILD_COUNT",
                message="Union must have at least 2 children",
                type_name=type_name, relation_name=relation_name, path=path,
            ))
        for i, child in enumerate(expr.children):
            _validate_expression(
                child, type_name, relation_name,
                type_map, condition_names, type_target_map,
                errors, path=f"{path}.union.children[{i}]",
            )

    elif expr.kind == "intersection":
        # [Fix] accept 2+ children (not exactly 2) — OpenFGA supports `a and b and c`
        if len(expr.children) < 2:
            errors.append(ValidationError(
                code="INTERSECTION_CHILD_COUNT",
                message="Intersection must have at least 2 children",
                type_name=type_name, relation_name=relation_name, path=path,
            ))
        for i, child in enumerate(expr.children):
            _validate_expression(
                child, type_name, relation_name,
                type_map, condition_names, type_target_map,
                errors, path=f"{path}.intersection.children[{i}]",
            )
