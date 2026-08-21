from typing import Any

from langchain.tools import ToolRuntime, tool
from langchain_core.tools import BaseTool

from app.agents.context import CRMContext
from app.knowledge import KnowledgeService


def build_knowledge_tools(knowledge_service: KnowledgeService) -> tuple[BaseTool, ...]:
    """Build the knowledge agent's single, read-only retrieval tool."""

    @tool
    async def search_knowledge_base(
        query: str,
        runtime: ToolRuntime[CRMContext],
        limit: int = 5,
    ) -> dict[str, Any]:
        """检索当前账号可见的知识库文档，并返回带来源的相关内容。"""
        try:
            results = await knowledge_service.search(
                runtime.context.current_user(), query, limit=min(max(limit, 1), 10)
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return {
            "ok": True,
            "count": len(results),
            "results": [item.model_dump(mode="json") for item in results],
        }

    return (search_knowledge_base,)
