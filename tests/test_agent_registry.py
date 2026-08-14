from typing import Any, cast

import pytest

from app.agents.registry import build_agent_registry


def _repository() -> Any:
    return cast(Any, object())


def test_registry_builds_exactly_one_data_subagent() -> None:
    specs = build_agent_registry(_repository()).build_specs()

    assert [spec["name"] for spec in specs] == ["crud-agent"]
    assert specs[0]["skills"] == ["/skills/crud-agent/"]
    assert {agent_tool.name for agent_tool in specs[0]["tools"]} == {
        "select_leads",
        "insert_lead",
        "update_lead",
        "convert_lead",
        "select_accounts",
        "select_account_overview",
        "insert_account",
        "update_account",
        "select_contacts",
        "insert_contact",
        "update_contact",
        "select_opportunities",
        "insert_opportunity",
        "update_opportunity",
        "select_activities",
        "insert_activity",
        "update_activity",
    }


def test_registry_can_disable_data_subagent() -> None:
    registry = build_agent_registry(_repository(), set())
    assert registry.build_specs() == []
    assert registry.describe()[0]["enabled"] is False


def test_registry_builds_native_async_lifecycle_spec() -> None:
    specs = build_agent_registry(
        _repository(),
        execution="async",
        async_url="http://127.0.0.1:8123",
    ).build_specs()
    assert [spec["graph_id"] for spec in specs] == ["crud-agent"]
    assert specs[0]["url"] == "http://127.0.0.1:8123"


def test_registry_rejects_unknown_enabled_name() -> None:
    with pytest.raises(ValueError, match="未知名称"):
        build_agent_registry(_repository(), {"unknown-agent"})


def test_subagent_permissions_deny_memory_and_other_paths() -> None:
    spec = build_agent_registry(_repository()).build_specs()[0]
    rules = spec["permissions"]

    assert any(rule.mode == "deny" and "/memory/**" in rule.paths for rule in rules)
    assert any(rule.mode == "deny" and "/**" in rule.paths for rule in rules)
