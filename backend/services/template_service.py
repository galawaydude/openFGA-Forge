import json
from pathlib import Path
from fga_forge import compile as fga_compile, CompileSuccess
from services.compiler_service import dict_to_model

TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
_templates: dict[str, dict] = {}


def load_templates() -> None:
    _templates.clear()
    if not TEMPLATE_DIR.exists():
        return
    for path in sorted(TEMPLATE_DIR.glob("*.json")):
        with open(path) as f:
            data = json.load(f)
        tid = path.stem
        model = dict_to_model(data["model"])
        result = fga_compile(model)
        dsl = result.dsl if isinstance(result, CompileSuccess) else "# Compilation error"
        _templates[tid] = {**data, "id": tid, "dsl": dsl}


def list_templates() -> list[dict]:
    return [
        {
            "id": tid,
            "name": t["name"],
            "description": t["description"],
            "tags": t.get("tags", []),
            "type_count": len(t["model"].get("types", [])),
            "relation_count": sum(
                len(td.get("relations", [])) for td in t["model"].get("types", [])
            ),
        }
        for tid, t in _templates.items()
    ]


def get_template(template_id: str) -> dict | None:
    return _templates.get(template_id)
