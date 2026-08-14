from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

from langchain.messages import AIMessage

from app.agents.context import CRMContext
from app.permissions import CurrentUser, permissions_for_role


def extract_ai_text(message: AIMessage) -> str:
    if isinstance(message.content, str):
        return message.content.strip()
    parts: list[str] = []
    for block in message.content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") in {"text", "output_text"}:
            parts.append(str(block.get("text", "")))
    return "\n".join(part for part in parts if part).strip()


class AgentService:
    """Serialize turns per conversation and enforce a total Agent timeout."""

    def __init__(self, agent: Any, timeout_seconds: float = 120.0) -> None:
        self.agent = agent
        self.timeout_seconds = timeout_seconds
        self._locks: dict[tuple[UUID, UUID], asyncio.Lock] = {}

    def _lock_for(self, key: tuple[UUID, UUID]) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    def release_conversation(self, user_id: UUID, conversation_id: UUID) -> None:
        """Drop an idle lock when its conversation is permanently removed."""
        key = (user_id, conversation_id)
        lock = self._locks.get(key)
        if lock is not None and not lock.locked():
            self._locks.pop(key, None)

    async def invoke(
        self,
        *,
        current_user: CurrentUser,
        conversation_id: UUID,
        storage_thread_id: str,
        request_id: str,
        message: str,
        runtime_files: dict[str, Any],
    ) -> str:
        context = CRMContext(
            user_id=current_user.id,
            username=current_user.username,
            display_name=current_user.display_name,
            email=current_user.email,
            role=current_user.role.value,
            permissions=frozenset(
                permission.value for permission in permissions_for_role(current_user.role)
            ),
            conversation_id=conversation_id,
            request_id=request_id,
        )
        config = {"configurable": {"thread_id": storage_thread_id}}
        lock = self._lock_for((current_user.id, conversation_id))
        async with lock, asyncio.timeout(self.timeout_seconds):
            graph_output = await self.agent.ainvoke(
                {
                    "messages": [{"role": "user", "content": message}],
                    "files": runtime_files,
                },
                config=config,
                context=context,
                version="v2",
            )
        result: dict[str, Any] = graph_output.value
        for result_message in reversed(result.get("messages", [])):
            if isinstance(result_message, AIMessage):
                answer = extract_ai_text(result_message)
                if answer:
                    return answer
        return "本轮处理完成，但模型没有返回可显示的文本。"
