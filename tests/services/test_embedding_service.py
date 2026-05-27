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
