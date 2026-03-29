"""
Tests for template loading and compilation.
"""
import pytest
from services.template_service import load_templates, list_templates, get_template


def test_all_templates_load():
    load_templates()
    templates = list_templates()
    assert len(templates) == 8


def test_template_ids():
    load_templates()
    ids = {t["id"] for t in list_templates()}
    expected = {
        "github_rbac", "saas_entitlements", "multi_tenant",
        "team_hierarchy", "bank_transfers", "dynamic_roles",
        "lxd_server", "asset_management",
    }
    assert ids == expected


def test_template_dsl_compiles():
    load_templates()
    for t in list_templates():
        detail = get_template(t["id"])
        assert detail["dsl"] != "# Compilation error", f"Template {t['id']} failed to compile"
