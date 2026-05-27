from app.services.embedding_meta import (
    EmbeddingMeta,
    get_current_embedding_meta,
    validate_index_compatibility,
)


def test_compatible_meta_passes():
    a = EmbeddingMeta("default", 384, "cosine", "v1", "")
    b = EmbeddingMeta("default", 384, "cosine", "v1", "")
    assert validate_index_compatibility(a, b) is True


def test_dimension_mismatch_fails():
    a = EmbeddingMeta("voyage-3", 1024, "cosine", "v1", "")
    b = EmbeddingMeta("default", 384, "cosine", "v1", "")
    assert validate_index_compatibility(a, b) is False


def test_current_meta_has_dimension(test_settings):
    meta = get_current_embedding_meta()
    assert meta.dimension > 0
