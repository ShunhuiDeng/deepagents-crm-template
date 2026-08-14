from app.agents.opportunity_analyst.prompt import (
    DESCRIPTION,
    SKILL_ROOTS,
    SYSTEM_PROMPT,
)
from app.agents.opportunity_analyst.tools import build_opportunity_tools
from app.agents.types import AgentDefinition, ExecutionMode
from app.database import CRMRepository


def build_opportunity_analyst_definition(
    repository: CRMRepository,
    *,
    enabled: bool = True,
    execution: ExecutionMode = "sync",
    async_url: str | None = None,
) -> AgentDefinition:
    return AgentDefinition(
        name="opportunity-analyst",
        description=DESCRIPTION,
        system_prompt=SYSTEM_PROMPT,
        tools=build_opportunity_tools(repository),
        skill_roots=SKILL_ROOTS,
        execution=execution,
        graph_id="opportunity-analyst" if execution == "async" else None,
        url=async_url,
        enabled=enabled,
    )
