from datetime import UTC, datetime
from uuid import uuid4

from app.schemas import ConversationMemoryOut
from app.skill_loader import (
    CONVERSATION_MEMORY_PATH,
    load_conversation_memory_file,
    load_skill_files,
)


def test_skill_loader_seeds_all_agent_skill_roots() -> None:
    files = load_skill_files()
    assert "/skills/supervisor/crm-orchestration/SKILL.md" in files
    assert "/skills/crud-agent/customer-crud/SKILL.md" in files
    assert "/skills/knowledge-agent/knowledge-retrieval/SKILL.md" in files
    assert "/skills/opportunity-analyst/opportunity-analysis/SKILL.md" in files
    assert all(path.startswith("/skills/") for path in files)


def test_conversation_memory_file_is_scoped_data() -> None:
    now = datetime.now(UTC)
    memory = ConversationMemoryOut(
        id=uuid4(),
        conversation_id=uuid4(),
        content="本会话下周需要交付方案",
        memory_type="decision",
        importance=4,
        created_at=now,
        updated_at=now,
    )
    files = load_conversation_memory_file([memory])
    rendered = files[CONVERSATION_MEMORY_PATH]["content"]
    assert "本会话下周需要交付方案" in rendered
    assert "Never infer that they apply to another conversation" in rendered
