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

## Files

| File | doc_id |
|------|--------|
| `writing_guidelines.md` | `builtin-writing-guidelines` |
| `prose_voice_and_txt_format.md` | `builtin-prose-voice-format` |

Add new `.md` files here and register them in `app/services/knowledge_seed.py`.
