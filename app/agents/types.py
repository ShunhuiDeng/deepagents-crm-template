from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from deepagents import AsyncSubAgent, FilesystemPermission
from langchain_core.tools import BaseTool

ExecutionMode = Literal["sync", "async"]


def skill_permissions(
    skill_roots: tuple[str, ...], *, memory_paths: tuple[str, ...] = ()
) -> list[FilesystemPermission]:
    """Fail closed: an agent may read only its explicitly declared skill roots."""
    allowed_paths = [f"{root.rstrip('/')}/**" for root in skill_roots]
    rules: list[FilesystemPermission] = []
    if allowed_paths:
        rules.append(
            FilesystemPermission(operations=["read"], paths=allowed_paths, mode="allow")
        )
    if memory_paths:
        rules.append(
            FilesystemPermission(
                operations=["read"], paths=list(memory_paths), mode="allow"
            )
        )
    rules.extend(
        [
            FilesystemPermission(operations=["read"], paths=["/skills/**"], mode="deny"),
            FilesystemPermission(operations=["read"], paths=["/memory/**"], mode="deny"),
            FilesystemPermission(
                operations=["read", "write", "edit"], paths=["/**"], mode="deny"
            ),
            FilesystemPermission(operations=["write"], paths=["/skills/**"], mode="deny"),
        ]
    )
    return rules


@dataclass(frozen=True)
class AgentDefinition:
    """One independently managed Deep Agents subagent definition."""

    name: str
    description: str
    system_prompt: str
    tools: tuple[BaseTool, ...] = ()
    skill_roots: tuple[str, ...] = ()
    execution: ExecutionMode = "sync"
    enabled: bool = True
    graph_id: str | None = None
    url: str | None = None

    def __post_init__(self) -> None:
        if self.execution == "async" and not self.graph_id:
            raise ValueError(f"异步子 Agent {self.name} 必须配置 graph_id")

    @property
    def tool_names(self) -> tuple[str, ...]:
        return tuple(agent_tool.name for agent_tool in self.tools)

    def to_deepagents_spec(self) -> Any:
        """Build the value passed directly to create_deep_agent(subagents=...)."""
        if self.execution == "async":
            async_spec: dict[str, Any] = {
                "name": self.name,
                "description": self.description,
                "graph_id": self.graph_id,
            }
            if self.url:
                async_spec["url"] = self.url
            return AsyncSubAgent(**async_spec)

        return {
            "name": self.name,
            "description": self.description,
            "system_prompt": self.system_prompt,
            "tools": list(self.tools),
            "skills": list(self.skill_roots),
            "permissions": skill_permissions(self.skill_roots),
        }
