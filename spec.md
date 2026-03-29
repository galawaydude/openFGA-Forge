# FGA Forge — Design Document (HLD + LLD)

## Purpose

This document specifies the complete logic for FGA Forge: a visual no-code builder that lets users construct OpenFGA authorization models on a graph canvas and compile them into valid OpenFGA DSL. It covers the data model, the mapping between visual elements and DSL constructs, the compiler pipeline, and validation rules.

This document is **logic-only** — it does not prescribe UI framework choices, API endpoints, or deployment. It is the source of truth for what the system does and how the compiler works.

---

# Part 1 — High-Level Design

## 1.1 System Overview

FGA Forge has three logical layers:

```
┌─────────────────────────────────────┐
│          Canvas Layer               │
│  (visual graph: nodes + edges)      │
├─────────────────────────────────────┤
│     Intermediate Representation     │
│  (structured model state — the IR)  │
├─────────────────────────────────────┤
│          Compiler Layer             │
│  (IR → OpenFGA DSL text output)     │
└─────────────────────────────────────┘
```

**Canvas Layer** — The user interacts with a graph editor. They create type nodes, connect them with edges, and configure relation definitions through node/edge property panels. Every visual action mutates the IR.

**Intermediate Representation (IR)** — A structured data model that captures the full state of the authorization model. It is the canonical representation — the canvas reads from it to render, and the compiler reads from it to emit DSL. The IR is serializable to JSON for persistence.

**Compiler Layer** — A pure function: `IR → string`. It walks the IR, resolves all relation definitions, and emits syntactically correct OpenFGA DSL text. It has no side effects and no dependency on the canvas.

## 1.2 Core Concept Mapping

Every OpenFGA DSL construct maps to a visual element:

| DSL Construct | Visual Element | User Action |
|---|---|---|
| `type user` | Node (actor type) | Drop a node, mark as "actor" (no relations) |
| `type document` | Node (resource type) | Drop a node, add relations in its panel |
| `define member: [user]` | Edge from `user` → `document` on relation `member` | Drag edge, select target relation |
| `[user, group#member]` | Multiple edges arriving at the same relation port | Drag multiple edges to same relation |
| `[user:*]` | Wildcard toggle on an edge | Toggle "all instances" in edge config |
| `[user with condition]` | Condition attachment on an edge | Attach a condition from the conditions palette |
| `define admin: [user] or owner` | Relation config: direct grants + union with sibling relation | In the relation panel, add "or owner" |
| `define reader: assignee and can_view from role` | Relation config: intersection of two sub-expressions | In the relation panel, set operator to "and" |
| `define editor: document_manager from organization` | "From" traversal relation | Select "from" mode, pick parent relation + source relation |
| `define can_edit: editor` | Pure reference to sibling relation | In relation panel, reference another relation |
| `condition name(params) { expr }` | Condition definition in the conditions palette | Fill out a form: name, params, expression |
| `# comment` | Comment field on a relation | Optional text field in relation config |

## 1.3 Data Flow

```
User drags edge from "user" node to "group" node, selects relation "member"
  │
  ▼
Canvas dispatches: addEdge({ source: "user", target: "group", relation: "member" })
  │
  ▼
IR updates: group.relations["member"].directGrants adds { type: "user" }
  │
  ▼
Compiler reads IR, emits:
    type group
      relations
        define member: [user]
```

Every canvas interaction follows this pattern: visual action → IR mutation → compiler can re-run at any time to produce current DSL.

## 1.4 Module Breakdown

### Module 1: Node Manager
- Create, rename, delete type nodes
- Each node has: name (string, supports hyphens), a flag for whether it's an actor type (no relations), and an ordered list of relation definitions
- Node names must be unique, non-empty, match pattern `[a-z][a-z0-9_-]*`

### Module 2: Relation Editor
- Each relation on a node has a definition that is an **expression tree** (see LLD)
- The editor lets users build this tree through a panel UI
- Supports: direct grants, union (or), intersection (and), from traversal, pure reference, and parenthesized grouping

### Module 3: Edge Manager
- Edges represent direct grant connections between types
- Each edge carries: source type, target type, target relation name, optional `#relation` suffix on source, optional condition reference, optional wildcard flag
- Multiple edges can target the same relation (they become comma-separated entries in the type restriction list)

### Module 4: Condition Palette
- A separate panel (not a graph node) for defining conditions
- Each condition has: name, parameter list (name + type), CEL expression body
- Supported parameter types: `string`, `int`, `uint`, `double`, `bool`, `duration`, `timestamp`, `list<T>`, `map<T>`
- Conditions are referenced by edges via "with" attachments

### Module 5: Compiler
- Pure function: takes the full IR, returns a string of OpenFGA DSL
- Phases: validate → sort types → emit header → emit types → emit conditions
- Detailed in LLD Part 2

### Module 6: Validator
- Runs before compilation to catch errors
- Checks: dangling references, circular `from` chains, invalid type restrictions, undeclared conditions, duplicate relation names, empty type names

---

# Part 2 — Low-Level Design

## 2.1 Intermediate Representation (IR) Schema

The IR is the single source of truth. Everything below is described as a logical schema — the actual implementation can use classes, interfaces, or plain objects.

### Top-Level Model

```
Model {
  schemaVersion: string              // always "1.1" for now
  types: OrderedMap<string, TypeDef> // preserves insertion order
  conditions: OrderedMap<string, ConditionDef>
}
```

### TypeDef

```
TypeDef {
  name: string                       // e.g. "user", "document", "asset-category"
  relations: OrderedMap<string, RelationDef>
  comments: Map<string, string>      // optional per-relation comments
}
```

A type with zero relations is an **actor type** (e.g. `type user`). The compiler emits just `type user` with no `relations` block.

### RelationDef

Each relation has a **definition expression** — a tree that describes how the relation is computed.

```
RelationDef {
  name: string                       // e.g. "member", "can_edit", "admin"
  expression: RelationExpression     // the definition tree
  comment: string | null             // optional comment emitted as "# ..."
}
```

### RelationExpression (the core abstraction)

This is a discriminated union (tagged union). Every node in the expression tree is one of these variants:

```
RelationExpression =
  | DirectGrant
  | RelationRef
  | FromTraversal
  | Union
  | Intersection

DirectGrant {
  kind: "direct"
  grants: Array<TypeRestriction>     // becomes [user, group#member, user:*, ...]
}

TypeRestriction {
  type: string                       // e.g. "user", "group", "organization"
  relation: string | null            // e.g. "member" → group#member. null = bare type
  wildcard: boolean                  // true → user:*
  condition: string | null           // e.g. "temporal_access" → user with temporal_access
}

RelationRef {
  kind: "ref"
  relation: string                   // name of another relation on the SAME type
}

FromTraversal {
  kind: "from"
  sourceRelation: string             // relation on the PARENT type to check
  parentRelation: string             // relation on THIS type that points to the parent
}

Union {
  kind: "union"
  children: Array<RelationExpression>  // 2+ children combined with "or"
}

Intersection {
  kind: "intersection"
  children: Array<RelationExpression>  // 2+ children combined with "and"
}
```

### ConditionDef

```
ConditionDef {
  name: string                       // e.g. "temporal_access"
  parameters: Array<ConditionParam>
  expression: string                 // raw CEL expression string
}

ConditionParam {
  name: string                       // e.g. "current_time"
  type: string                       // e.g. "timestamp", "int", "duration"
}
```

## 2.2 Expression Tree Examples

To validate the IR design, here's how every pattern from the studied models maps to an expression tree.

### Example 1: Simple direct grant
DSL: `define member: [user, group#member]`

```
DirectGrant {
  kind: "direct",
  grants: [
    { type: "user", relation: null, wildcard: false, condition: null },
    { type: "group", relation: "member", wildcard: false, condition: null }
  ]
}
```

### Example 2: Direct + union with sibling
DSL: `define admin: [user] or owner`

```
Union {
  kind: "union",
  children: [
    DirectGrant {
      kind: "direct",
      grants: [{ type: "user", relation: null, wildcard: false, condition: null }]
    },
    RelationRef { kind: "ref", relation: "owner" }
  ]
}
```

### Example 3: From traversal
DSL: `define editor: document_manager from organization`

```
FromTraversal {
  kind: "from",
  sourceRelation: "document_manager",
  parentRelation: "organization"
}
```

### Example 4: Direct + union + from
DSL: `define can_edit: [identity, service_account, group#member] or can_edit_identities from server`

```
Union {
  kind: "union",
  children: [
    DirectGrant {
      kind: "direct",
      grants: [
        { type: "identity", relation: null, wildcard: false, condition: null },
        { type: "service_account", relation: null, wildcard: false, condition: null },
        { type: "group", relation: "member", wildcard: false, condition: null }
      ]
    },
    FromTraversal {
      kind: "from",
      sourceRelation: "can_edit_identities",
      parentRelation: "server"
    }
  ]
}
```

### Example 5: Intersection (and)
DSL: `define can_view_project: assignee and can_view_project from role`

```
Intersection {
  kind: "intersection",
  children: [
    RelationRef { kind: "ref", relation: "assignee" },
    FromTraversal {
      kind: "from",
      sourceRelation: "can_view_project",
      parentRelation: "role"
    }
  ]
}
```

### Example 6: Parenthesized group with intersection
DSL: `define can_make_bank_transfer: (owner or account_manager or delegate) and transfer_limit_policy from bank`

```
Intersection {
  kind: "intersection",
  children: [
    Union {
      kind: "union",
      children: [
        RelationRef { kind: "ref", relation: "owner" },
        RelationRef { kind: "ref", relation: "account_manager" },
        RelationRef { kind: "ref", relation: "delegate" }
      ]
    },
    FromTraversal {
      kind: "from",
      sourceRelation: "transfer_limit_policy",
      parentRelation: "bank"
    }
  ]
}
```

### Example 7: Conditional type restriction
DSL: `define viewer: [user, user with temporal_access]`

```
DirectGrant {
  kind: "direct",
  grants: [
    { type: "user", relation: null, wildcard: false, condition: null },
    { type: "user", relation: null, wildcard: false, condition: "temporal_access" }
  ]
}
```

### Example 8: Wildcard
DSL: `define can_edit_project: [user:*]`

```
DirectGrant {
  kind: "direct",
  grants: [
    { type: "user", relation: null, wildcard: true, condition: null }
  ]
}
```

### Example 9: Multiple conditional + unconditional on same type#relation
DSL: `define has_feature: [plan#subscriber, plan#subscriber with is_below_collaborator_limit, plan#subscriber with is_below_row_sync_limit]`

```
DirectGrant {
  kind: "direct",
  grants: [
    { type: "plan", relation: "subscriber", wildcard: false, condition: null },
    { type: "plan", relation: "subscriber", wildcard: false, condition: "is_below_collaborator_limit" },
    { type: "plan", relation: "subscriber", wildcard: false, condition: "is_below_row_sync_limit" }
  ]
}
```

### Example 10: Intersection as tenancy guard
DSL: `define reader: [application] and application from organization`

```
Intersection {
  kind: "intersection",
  children: [
    DirectGrant {
      kind: "direct",
      grants: [{ type: "application", relation: null, wildcard: false, condition: null }]
    },
    FromTraversal {
      kind: "from",
      sourceRelation: "application",
      parentRelation: "organization"
    }
  ]
}
```

### Example 11: Pure reference (alias)
DSL: `define can_edit_billing: billing_manager`

```
RelationRef {
  kind: "ref",
  relation: "billing_manager"
}
```

### Example 12: Deep union chain (role subsumption)
DSL: `define reader: [user, team#member] or triager or repo_reader from owner`

```
Union {
  kind: "union",
  children: [
    DirectGrant {
      kind: "direct",
      grants: [
        { type: "user", relation: null, wildcard: false, condition: null },
        { type: "team", relation: "member", wildcard: false, condition: null }
      ]
    },
    RelationRef { kind: "ref", relation: "triager" },
    FromTraversal {
      kind: "from",
      sourceRelation: "repo_reader",
      parentRelation: "owner"
    }
  ]
}
```

## 2.3 Compiler Pipeline

The compiler is a pure function with four phases:

```
IR  →  [Validate]  →  [Sort]  →  [Emit]  →  DSL string
```

### Phase 1: Validate

Before emitting anything, check for errors. The compiler should return either a DSL string or a list of validation errors — never both.

**Validation rules:**

1. **No empty type names** — every type must have a non-empty name matching `[a-z][a-z0-9_-]*`
2. **No duplicate type names** — across the entire model
3. **No duplicate relation names** — within a single type
4. **All RelationRef targets exist** — if a relation references "owner", the same type must define "owner"
5. **All FromTraversal parent relations exist** — if `editor: document_manager from organization`, then the current type must define a relation called "organization"
6. **All FromTraversal source relations exist on the target type** — if `organization` points to type `organization`, then type `organization` must define `document_manager`
7. **All type references in DirectGrant exist** — if `[user]` is specified, type `user` must exist in the model
8. **All relation references in TypeRestriction exist** — if `[group#member]`, type `group` must define relation `member`
9. **All condition references exist** — if `user with temporal_access`, condition `temporal_access` must be defined
10. **No circular `from` chains** — `A from B from C from A` is invalid (but A referencing B which references C via `from` is fine — cycles are only invalid if they form a `from`-only loop within a single type)
11. **Intersection children constraints** — OpenFGA requires that an `and` expression has exactly 2 children. Each child must be either a DirectGrant, a RelationRef, a FromTraversal, or a Union (but not another Intersection at the same level — no `A and B and C`, must be restructured)
12. **Union children constraint** — must have 2+ children
13. **Condition parameter types are valid** — must be one of the recognized CEL types
14. **Condition names match pattern** — `[a-z][a-z0-9_]*` (no hyphens in condition names)

### Phase 2: Sort Types

Types are emitted in a stable order. The default strategy: emit types in the order they were added to the canvas (insertion order). The user can also manually reorder types via drag-and-drop in a type list panel.

Actor types (those with zero relations) are conventionally placed first, but this is not enforced by OpenFGA — it's a readability convention.

### Phase 3: Emit

Walk each type and emit DSL. The emitter is a recursive function over the expression tree.

**Emit header:**
```
model
  schema 1.1
```

**Emit each type:**
```
type <name>
  relations
    <for each relation>
    define <name>: <emit_expression(relation.expression)>
```

If a type has zero relations, emit only `type <name>` with no `relations` block.

**Emit each condition (after all types):**
```
  condition <name>(<param1>: <type1>, <param2>: <type2>) {
    <expression>
  }
```

### Expression Emitter Rules

The `emit_expression` function is recursive:

```
emit_expression(expr):
  match expr.kind:

    "direct":
      items = []
      for grant in expr.grants:
        s = grant.type
        if grant.wildcard:
          s = s + ":*"
        elif grant.relation:
          s = s + "#" + grant.relation
        if grant.condition:
          s = s + " with " + grant.condition
        items.append(s)
      return "[" + ", ".join(items) + "]"

    "ref":
      return expr.relation

    "from":
      return expr.sourceRelation + " from " + expr.parentRelation

    "union":
      parts = [emit_expression(child) for child in expr.children]
      return " or ".join(parts)

    "intersection":
      parts = []
      for child in expr.children:
        if child.kind == "union":
          parts.append("(" + emit_expression(child) + ")")
        else:
          parts.append(emit_expression(child))
      return " and ".join(parts)
```

**Key rules:**
- Union children are joined with ` or ` — no parentheses needed at the top level
- Intersection children are joined with ` and `
- If an intersection child is a union, it MUST be wrapped in parentheses: `(A or B) and C`
- Direct grants are always emitted as `[...]` with comma-separated entries
- A `from` traversal is emitted as `sourceRelation from parentRelation`
- A `ref` is emitted as just the relation name (bare identifier)

### Indentation Rules

OpenFGA DSL uses strict indentation:
- `model` — zero indent
- `schema 1.1` — 2 spaces
- `type <name>` — zero indent
- `relations` — 2 spaces
- `define <name>: <expr>` — 4 spaces
- `condition <name>(...) {` — 2 spaces (from root level)
- condition body — 4 spaces (additional 2 from condition block, so visually 4-6 depending on style)

The exact indentation:
```
model\n
  schema 1.1\n
type user\n
type document\n
  relations\n
    define viewer: [user]\n
  condition temporal_access(...) {\n
    current_time < grant_time + grant_duration\n
  }\n
```

Note: conditions are emitted at the root indentation level (zero indent for `condition` keyword), not nested under any type. They are global to the model.

Corrected indentation for conditions:
```
model
  schema 1.1
type user
type document
  relations
    define viewer: [user with temporal_access]
condition temporal_access(current_time: timestamp, grant_time: timestamp, grant_duration: duration) {
  current_time < grant_time + grant_duration
}
```

## 2.4 Edge-to-IR Mapping (Canvas → IR)

When a user creates an edge on the canvas, the system must translate it into an IR mutation. Here are the mappings:

### Creating a direct grant edge

User drags from type A to type B, targeting relation R on B.

**Case 1: Bare type reference**
- Edge config: source type = A, no relation suffix, no wildcard, no condition
- IR mutation: add `{ type: "A", relation: null, wildcard: false, condition: null }` to `B.relations[R].expression`
- If R's expression is already a DirectGrant, append to its grants array
- If R's expression is a Union that starts with a DirectGrant, append to that DirectGrant's grants
- If R's expression is something else (ref, from), wrap in a Union: `Union([existing, DirectGrant([new_grant])])`

**Case 2: Type#relation reference**
- Edge config: source type = A, relation suffix = "member"
- IR mutation: add `{ type: "A", relation: "member", wildcard: false, condition: null }`

**Case 3: Wildcard**
- Edge config: source type = A, wildcard = true
- IR mutation: add `{ type: "A", relation: null, wildcard: true, condition: null }`

**Case 4: Conditional**
- Edge config: source type = A, condition = "temporal_access"
- IR mutation: add `{ type: "A", relation: null, wildcard: false, condition: "temporal_access" }`

**Case 5: Type#relation + condition**
- Edge config: source type = A, relation = "subscriber", condition = "is_below_limit"
- IR mutation: add `{ type: "A", relation: "subscriber", wildcard: false, condition: "is_below_limit" }`

### Creating a "from" traversal

User selects a relation R on type B, chooses "from traversal" mode, picks parent relation P (a relation on B that points to another type), and picks source relation S (a relation on the type that P points to).

- IR mutation: set `B.relations[R].expression` to `FromTraversal { sourceRelation: S, parentRelation: P }`
- Or if R already has other sub-expressions, wrap in Union/Intersection as appropriate

### Creating a computed relation (union/intersection)

User opens the relation panel for relation R on type B, adds references to sibling relations.

- For "or": wrap children in `Union { children: [...] }`
- For "and": wrap children in `Intersection { children: [...] }`
- Children can be any mix of DirectGrant, RelationRef, FromTraversal

### Creating a pure reference

User opens relation panel for R on type B, sets it to reference relation S on the same type.

- IR mutation: set `B.relations[R].expression` to `RelationRef { relation: S }`

## 2.5 Relation Definition Categories

For UI purposes, relations can be categorized (these categories are display hints, not compiler constructs):

| Category | Tag | Condition | Example |
|---|---|---|---|
| Direct grant | `direct` | Expression is or starts with a DirectGrant | `define member: [user]` |
| Computed | `computed` | Expression is a Union or Intersection of refs/from | `define can_view: viewer or editor` |
| From traversal | `from` | Expression is or contains a FromTraversal | `define editor: doc_mgr from org` |
| Permission | `perm` | Expression is a bare RelationRef to a sibling | `define can_edit_billing: billing_manager` |
| Conditional | `conditional` | Any DirectGrant contains a condition | `define viewer: [user with access]` |
| Hybrid | (multiple tags) | Mix of categories in a Union | `define admin: [user] or owner` |

## 2.6 Validation Error Types

```
ValidationError {
  code: string          // machine-readable error code
  message: string       // human-readable description
  typeName: string      // which type the error is on (if applicable)
  relationName: string  // which relation (if applicable)
  path: string          // dot-path into the expression tree (e.g. "union.children[1].from")
}
```

Error codes:

| Code | Description |
|---|---|
| `EMPTY_TYPE_NAME` | Type name is empty or whitespace |
| `INVALID_TYPE_NAME` | Type name doesn't match `[a-z][a-z0-9_-]*` |
| `DUPLICATE_TYPE_NAME` | Two types share the same name |
| `DUPLICATE_RELATION_NAME` | Two relations in the same type share a name |
| `UNDEFINED_TYPE_REF` | DirectGrant references a type that doesn't exist |
| `UNDEFINED_RELATION_REF` | RelationRef references a relation not on this type |
| `UNDEFINED_FROM_PARENT` | FromTraversal's parentRelation not on this type |
| `UNDEFINED_FROM_SOURCE` | FromTraversal's sourceRelation not on the target type |
| `UNDEFINED_GRANT_RELATION` | TypeRestriction's #relation not defined on the referenced type |
| `UNDEFINED_CONDITION` | TypeRestriction references a condition not in the model |
| `INVALID_CONDITION_NAME` | Condition name doesn't match `[a-z][a-z0-9_]*` |
| `INVALID_CONDITION_PARAM_TYPE` | Condition parameter uses an unrecognized type |
| `CIRCULAR_FROM_CHAIN` | `from` traversals form a cycle |
| `INTERSECTION_CHILD_COUNT` | Intersection has != 2 children |
| `UNION_CHILD_COUNT` | Union has < 2 children |
| `EMPTY_DIRECT_GRANT` | DirectGrant has zero entries in grants array |
| `EMPTY_EXPRESSION` | Relation has no expression defined |

## 2.7 JSON Serialization

The IR is serialized to JSON for persistence. The JSON schema mirrors the IR exactly. Example for the organization/document/role model:

```json
{
  "schemaVersion": "1.1",
  "types": [
    {
      "name": "user",
      "relations": []
    },
    {
      "name": "group",
      "relations": [
        {
          "name": "member",
          "expression": {
            "kind": "direct",
            "grants": [
              { "type": "user", "relation": null, "wildcard": false, "condition": null },
              { "type": "group", "relation": "member", "wildcard": false, "condition": null }
            ]
          },
          "comment": null
        }
      ]
    },
    {
      "name": "role",
      "relations": [
        {
          "name": "assignee",
          "expression": {
            "kind": "direct",
            "grants": [
              { "type": "user", "relation": null, "wildcard": false, "condition": null },
              { "type": "role", "relation": "assignee", "wildcard": false, "condition": null },
              { "type": "group", "relation": "member", "wildcard": false, "condition": null }
            ]
          },
          "comment": null
        }
      ]
    },
    {
      "name": "organization",
      "relations": [
        {
          "name": "admin",
          "expression": {
            "kind": "direct",
            "grants": [
              { "type": "user", "relation": null, "wildcard": false, "condition": null },
              { "type": "role", "relation": "assignee", "wildcard": false, "condition": null }
            ]
          },
          "comment": null
        },
        {
          "name": "user_manager",
          "expression": {
            "kind": "union",
            "children": [
              {
                "kind": "direct",
                "grants": [
                  { "type": "role", "relation": "assignee", "wildcard": false, "condition": null }
                ]
              },
              { "kind": "ref", "relation": "admin" }
            ]
          },
          "comment": null
        },
        {
          "name": "can_invite_user",
          "expression": {
            "kind": "ref",
            "relation": "user_manager"
          },
          "comment": null
        }
      ]
    },
    {
      "name": "document",
      "relations": [
        {
          "name": "organization",
          "expression": {
            "kind": "direct",
            "grants": [
              { "type": "organization", "relation": null, "wildcard": false, "condition": null }
            ]
          },
          "comment": null
        },
        {
          "name": "editor",
          "expression": {
            "kind": "from",
            "sourceRelation": "document_manager",
            "parentRelation": "organization"
          },
          "comment": null
        },
        {
          "name": "can_view",
          "expression": {
            "kind": "union",
            "children": [
              { "kind": "ref", "relation": "viewer" },
              { "kind": "ref", "relation": "editor" }
            ]
          },
          "comment": null
        }
      ]
    }
  ],
  "conditions": []
}
```

## 2.8 OpenFGA DSL Grammar Reference

For compiler correctness, here is the complete grammar the emitter must produce:

```
model         = "model" NL INDENT "schema 1.1" NL type_defs condition_defs
type_def      = "type" SP name NL [relations_block]
relations_block = INDENT "relations" NL relation_defs
relation_def  = INDENT INDENT "define" SP name ":" SP relation_expr NL
relation_expr = direct_expr
              | ref_expr
              | from_expr
              | union_expr
              | intersection_expr

direct_expr   = "[" type_restriction ("," SP type_restriction)* "]"
ref_expr      = name
from_expr     = name SP "from" SP name
union_expr    = relation_expr SP "or" SP relation_expr (SP "or" SP relation_expr)*
intersect_expr = relation_operand SP "and" SP relation_operand
relation_operand = "(" union_expr ")" | direct_expr | ref_expr | from_expr

type_restriction = type_ref [SP "with" SP condition_name]
type_ref      = name                           // bare type: user
              | name "#" name                   // type#relation: group#member
              | name ":*"                       // wildcard: user:*

condition_def = "condition" SP name "(" param_list ")" SP "{" NL
                INDENT expr NL
                "}" NL
param_list    = param ("," SP param)*
param         = name ":" SP param_type
param_type    = "string" | "int" | "uint" | "double" | "bool"
              | "duration" | "timestamp"
              | "list<" param_type ">"
              | "map<" param_type ">"

name          = [a-z][a-z0-9_-]*               // for types (hyphens allowed)
              | [a-z][a-z0-9_]*                 // for relations, conditions (no hyphens)
SP            = " "
NL            = "\n"
INDENT        = "  "                            // 2 spaces
```

**Operator precedence:**
- `and` binds tighter than `or`
- Parentheses are required when a union is a child of an intersection
- Parentheses are NOT used in any other case in the DSL
- A relation definition can have at most one `and` at the top level
- Each side of `and` can be a parenthesized `or` group, a direct grant, a ref, or a from

## 2.9 Comment Emission

If a relation has a comment, it is emitted on the line above the `define`:

```
    # Grants permission to view the document.
    define can_view: viewer or editor
```

If a type-level comment exists, it is emitted above the type:

```
# Documents belong to an organization
type document
```

Comments use `#` followed by a space, then the comment text. Multi-line comments use multiple `#` lines.

---

# Part 3 — Compiler Test Cases

These are the expected inputs (IR as JSON) and outputs (DSL strings) that the compiler must handle correctly. They are derived directly from the models studied.

## Test 1: Actor type only
Input: `{ types: [{ name: "user", relations: [] }], conditions: [] }`
Output:
```
model
  schema 1.1
type user
```

## Test 2: Simple direct grant
Input: group with `member: [user]`
Output:
```
type group
  relations
    define member: [user]
```

## Test 3: Self-referencing type restriction
Input: group with `member: [user, group#member]`
Output:
```
type group
  relations
    define member: [user, group#member]
```

## Test 4: Union of direct + ref
Input: org with `admin: [user] or owner`
Output:
```
    define admin: [user] or owner
```

## Test 5: From traversal
Input: document with `editor: document_manager from organization`
Output:
```
    define editor: document_manager from organization
```

## Test 6: Intersection
Input: `reader: assignee and can_view_project from role`
Output:
```
    define reader: assignee and can_view_project from role
```

## Test 7: Parenthesized intersection
Input: `can_make_transfer: (owner or account_manager or delegate) and transfer_limit_policy from bank`
Output:
```
    define can_make_bank_transfer: (owner or account_manager or delegate) and transfer_limit_policy from bank
```

## Test 8: Conditional type restriction
Input: `viewer: [user, user with temporal_access]`
Output:
```
    define viewer: [user, user with temporal_access]
```

## Test 9: Wildcard
Input: `can_edit_project: [user:*]`
Output:
```
    define can_edit_project: [user:*]
```

## Test 10: Condition definition
Input: condition `temporal_access(current_time: timestamp, grant_time: timestamp, grant_duration: duration) { current_time < grant_time + grant_duration }`
Output:
```
condition temporal_access(current_time: timestamp, grant_time: timestamp, grant_duration: duration) {
  current_time < grant_time + grant_duration
}
```

## Test 11: Full model (entitlement pattern)
Input: user → organization (member) → plan (subscriber) → feature (has_feature with conditions)
Output:
```
model
  schema 1.1
type user
type organization
  relations
    define member: [user]
type plan
  relations
    define subscriber: [organization#member]
type feature
  relations
    define has_feature: [plan#subscriber, plan#subscriber with is_below_collaborator_limit, plan#subscriber with is_below_row_sync_limit, plan#subscriber with is_below_page_history_days_limit]
condition is_below_collaborator_limit(collaborator_count: int, collaborator_limit: int) {
  collaborator_count <= collaborator_limit
}
condition is_below_row_sync_limit(row_sync_count: int, row_sync_limit: int) {
  row_sync_count <= row_sync_limit
}
condition is_below_page_history_days_limit(page_history_days_count: int, page_history_days_limit: int) {
  page_history_days_count <= page_history_days_limit
}
```

## Test 12: Hyphenated type name
Input: `asset-category` type
Output:
```
type asset-category
  relations
    define viewer: [role#assignee] or commenter or asset_viewer from org
```

## Test 13: Multi-type actor model
Input: employee, customer, application as actor types
Output:
```
type employee
type customer
type application
```

## Test 14: Deep union chain
Input: `reader: [user, team#member] or triager or repo_reader from owner`
Output:
```
    define reader: [user, team#member] or triager or repo_reader from owner
```

## Test 15: Intersection as tenancy guard
Input: `reader: [application] and application from organization`
Output:
```
    define reader: [application] and application from organization
```

---

# Part 4 — Decompiler (DSL → IR)

For import functionality, FGA Forge needs a **parser** that reads OpenFGA DSL text and produces the IR. This is the reverse of the compiler.

## 4.1 Parser Phases

```
DSL string → [Tokenize] → [Parse] → IR
```

### Tokenize
Break the DSL into tokens:
- Keywords: `model`, `schema`, `type`, `relations`, `define`, `or`, `and`, `from`, `with`, `condition`
- Identifiers: type names, relation names, condition names
- Symbols: `[`, `]`, `(`, `)`, `{`, `}`, `:`, `#`, `,`, `*`
- Literals: numbers, strings (for schema version)
- Comments: `#` to end of line
- Newlines and indentation (significant for structure)

### Parse
Walk tokens and build the IR:

1. Expect `model` → `schema 1.1`
2. Loop: expect `type <name>` → optional `relations` block → loop relation definitions
3. For each `define <name>:` → parse `relation_expr`
4. After all types, loop: `condition <name>(<params>) { <expr> }`

### Relation Expression Parser

The expression parser handles operator precedence:

```
parse_relation_expr():
  left = parse_relation_operand()
  while peek() == "and":
    consume("and")
    right = parse_relation_operand()
    left = Intersection(left, right)
  return left

parse_relation_operand():
  if peek() == "(":
    consume("(")
    expr = parse_union_expr()
    consume(")")
    return expr
  return parse_union_expr()

parse_union_expr():
  left = parse_atom()
  while peek() == "or":
    consume("or")
    right = parse_atom()
    left = Union(left, right)  // or collect into Union.children
  return left

parse_atom():
  if peek() == "[":
    return parse_direct_grant()
  name = consume(IDENTIFIER)
  if peek() == "from":
    consume("from")
    parent = consume(IDENTIFIER)
    return FromTraversal(name, parent)
  return RelationRef(name)

parse_direct_grant():
  consume("[")
  grants = []
  loop:
    type_name = consume(IDENTIFIER)
    if peek() == "#":
      consume("#")
      relation = consume(IDENTIFIER)
    elif peek() == ":":
      consume(":")
      consume("*")
      → wildcard = true
    if peek_word() == "with":
      consume("with")
      condition = consume(IDENTIFIER)
    grants.append(TypeRestriction(...))
    if peek() != ",": break
    consume(",")
  consume("]")
  return DirectGrant(grants)
```

This parser is needed for the "Import DSL" feature, allowing users to paste existing OpenFGA models and see them rendered on the canvas.
