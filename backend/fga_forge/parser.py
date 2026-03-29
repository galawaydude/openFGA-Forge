"""
DSL → IR parser for OpenFGA DSL.

Implements a recursive-descent parser for the grammar in spec.md §2.8.
"""
import re
from .types import (
    FGAModel, TypeDef, RelationDef, ConditionDef, ConditionParam,
    TypeRestriction, DirectGrant, RelationRef, FromTraversal,
    UnionExpr, IntersectionExpr,
    ParseSuccess, ParseError,
)

_NAME_RE = re.compile(r"^([a-z][a-z0-9_]*)(.*)", re.DOTALL)  # relation names: no hyphens


class _ParseError(Exception):
    def __init__(self, message: str, line: int | None = None, column: int | None = None):
        self.message = message
        self.line = line
        self.column = column
        super().__init__(message)


class _Parser:
    def __init__(self, text: str):
        self.lines = text.splitlines()
        self.pos = 0

    # ── line-level helpers ──────────────────────────────────────────────────

    def _line_num(self) -> int:
        return self.pos + 1

    def _peek(self) -> str | None:
        """Next non-blank line (does not consume it)."""
        i = self.pos
        while i < len(self.lines) and not self.lines[i].strip():
            i += 1
        return self.lines[i] if i < len(self.lines) else None

    def _consume(self) -> str:
        """Advance past blank lines, return next non-blank line."""
        while self.pos < len(self.lines) and not self.lines[self.pos].strip():
            self.pos += 1
        if self.pos >= len(self.lines):
            raise _ParseError("Unexpected end of input", line=self._line_num())
        line = self.lines[self.pos]
        self.pos += 1
        return line

    # ── top-level ───────────────────────────────────────────────────────────

    def parse(self) -> FGAModel:
        line = self._consume()
        if line.strip() != "model":
            raise _ParseError(f"Expected 'model', got {line.strip()!r}", line=self.pos)

        line = self._consume()
        if line.strip() != "schema 1.1":
            raise _ParseError(f"Expected 'schema 1.1', got {line.strip()!r}", line=self.pos)

        model = FGAModel()

        while True:
            line = self._peek()
            if line is None:
                break
            stripped = line.strip()
            if stripped.startswith("type ") or stripped == "type":
                model.types.append(self._parse_type())
            elif stripped.startswith("condition "):
                model.conditions.append(self._parse_condition())
            elif stripped.startswith("#"):
                # Collect consecutive comment lines; attach to the following type if any
                comment = self._consume_comment_block()
                nxt = self._peek()
                if nxt is not None and (nxt.strip().startswith("type ") or nxt.strip() == "type"):
                    td = self._parse_type()
                    td.comment = comment
                    model.types.append(td)
                # else: model-level comment with no following type — silently discard
            else:
                raise _ParseError(f"Unexpected token: {stripped!r}", line=self._line_num())

        return model

    # ── type block ──────────────────────────────────────────────────────────

    def _consume_comment_block(self) -> str:
        """Consume consecutive # lines and return their bodies joined by newlines."""
        body_lines = []
        while self._peek() is not None and self._peek().strip().startswith("#"):
            body_lines.append(self._consume().strip()[1:].strip())
        return "\n".join(body_lines)

    def _parse_type(self) -> TypeDef:
        line = self._consume()
        m = re.match(r"^type\s+(\S+)", line.strip())
        if not m:
            raise _ParseError(f"Invalid type declaration: {line.strip()!r}", line=self.pos)
        name = m.group(1)
        if not re.match(r"^[a-z][a-z0-9_-]*$", name):
            raise _ParseError(f"Invalid type name: {name!r}", line=self.pos)
        type_def = TypeDef(name=name)

        next_line = self._peek()
        if next_line is not None and next_line.strip() == "relations":
            self._consume()  # consume "relations"
            while True:
                next_line = self._peek()
                if next_line is None:
                    break
                indent = len(next_line) - len(next_line.lstrip())
                stripped = next_line.strip()
                if indent >= 4 and (stripped.startswith("define ") or stripped.startswith("#")):
                    type_def.relations.append(self._parse_relation())
                else:
                    break

        return type_def

    def _parse_relation(self) -> RelationDef:
        # Consume and accumulate any leading # comment lines, then the define line
        comment_lines = []
        while True:
            line = self._consume()
            stripped = line.strip()
            if stripped.startswith("#"):
                comment_lines.append(stripped[1:].strip())
            else:
                break  # must be the define line

        comment = "\n".join(comment_lines) if comment_lines else None

        m = re.match(r"^define\s+([a-z][a-z0-9_]*)\s*:\s*(.+)$", stripped)  # no hyphens in relation names
        if not m:
            raise _ParseError(f"Invalid relation definition: {stripped!r}", line=self.pos)

        name = m.group(1)
        expr_str = m.group(2).strip()
        expr = self._parse_expression(expr_str)
        return RelationDef(name=name, expression=expr, comment=comment)

    # ── expression parser ───────────────────────────────────────────────────

    def _parse_expression(self, s: str):
        s = s.strip()
        operands = []
        operators = []
        remaining = s

        while remaining:
            remaining = remaining.strip()
            if not remaining:
                break

            operand, remaining = self._parse_operand(remaining)
            operands.append(operand)

            remaining = remaining.strip()
            or_m = re.match(r"^or\b(.*)", remaining, re.DOTALL)
            and_m = re.match(r"^and\b(.*)", remaining, re.DOTALL)

            if or_m:
                remaining = or_m.group(1).strip()
                operators.append("or")
            elif and_m:
                remaining = and_m.group(1).strip()
                operators.append("and")
            else:
                break

        if not operands:
            raise _ParseError(f"Empty expression: {s!r}")
        if len(operands) == 1:
            return operands[0]

        if len(set(operators)) > 1:
            raise _ParseError(
                f"Cannot mix 'or' and 'and' without parentheses: {s!r}"
            )

        if operators[0] == "or":
            return UnionExpr(children=operands)
        return IntersectionExpr(children=operands)

    def _parse_operand(self, s: str) -> tuple:
        """Returns (expr, remaining_string)."""
        s = s.strip()

        if s.startswith("["):
            end = self._find_bracket_end(s)
            inside = s[1:end]
            remaining = s[end + 1:]
            return DirectGrant(grants=self._parse_type_restrictions(inside)), remaining

        if s.startswith("("):
            end = self._find_paren_end(s)
            inside = s[1:end]
            remaining = s[end + 1:]
            return self._parse_expression(inside), remaining

        m = _NAME_RE.match(s)
        if not m:
            raise _ParseError(f"Expected name in expression: {s!r}")
        name = m.group(1)
        rest = m.group(2)

        from_m = re.match(r"^\s+from\s+([a-z][a-z0-9_]*)(.*)", rest, re.DOTALL)  # parent is a relation name
        if from_m:
            return FromTraversal(
                source_relation=name,
                parent_relation=from_m.group(1),
            ), from_m.group(2)

        return RelationRef(relation=name), rest

    # ── type-restriction list parser ────────────────────────────────────────

    def _parse_type_restrictions(self, s: str) -> list[TypeRestriction]:
        return [
            self._parse_type_restriction(part.strip())
            for part in s.split(",")
            if part.strip()
        ]

    def _parse_type_restriction(self, s: str) -> TypeRestriction:
        condition = None
        with_m = re.match(r"^(.+?)\s+with\s+([a-z][a-z0-9_]*)$", s)
        if with_m:
            s = with_m.group(1).strip()
            condition = with_m.group(2)

        if ":*" in s:
            type_name = s.replace(":*", "").strip()
            if not re.match(r"^[a-z][a-z0-9_-]*$", type_name):
                raise _ParseError(f"Invalid type name in grant: {type_name!r}")
            return TypeRestriction(type=type_name, wildcard=True, condition=condition)
        if "#" in s:
            parts = s.split("#", 1)
            type_name, rel_name = parts[0].strip(), parts[1].strip()
            if not re.match(r"^[a-z][a-z0-9_-]*$", type_name):
                raise _ParseError(f"Invalid type name in grant: {type_name!r}")
            if not re.match(r"^[a-z][a-z0-9_]*$", rel_name):
                raise _ParseError(f"Invalid relation name in grant: {rel_name!r}")
            return TypeRestriction(type=type_name, relation=rel_name, condition=condition)
        type_name = s.strip()
        if not re.match(r"^[a-z][a-z0-9_-]*$", type_name):
            raise _ParseError(f"Invalid type name in grant: {type_name!r}")
        return TypeRestriction(type=type_name, condition=condition)

    # ── bracket / paren helpers ─────────────────────────────────────────────

    def _find_bracket_end(self, s: str) -> int:
        for i, c in enumerate(s):
            if c == "]":
                return i
        raise _ParseError(f"Unmatched '[' in: {s!r}")

    def _find_paren_end(self, s: str) -> int:
        depth = 0
        for i, c in enumerate(s):
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    return i
        raise _ParseError(f"Unmatched '(' in: {s!r}")

    # ── condition block ─────────────────────────────────────────────────────

    def _parse_condition(self) -> ConditionDef:
        line = self._consume()
        stripped = line.strip()

        m = re.match(r"^condition\s+([a-z][a-z0-9_]*)\s*\(([^)]*)\)\s*\{$", stripped)
        if not m:
            raise _ParseError(f"Invalid condition declaration: {stripped!r}", line=self.pos)

        name = m.group(1)
        params_str = m.group(2).strip()

        params: list[ConditionParam] = []
        if params_str:
            for param_str in params_str.split(","):
                param_str = param_str.strip()
                pm = re.match(r"^([a-z][a-z0-9_]*)\s*:\s*(.+)$", param_str)
                if not pm:
                    raise _ParseError(f"Invalid condition parameter: {param_str!r}", line=self.pos)
                params.append(ConditionParam(name=pm.group(1), type=pm.group(2).strip()))

        body_lines: list[str] = []
        while True:
            line = self._consume()
            if line.strip() == "}":
                break
            body_lines.append(line.strip())

        return ConditionDef(name=name, parameters=params, expression="\n".join(body_lines).strip())


# ── public API ──────────────────────────────────────────────────────────────

def decompile(dsl: str) -> ParseSuccess | ParseError:
    try:
        return ParseSuccess(model=_Parser(dsl).parse())
    except _ParseError as e:
        return ParseError(error=e.message, line=e.line, column=e.column)
    except Exception as e:
        return ParseError(error=str(e))
