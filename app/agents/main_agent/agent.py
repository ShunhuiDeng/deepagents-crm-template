from typing import Any

from deepagents import (
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    create_deep_agent,
    register_harness_profile,
)
from langchain.chat_models import init_chat_model

from app.agents.context import CRMContext
from app.agents.main_agent.prompt import SKILL_ROOTS, SYSTEM_PROMPT
from app.agents.main_agent.tools import build_main_tools
from app.agents.registry import AgentRegistry
from app.agents.types import skill_permissions
from app.database import CRMRepository
from app.skill_loader import CONVERSATION_MEMORY_PATH


def build_main_agent(
    repository: CRMRepository,
    checkpointer: Any,
    model_name: str,
    model_api_key: str | None = None,
    *,
    registry: AgentRegistry,
) -> Any:
    """Compile the supervisor and pass native subagent definitions to Deep Agents."""
    provider = model_name.split(":", 1)[0] if ":" in model_name else model_name
    register_harness_profile(
        provider,
        HarnessProfile(
            general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False)
        ),
    )
    model = init_chat_model(model_name, api_key=model_api_key) if model_api_key else model_name
    return create_deep_agent(
        model=model,
        tools=list(build_main_tools(repository, registry)),
        system_prompt=SYSTEM_PROMPT,
        context_schema=CRMContext,
        checkpointer=checkpointer,
        skills=list(SKILL_ROOTS),
        memory=[CONVERSATION_MEMORY_PATH],
        permissions=skill_permissions(
            SKILL_ROOTS, memory_paths=(CONVERSATION_MEMORY_PATH,)
        ),
        subagents=registry.build_specs(),
        name="crm_assistant",
    )
