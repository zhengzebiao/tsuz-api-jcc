"""Provider-neutral embedding contracts and deterministic test provider."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from typing import Protocol

import httpx


class EmbeddingError(ValueError):
    """Raised when an embedding provider returns an invalid vector."""


class EmbeddingClient(Protocol):
    model_name: str
    dimension: int

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


def validate_embeddings(vectors: Sequence[Sequence[float]], *, expected_count: int, dimension: int) -> list[list[float]]:
    if len(vectors) != expected_count:
        raise EmbeddingError("embedding_count_mismatch")
    try:
        result = [[float(value) for value in vector] for vector in vectors]
    except (TypeError, ValueError) as exc:
        raise EmbeddingError("embedding_values_invalid") from exc
    if any(len(vector) != dimension or any(not math.isfinite(value) for value in vector) for vector in result):
        raise EmbeddingError("embedding_dimension_invalid")
    return result


class OpenAICompatibleEmbeddingClient:
    """Synchronous client for OpenAI-compatible embedding endpoints."""

    def __init__(
        self,
        *,
        model_name: str,
        base_url: str,
        api_key: str,
        dimension: int,
        timeout_seconds: float = 120.0,
        client: httpx.Client | None = None,
    ) -> None:
        if not model_name or not base_url or not api_key or dimension < 1 or timeout_seconds <= 0:
            raise EmbeddingError("embedding_configuration_invalid")
        self.model_name = model_name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.dimension = dimension
        self.timeout_seconds = timeout_seconds
        self._client = client
        self._owns_client = client is None

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        inputs = list(texts)
        if not inputs:
            return []
        client = self._client or httpx.Client(timeout=self.timeout_seconds)
        try:
            response = client.post(
                f"{self.base_url}/embeddings",
                json={"model": self.model_name, "input": inputs},
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
            if response.status_code in (401, 403):
                raise EmbeddingError("embedding_authentication_failed")
            if response.status_code == 429:
                raise EmbeddingError("embedding_rate_limited")
            if response.status_code >= 400:
                raise EmbeddingError(f"embedding_provider_http_{response.status_code}")
            try:
                payload = response.json()
                rows = payload["data"]
                if not isinstance(rows, list):
                    raise TypeError
                indexed = []
                for row in rows:
                    if not isinstance(row, dict) or not isinstance(row.get("index"), int) or not isinstance(row.get("embedding"), list):
                        raise TypeError
                    indexed.append((row["index"], row["embedding"]))
                indexes = [index for index, _ in indexed]
                if sorted(indexes) != list(range(len(inputs))):
                    raise ValueError
                vectors = [vector for _, vector in sorted(indexed)]
            except (KeyError, TypeError, ValueError, IndexError) as exc:
                raise EmbeddingError("embedding_response_invalid") from exc
            return validate_embeddings(vectors, expected_count=len(inputs), dimension=self.dimension)
        except httpx.TimeoutException as exc:
            raise EmbeddingError("embedding_provider_timeout") from exc
        except httpx.HTTPError as exc:
            raise EmbeddingError("embedding_provider_unavailable") from exc
        finally:
            if self._owns_client:
                client.close()


class DeterministicEmbeddingClient:
    """Offline provider for tests; never use as a production semantic model."""

    model_name = "fake-test-1024"
    dimension = 1024

    def __init__(self, dimension: int = 1024) -> None:
        self.dimension = dimension
        self.calls: list[tuple[str, ...]] = []

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(tuple(texts))
        vectors: list[list[float]] = []
        for text in texts:
            vector: list[float] = []
            counter = 0
            while len(vector) < self.dimension:
                digest = hashlib.sha256(f"{text}:{counter}".encode()).digest()
                vector.extend((byte / 127.5) - 1.0 for byte in digest)
                counter += 1
            norm = math.sqrt(sum(item * item for item in vector[: self.dimension])) or 1.0
            vectors.append([item / norm for item in vector[: self.dimension]])
        return vectors
