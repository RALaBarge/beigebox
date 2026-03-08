"""
DocumentSearchTool — semantic search over indexed workspace documents.

Documents are chunked and embedded at upload time via the workspace upload
endpoint.  This tool retrieves relevant chunks by cosine similarity and
returns them with source filename and chunk position.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class DocumentSearchTool:
    description = (
        "Search indexed documents in workspace/in/ by semantic similarity. "
        "Input: a natural language query or keywords describing what you are looking for. "
        "Returns relevant excerpts from uploaded documents with source filenames. "
        "Use when the user asks about documents they have uploaded or references a file by name."
    )

    def __init__(self, vector_store=None, max_results: int = 5, min_score: float = 0.3):
        self.vector_store = vector_store
        self.max_results = max_results
        self.min_score = min_score

    def run(self, query: str) -> str:
        if not self.vector_store:
            return "Document search unavailable — vector store not initialized."

        try:
            results = self.vector_store.search(
                query,
                n_results=self.max_results,
                where={"source_type": "document"},
            )
        except Exception as e:
            logger.error("DocumentSearchTool search failed: %s", e)
            return f"Document search failed: {e}"

        if not results:
            return f"No relevant documents found for: '{query}'"

        lines = []
        included = 0
        for hit in results:
            score = max(0.0, round(1.0 - hit["distance"], 4))
            if score < self.min_score:
                continue
            meta = hit["metadata"]
            source = meta.get("source_file", "unknown")
            chunk_idx = meta.get("chunk_index", "?")
            content = hit["content"]
            if len(content) > 400:
                content = content[:400] + "..."
            lines.append(f"\n[{source} chunk {chunk_idx}] (score: {score:.2f})")
            lines.append(content)
            included += 1

        if included == 0:
            return f"No sufficiently relevant documents found for: '{query}'"

        return f"Found {included} relevant document chunk(s):" + "\n".join(lines)
