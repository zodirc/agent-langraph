# Knowledge content (RAG seed files)

Markdown files in this directory are upserted into the vector/knowledge store on startup (`ensure_builtin_knowledge`).

## Location

Default: repository root `knowledge/`. Override in `config/config.yaml`:

```yaml
knowledge:
  content_dir: knowledge   # relative to repo root
  # content_dir: /opt/agent-knowledge   # or absolute path
```

Or set environment variable `KNOWLEDGE_CONTENT_DIR`.

## Docker

- Production image: `COPY knowledge/ ./knowledge/`
- Dev compose: mount `./knowledge:/app/knowledge:ro`

## Domain Layout

Recommended structure:

- `knowledge/common/` → domain `common`
- `knowledge/code/` → domain `code`
- `knowledge/writing/` → domain `writing`

Config mapping (already supported):

```yaml
knowledge:
  domain_paths:
    common: knowledge/common
    code: knowledge/code
    writing: knowledge/writing
```

## Files

| File | doc_id |
|------|--------|
| `writing/writing_guidelines.md` | `builtin-writing-guidelines` |
| `writing/prose_voice_and_txt_format.md` | `builtin-prose-voice-format` |
| `code/code_editing_guidelines.md` | `builtin-code-editing-guidelines` |

Add new `.md` files in domain folders and register them in `app/services/knowledge_seed.py`.
