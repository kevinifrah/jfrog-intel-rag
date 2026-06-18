from __future__ import annotations

from functools import lru_cache
from typing import Any

from google import genai
from google.genai import types

from ci_engine.config import get as config_get

_VERTEX_LOCATION = "europe-west1"


def _required_config(path: str) -> Any:
    value = config_get(path)
    if value is None:
        raise RuntimeError(f"missing required config value: {path}")
    return value


def _embedding_model() -> str:
    return str(_required_config("embedding.model"))


def _embedding_dimensions() -> int:
    return int(_required_config("embedding.dimensions"))


def _insert_batch_size() -> int:
    return max(int(_required_config("ingestion.insert_batch_size")), 1)


@lru_cache(maxsize=1)
def _client() -> genai.Client:
    return genai.Client(
        vertexai=True,
        project=str(_required_config("project.gcp_project_id")),
        location=_VERTEX_LOCATION,
    )


def _embed_content(texts: list[str], task_type: str) -> list[list[float]]:
    if not texts:
        return []

    response = _client().models.embed_content(
        model=_embedding_model(),
        contents=texts,
        config=types.EmbedContentConfig(
            task_type=task_type,
            output_dimensionality=_embedding_dimensions(),
        ),
    )
    embeddings = response.embeddings
    if embeddings is None:
        raise RuntimeError("Vertex returned no embeddings")
    if len(embeddings) != len(texts):
        raise RuntimeError(
            f"expected {len(texts)} embeddings, got {len(embeddings)}"
        )

    dimensions = _embedding_dimensions()
    vectors: list[list[float]] = []
    for index, embedding in enumerate(embeddings):
        values = embedding.values
        if values is None:
            raise RuntimeError(f"Vertex returned no values for embedding {index}")

        vector = list(values)
        if len(vector) != dimensions:
            raise RuntimeError(
                f"expected {dimensions} embedding dimensions, got {len(vector)}"
            )
        vectors.append(vector)

    return vectors


def embed_documents(texts: list[str]) -> list[list[float]]:
    vectors: list[list[float]] = []
    batch_size = _insert_batch_size()
    task_type = str(_required_config("embedding.doc_task_type"))

    for start in range(0, len(texts), batch_size):
        vectors.extend(_embed_content(texts[start : start + batch_size], task_type))

    return vectors


def embed_query(text: str) -> list[float]:
    task_type = str(_required_config("embedding.query_task_type"))
    return _embed_content([text], task_type)[0]


__all__ = ["embed_documents", "embed_query"]
