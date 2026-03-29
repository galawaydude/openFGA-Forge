"""
Tests for the fga_forge compiler (emitter + validator).
Based on spec.md Part 3 — Compiler Test Cases.
"""
import pytest
from fga_forge import (
    compile, validate, emit,
    FGAModel, TypeDef, RelationDef, ConditionDef, ConditionParam,
    TypeRestriction, DirectGrant, RelationRef, FromTraversal,
    UnionExpr, IntersectionExpr, CompileSuccess,
)


def test_actor_type_only():
    model = FGAModel(types=[TypeDef(name="user")])
    result = compile(model)
    assert isinstance(result, CompileSuccess)
    assert "type user" in result.dsl


def test_simple_direct_grant():
    model = FGAModel(types=[
        TypeDef(name="user"),
        TypeDef(name="group", relations=[
            RelationDef(name="member", expression=DirectGrant(grants=[
                TypeRestriction(type="user"),
            ])),
        ]),
    ])
    result = compile(model)
    assert isinstance(result, CompileSuccess)
    assert "define member: [user]" in result.dsl


def test_self_referencing_type_restriction():
    model = FGAModel(types=[
        TypeDef(name="user"),
        TypeDef(name="group", relations=[
            RelationDef(name="member", expression=DirectGrant(grants=[
                TypeRestriction(type="user"),
                TypeRestriction(type="group", relation="member"),
            ])),
        ]),
    ])
    result = compile(model)
    assert isinstance(result, CompileSuccess)
    assert "define member: [user, group#member]" in result.dsl


def test_union_of_direct_and_ref():
    model = FGAModel(types=[
        TypeDef(name="user"),
        TypeDef(name="organization", relations=[
            RelationDef(name="owner", expression=DirectGrant(grants=[TypeRestriction(type="user")])),
            RelationDef(name="admin", expression=UnionExpr(children=[
                DirectGrant(grants=[TypeRestriction(type="user")]),
                RelationRef(relation="owner"),
            ])),
        ]),
    ])
    result = compile(model)
    assert isinstance(result, CompileSuccess)
    assert "define admin: [user] or owner" in result.dsl


def test_from_traversal():
    model = FGAModel(types=[
        TypeDef(name="user"),
        TypeDef(name="organization", relations=[
            RelationDef(name="document_manager", expression=DirectGrant(grants=[TypeRestriction(type="user")])),
        ]),
        TypeDef(name="document", relations=[
            RelationDef(name="organization", expression=DirectGrant(grants=[TypeRestriction(type="organization")])),
            RelationDef(name="editor", expression=FromTraversal(
                source_relation="document_manager", parent_relation="organization"
            )),
        ]),
    ])
    result = compile(model)
    assert isinstance(result, CompileSuccess)
    assert "define editor: document_manager from organization" in result.dsl


def test_intersection():
    model = FGAModel(types=[
        TypeDef(name="user"),
        TypeDef(name="role", relations=[
            RelationDef(name="assignee", expression=DirectGrant(grants=[TypeRestriction(type="user")])),
            RelationDef(name="can_view_project", expression=DirectGrant(grants=[TypeRestriction(type="user")])),
        ]),
        TypeDef(name="project", relations=[
            RelationDef(name="role", expression=DirectGrant(grants=[TypeRestriction(type="role")])),
            RelationDef(name="assignee", expression=DirectGrant(grants=[TypeRestriction(type="user")])),
            RelationDef(name="reader", expression=IntersectionExpr(children=[
                RelationRef(relation="assignee"),
                FromTraversal(source_relation="can_view_project", parent_relation="role"),
            ])),
        ]),
    ])
    result = compile(model)
    assert isinstance(result, CompileSuccess)
    assert "define reader: assignee and can_view_project from role" in result.dsl


def test_parenthesized_intersection():
    model = FGAModel(types=[
        TypeDef(name="customer"),
        TypeDef(name="employee"),
        TypeDef(name="bank", relations=[
            RelationDef(name="transfer_limit_policy", expression=DirectGrant(
                grants=[TypeRestriction(type="employee")]
            )),
        ]),
        TypeDef(name="account", relations=[
            RelationDef(name="bank", expression=DirectGrant(grants=[TypeRestriction(type="bank")])),
            RelationDef(name="owner", expression=DirectGrant(grants=[TypeRestriction(type="customer")])),
            RelationDef(name="account_manager", expression=DirectGrant(grants=[TypeRestriction(type="employee")])),
            RelationDef(name="delegate", expression=DirectGrant(grants=[TypeRestriction(type="customer")])),
            RelationDef(name="can_make_bank_transfer", expression=IntersectionExpr(children=[
                UnionExpr(children=[
                    RelationRef(relation="owner"),
                    RelationRef(relation="account_manager"),
                    RelationRef(relation="delegate"),
                ]),
                FromTraversal(source_relation="transfer_limit_policy", parent_relation="bank"),
            ])),
        ]),
    ])
    result = compile(model)
    assert isinstance(result, CompileSuccess)
    assert "(owner or account_manager or delegate) and transfer_limit_policy from bank" in result.dsl


def test_wildcard():
    model = FGAModel(types=[
        TypeDef(name="user"),
        TypeDef(name="resource", relations=[
            RelationDef(name="can_edit_project", expression=DirectGrant(grants=[
                TypeRestriction(type="user", wildcard=True),
            ])),
        ]),
    ])
    result = compile(model)
    assert isinstance(result, CompileSuccess)
    assert "define can_edit_project: [user:*]" in result.dsl


def test_conditional_type_restriction():
    model = FGAModel(
        types=[
            TypeDef(name="user"),
            TypeDef(name="document", relations=[
                RelationDef(name="viewer", expression=DirectGrant(grants=[
                    TypeRestriction(type="user"),
                    TypeRestriction(type="user", condition="temporal_access"),
                ])),
            ]),
        ],
        conditions=[
            ConditionDef(
                name="temporal_access",
                parameters=[
                    ConditionParam(name="current_time", type="timestamp"),
                    ConditionParam(name="grant_time", type="timestamp"),
                    ConditionParam(name="grant_duration", type="duration"),
                ],
                expression="current_time < grant_time + grant_duration",
            ),
        ],
    )
    result = compile(model)
    assert isinstance(result, CompileSuccess)
    assert "define viewer: [user, user with temporal_access]" in result.dsl
    assert "condition temporal_access" in result.dsl


def test_pure_ref():
    model = FGAModel(types=[
        TypeDef(name="user"),
        TypeDef(name="organization", relations=[
            RelationDef(name="billing_manager", expression=DirectGrant(grants=[TypeRestriction(type="user")])),
            RelationDef(name="can_edit_billing", expression=RelationRef(relation="billing_manager")),
        ]),
    ])
    result = compile(model)
    assert isinstance(result, CompileSuccess)
    assert "define can_edit_billing: billing_manager" in result.dsl
