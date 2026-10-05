import json

import httpx
import pytest

from app.rag.embedding import EmbeddingError, OpenAICompatibleEmbeddingClient


def client_for(handler):
    return OpenAICompatibleEmbeddingClient(
        model_name="BAAI/bge-m3",
        base_url="https://embed.example/v1/",
        api_key="secret-key",
        dimension=3,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_openai_compatible_embedding_sends_batch_and_orders_results() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/embeddings"
        assert request.headers["authorization"] == "Bearer secret-key"
        assert json.loads(request.content) == {"model": "BAAI/bge-m3", "input": ["a", "b"]}
        return httpx.Response(200, json={"data": [
            {"index": 1, "embedding": [0.4, 0.5, 0.6]},
            {"index": 0, "embedding": [0.1, 0.2, 0.3]},
        ]})

    assert client_for(handler).embed(["a", "b"]) == [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]


def test_embedding_rejects_invalid_indexes() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.1, 0.2, 0.3]}, {"index": 0, "embedding": [0.1, 0.2, 0.3]}]})

    with pytest.raises(EmbeddingError, match="embedding_response_invalid"):
        client_for(handler).embed(["a", "b"])


@pytest.mark.parametrize("status, code", [(401, "embedding_authentication_failed"), (429, "embedding_rate_limited"), (500, "embedding_provider_http_500")])
def test_embedding_maps_provider_errors(status: int, code: str) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text="secret-key should not leak")

    with pytest.raises(EmbeddingError, match=code) as error:
        client_for(handler).embed(["a"])
    assert "secret-key" not in str(error.value)


def test_embedding_rejects_wrong_dimension() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.1]}]})

    with pytest.raises(EmbeddingError, match="embedding_dimension_invalid"):
        client_for(handler).embed(["a"])
