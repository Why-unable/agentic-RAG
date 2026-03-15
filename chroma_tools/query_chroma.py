"""Query content from a local Chroma collection.
"""

from __future__ import annotations

from typing import Any

import chromadb


DEFAULT_COLLECTION_NAME = "pdf_documents"


def retrieve_by_question(
    question: str,
    collection_name: str = DEFAULT_COLLECTION_NAME,
    n_results: int = 5,
    host: str = "localhost",
    port: int = 8000,
) -> list[dict[str, Any]]:
    """Retrieve relevant chunks from Chroma by a user question.

    Args:
        question: User query text.
        collection_name: Chroma collection name.
        n_results: Number of retrieved chunks.
        host: Chroma server host.
        port: Chroma server port.

    Returns:
        A list of dictionaries with content, metadata, id and distance.
    """
    if not question or not question.strip():
        raise ValueError("question must be a non-empty string")

    client = chromadb.HttpClient(host=host, port=port)
    collection = client.get_or_create_collection(name=collection_name)

    result = collection.query(
        query_texts=[question.strip()],
        n_results=n_results,
        include=["documents", "metadatas", "distances"],
    )

    docs = result.get("documents", [[]])[0]
    metas = result.get("metadatas", [[]])[0]
    ids = result.get("ids", [[]])[0]
    distances = result.get("distances", [[]])[0]

    rows: list[dict[str, Any]] = []
    for index, content in enumerate(docs):
        row_id = ids[index] if index < len(ids) else None
        row_meta = metas[index] if index < len(metas) else None
        row_distance = distances[index] if index < len(distances) else None
        rows.append(
            {
                "rank": index + 1,
                "id": row_id,
                "distance": row_distance,
                "metadata": row_meta,
                "content": content,
            }
        )

    return rows
