from app.agents.knowledge_agent.prompt import DESCRIPTION, SKILL_ROOTS, SYSTEM_PROMPT
from app.agents.knowledge_agent.tools import build_knowledge_tools
from app.agents.types import AgentDefinition, ExecutionMode
from app.knowledge import KnowledgeService


def build_knowledge_agent_definition(
    knowledge_service: KnowledgeService,
    *,
    enabled: bool = True,
    execution: ExecutionMode = "sync",
    async_url: str | None = None,
) -> AgentDefinition:
    return AgentDefinition(
        name="knowledge-agent",
        description=DESCRIPTION,
        system_prompt=SYSTEM_PROMPT,
        tools=build_knowledge_tools(knowledge_service),
        skill_roots=SKILL_ROOTS,
        execution=execution,
        graph_id="knowledge-agent" if execution == "async" else None,
        url=async_url,
        enabled=enabled,
    )
