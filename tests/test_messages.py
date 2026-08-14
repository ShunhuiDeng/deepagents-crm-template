from langchain.messages import AIMessage, HumanMessage, ToolMessage

from app.main import checkpoint_thread_id, extract_message_text, serialize_conversation_messages


def test_extract_string_message() -> None:
    assert extract_message_text(AIMessage(content="保存成功")) == "保存成功"


def test_extract_block_message() -> None:
    message = AIMessage(
        content=[
            {"type": "text", "text": "第一段"},
            {"type": "text", "text": "第二段"},
        ]
    )
    assert extract_message_text(message) == "第一段\n第二段"


def test_checkpoint_thread_is_scoped_to_actor() -> None:
    assert checkpoint_thread_id("user-a", "same-thread") != checkpoint_thread_id(
        "user-b", "same-thread"
    )
    assert checkpoint_thread_id("user-a", "same-thread") == checkpoint_thread_id(
        "user-a", "same-thread"
    )


def test_serialize_conversation_messages_hides_internal_messages() -> None:
    messages = [
        HumanMessage(content="你好", id="u1"),
        AIMessage(content="", tool_calls=[{"name": "task", "args": {}, "id": "t1"}]),
        ToolMessage(content="internal", tool_call_id="t1"),
        AIMessage(content="你好，有什么可以帮你？", id="a1"),
        HumanMessage(
            content="old summary",
            id="s1",
            additional_kwargs={"lc_source": "summarization"},
        ),
    ]
    assert [item.model_dump() for item in serialize_conversation_messages(messages)] == [
        {"id": "u1", "role": "user", "content": "你好"},
        {"id": "a1", "role": "assistant", "content": "你好，有什么可以帮你？"},
    ]
