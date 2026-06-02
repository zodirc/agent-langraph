from app.services.knowledge_chunker import chunk_document


def test_chunk_document_splits_markdown_headings():
    content = "\n".join(
        [
            "# 长文写作规范",
            "前言段落。",
            "## 结构与节奏",
            "开篇要给出场景锚点。",
            "## TXT 排版",
            "使用全角标点，段落之间空一行。",
        ]
    )
    chunks = chunk_document("长文写作规范", content, domain="writing")
    assert len(chunks) >= 3
    combined = " ".join(f"{c.section_title} {c.body}" for c in chunks)
    assert "TXT 排版" in combined
    assert all("文档标题" in c.embed_text for c in chunks)
    assert all("章节" in c.embed_text or c.section_title == "" for c in chunks)


def test_chunk_document_respects_max_chars():
    section = "段落内容。" * 400
    content = f"## 超长章节\n{section}"
    chunks = chunk_document("测试", content, domain="common")
    assert len(chunks) >= 2
    assert all(len(c.body) <= 1300 for c in chunks)


def test_chunk_document_code_domain_prefers_symbol_boundaries():
    content = "\n".join(
        [
            "## API handlers",
            "def create_task(payload):",
            "    return payload",
            "",
            "def delete_task(task_id):",
            "    return task_id",
            "",
            "MAX_RETRIES = 3",
            "",
            "class TaskService:",
            "    pass",
        ]
    )
    chunks = chunk_document("代码规范", content, domain="code")
    assert len(chunks) >= 3
    joined = "\n".join(c.body for c in chunks)
    assert "def create_task" in joined
    assert "def delete_task" in joined
    assert "class TaskService" in joined
