from app.rag.document_builder import DOCUMENT_SCHEMA_VERSION, _document
from app.rag.embedding import DeterministicEmbeddingClient, EmbeddingError, validate_embeddings


def test_deterministic_embedding_is_stable_and_bounded() -> None:
    provider = DeterministicEmbeddingClient(dimension=8)
    first = provider.embed(["护甲", "护甲"])
    assert first[0] == first[1]
    assert len(first[0]) == 8
    assert provider.calls == [("护甲", "护甲")]


def test_embedding_validation_rejects_wrong_count_or_dimension() -> None:
    try:
        validate_embeddings([[1.0]], expected_count=1, dimension=2)
    except EmbeddingError as exc:
        assert str(exc) == "embedding_dimension_invalid"
    else:
        raise AssertionError("invalid dimensions must be rejected")


def test_document_hash_is_stable_and_schema_versioned() -> None:
    class Snapshot:
        id = 7
        mode = "18"
        season = "S19"
        version = "18.18.2"
        revision = 1

    item = _document(Snapshot(), "hero", "11500", [("名称", " 奥恩 "), ("技能", "坚不可摧")], {})
    same = _document(Snapshot(), "hero", "11500", [("名称", "奥恩"), ("技能", "坚不可摧")], {})
    assert item.content == same.content
    assert item.content_hash == same.content_hash
    assert DOCUMENT_SCHEMA_VERSION in item.metadata["document_schema_version"]
    assert item.document_id == "hero:11500:hero_overview"
