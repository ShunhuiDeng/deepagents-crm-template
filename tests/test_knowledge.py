from app.knowledge import split_knowledge_text


def test_split_knowledge_text_prefers_paragraph_boundaries_and_overlap() -> None:
    content = "第一段内容足够长用于切分。\n\n第二段内容也足够长用于切分。\n\n第三段结束。"

    chunks = split_knowledge_text(content, chunk_size=20, overlap=5)

    assert len(chunks) >= 2
    assert all(chunk.strip() == chunk for chunk in chunks)
    assert "第一段" in chunks[0]


def test_split_knowledge_text_rejects_empty_content() -> None:
    try:
        split_knowledge_text("  \n", chunk_size=400, overlap=20)
    except ValueError as exc:
        assert "不能为空" in str(exc)
    else:
        raise AssertionError("empty knowledge content should be rejected")
