"""Provider-neutral embedding contracts and deterministic test provider."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from typing import Protocol


class EmbeddingError(ValueError):
    """Raised when an embedding provider returns an invalid vector."""


class EmbeddingClient(Protocol):
    model_name: str
    dimension: int

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


def validate_embeddings(vectors: Sequence[Sequence[float]], *, expected_count: int, dimension: int) -> list[list[float]]:
    if len(vectors) != expected_count:
        raise EmbeddingError("embedding_count_mismatch")
    result = [list(vector) for vector in vectors]
    if any(len(vector) != dimension or any(not math.isfinite(value) for value in vector) for vector in result):
        raise EmbeddingError("embedding_dimension_invalid")
    return result


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
