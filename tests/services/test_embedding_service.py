import sys
import types

from app.services import embedding_service
from app.services.embedding_service import embed_text, embed_texts


def test_local_embedding_deterministic():
    a = embed_text("hello world")
    b = embed_text("hello world")
    c = embed_text("other text")
    assert a == b
    assert a != c
    assert len(a) == 384


def test_embed_texts_batch():
    vectors = embed_texts(["a", "b"])
    assert len(vectors) == 2
    assert len(vectors[0]) == len(vectors[1])


def test_local_minilm_branch(monkeypatch):
    class _Vec:
        def __init__(self, values):
            self._values = values

        def astype(self, _dtype):
            return self

        def tolist(self):
            return list(self._values)

    class _DummyModel:
        def encode(self, texts, **_kwargs):
            return [_Vec([float(i + 1), 0.0, 0.0]) for i, _ in enumerate(texts)]

    class _DummySentenceTransformer:
        def __new__(cls, *args, **kwargs):
            return _DummyModel()

    dummy_module = types.SimpleNamespace(SentenceTransformer=_DummySentenceTransformer)
    monkeypatch.setitem(sys.modules, "sentence_transformers", dummy_module)
    monkeypatch.setattr("app.services.embedding_service._sentence_transformer_model", None)
    monkeypatch.setattr("app.services.embedding_service.settings.EMBEDDING_MODEL", "local_minilm")

    vectors = embedding_service.embed_texts(["x", "y"])
    assert vectors == [[1.0, 0.0, 0.0], [2.0, 0.0, 0.0]]
