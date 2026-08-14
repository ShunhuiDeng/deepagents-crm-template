from typing import Any

from langchain.tools import ToolRuntime, tool
from langchain_core.tools import BaseTool

from app.agents.context import CRMContext
from app.agents.registry import AgentRegistry
from app.database import CRMRepository
from app.schemas import ConversationMemoryCreate


def _dump(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def build_main_tools(
    repository: CRMRepository, registry: AgentRegistry
) -> tuple[BaseTool, ...]:
    """Build only the tools owned directly by the supervisor."""

    @tool
    def list_registered_agents() -> dict[str, Any]:
        """List specialist agents, execution modes, skills and enabled status."""
        return {"agents": registry.describe()}

    @tool
    async def remember_in_conversation(
        content: str,
        runtime: ToolRuntime[CRMContext],
        memory_type: str = "fact",
        importance: int = 3,
    ) -> dict[str, Any]:
        """Save durable context scoped only to the current conversation."""
        try:
            payload = ConversationMemoryCreate(
                content=content, memory_type=memory_type, importance=importance
            )
            memory = await repository.add_conversation_memory(
                runtime.context.user_id,
                runtime.context.conversation_id,
                payload,
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        if memory is None:
            return {"ok": False, "error": "当前会话不存在"}
        return {"ok": True, "memory": _dump(memory)}

    @tool
    async def recall_conversation_context(
        runtime: ToolRuntime[CRMContext],
        query: str | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        """Recall durable context from this conversation only; never crosses threads."""
        memories = await repository.recall_conversation_memories(
            runtime.context.user_id,
            runtime.context.conversation_id,
            query=query,
            limit=min(max(limit, 1), 50),
        )
        return {"count": len(memories), "memories": [_dump(item) for item in memories]}

    return (
        list_registered_agents,
        remember_in_conversation,
        recall_conversation_context,
    )
