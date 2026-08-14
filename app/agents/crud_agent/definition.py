from app.agents.crud_agent.prompt import DESCRIPTION, SKILL_ROOTS, SYSTEM_PROMPT
from app.agents.crud_agent.tools import build_crud_tools
from app.agents.types import AgentDefinition, ExecutionMode
from app.database import CRMRepository


def build_crud_agent_definition(
    repository: CRMRepository,
    *,
    enabled: bool = True,
    execution: ExecutionMode = "sync",
    async_url: str | None = None,
) -> AgentDefinition:
    return AgentDefinition(
        name="crud-agent",
        description=DESCRIPTION,
        system_prompt=SYSTEM_PROMPT,
        tools=build_crud_tools(repository),
        skill_roots=SKILL_ROOTS,
        execution=execution,
        graph_id="crud-agent" if execution == "async" else None,
        url=async_url,
        enabled=enabled,
    )
