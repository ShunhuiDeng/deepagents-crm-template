import ast
import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_script(script_name: str) -> ModuleType:
    path = PROJECT_ROOT / "scripts" / script_name
    spec = importlib.util.spec_from_file_location(script_name.removesuffix(".py"), path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Rows:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def fetchall(self) -> list[dict[str, Any]]:
        return self.rows


class _ExistingTablesConnection:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.calls: list[tuple[Any, Any]] = []

    def execute(self, query: Any, params: Any = None) -> _Rows:
        self.calls.append((query, params))
        return _Rows(self.rows)


def _confirmation_constant(script_name: str) -> str:
    tree = ast.parse((PROJECT_ROOT / "scripts" / script_name).read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        is_confirmation = any(
            isinstance(target, ast.Name) and target.id == "CONFIRMATION"
            for target in node.targets
        )
        if is_confirmation:
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                return node.value.value
    raise AssertionError(f"{script_name} does not define a string CONFIRMATION")


@pytest.mark.parametrize(
    ("script_name", "expected"),
    [
        ("reset-admin.py", "RESET-DEEPAGENTS-CRM-ADMIN"),
        ("clear-crm-data.py", "CLEAR-DEEPAGENTS-CRM-DATA"),
    ],
)
def test_maintenance_script_confirmation_guards(
    script_name: str, expected: str
) -> None:
    assert _confirmation_constant(script_name) == expected


def test_clear_script_only_selects_existing_allow_list_tables() -> None:
    module = _load_script("clear-crm-data.py")
    existing_required = [
        {"table_name": table} for table in module.REQUIRED_CLEAR_TABLES
    ]
    conn = _ExistingTablesConnection(
        existing_required
        + [
            {"table_name": "documents"},
            {"table_name": "users"},
            {"table_name": "crm_app_migrations"},
        ]
    )

    actual = module._existing_clear_tables(conn)

    assert actual == module.REQUIRED_CLEAR_TABLES + ("documents",)
    assert len(conn.calls) == 1
    _, params = conn.calls[0]
    assert params == (list(module.CLEAR_TABLES),)
    assert "users" not in module.CLEAR_TABLES
    assert "crm_app_migrations" not in module.CLEAR_TABLES


def test_clear_script_tolerates_missing_optional_tables() -> None:
    module = _load_script("clear-crm-data.py")
    conn = _ExistingTablesConnection(
        [{"table_name": table} for table in module.REQUIRED_CLEAR_TABLES]
    )

    actual = module._existing_clear_tables(conn)

    assert actual == module.REQUIRED_CLEAR_TABLES
    assert not set(module.OPTIONAL_CLEAR_TABLES).intersection(actual)


def test_clear_script_rejects_missing_required_table() -> None:
    module = _load_script("clear-crm-data.py")
    existing = [
        {"table_name": table}
        for table in module.REQUIRED_CLEAR_TABLES
        if table != "activities"
    ]
    conn = _ExistingTablesConnection(existing)

    with pytest.raises(RuntimeError, match=r"缺少必须的 CRM 数据表.*activities"):
        module._existing_clear_tables(conn)
