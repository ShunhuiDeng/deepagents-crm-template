from __future__ import annotations

from typing import Any

from app.agents.crud_agent.definition import build_crud_agent_definition
from app.agents.knowledge_agent.definition import build_knowledge_agent_definition
from app.agents.types import AgentDefinition, ExecutionMode
from app.database import CRMRepository
from app.knowledge import KnowledgeService


class AgentRegistry:
    """Register complete subagent definitions without owning prompts or tools."""

    def __init__(self, definitions: tuple[AgentDefinition, ...]) -> None:
        names = [definition.name for definition in definitions]
        if len(names) != len(set(names)):
            raise ValueError("子 Agent 名称必须唯一")
        self._definitions = definitions

    @property
    def definitions(self) -> tuple[AgentDefinition, ...]:
        return self._definitions

    def build_specs(self) -> list[Any]:
        return [
            definition.to_deepagents_spec()
            for definition in self._definitions
            if definition.enabled
        ]

    def describe(self) -> list[dict[str, Any]]:
        return [
            {
                "name": definition.name,
                "description": definition.description,
                "execution": definition.execution,
                "enabled": definition.enabled,
                "skills": list(definition.skill_roots),
                "tools": list(definition.tool_names),
                "graph_id": definition.graph_id,
                "url": definition.url,
            }
            for definition in self._definitions
        ]


def build_agent_registry(
    repository: CRMRepository,
    enabled_names: set[str] | None = None,
    *,
    knowledge_service: KnowledgeService | None = None,
    execution: ExecutionMode = "sync",
    async_url: str | None = None,
) -> AgentRegistry:
    """Compose explicitly enabled specialist agents passed to Deep Agents."""
    known_names = {"crud-agent", "knowledge-agent"}
    enabled_names = (
        {"crud-agent", "knowledge-agent"}
        if enabled_names is None and knowledge_service is not None
        else {"crud-agent"}
        if enabled_names is None
        else enabled_names
    )
    unknown = enabled_names - known_names
    if unknown:
        raise ValueError(f"ENABLED_SUBAGENTS 包含未知名称: {sorted(unknown)}")
    if "knowledge-agent" in enabled_names and knowledge_service is None:
        raise ValueError("启用 knowledge-agent 时必须配置知识库 RAG 服务")
    definitions: list[AgentDefinition] = [
        build_crud_agent_definition(
            repository,
            enabled="crud-agent" in enabled_names,
            execution=execution,
            async_url=async_url,
        )
    ]
    if knowledge_service is not None:
        definitions.append(
            build_knowledge_agent_definition(
                knowledge_service,
                enabled="knowledge-agent" in enabled_names,
                execution=execution,
                async_url=async_url,
            )
        )
    return AgentRegistry(
        tuple(definitions)
    )
