"""
Memory tool — searches past conversations via ChromaDB.

This is the RAG retrieval tool. The decision LLM can invoke it when
it detects the user is referencing past conversations.

Examples the decision LLM would route here:
  "What did we discuss about Docker networking?"
  "Remember that recipe from last week?"
  "What was the solution to that Python error?"
"""

import logging

logger = logging.getLogger(__name__)


class MemoryTool:
    """Semantic search over stored conversation history."""

    def __init__(self, vector_store=None, max_results: int = 3, min_score: float = 0.3):
        """
        Args:
            vector_store: VectorStore instance for querying ChromaDB.
            max_results: Maximum number of results to return.
            min_score: Minimum similarity score (0-1) to include.
        """
        self.vector_store = vector_store
        self.max_results = max_results
        self.min_score = min_score
        logger.info("MemoryTool initialized (max_results=%d, min_score=%.1f)", max_results, min_score)

    def run(self, query: str) -> str:
        """Search conversation history for relevant past messages."""
        if not self.vector_store:
            return "Memory search unavailable — vector store not initialized."

        try:
            results = self.vector_store.search(query, n_results=self.max_results)

            if not results:
                return f"No relevant past conversations found for: '{query}'"

            lines = [f"Found {len(results)} relevant past messages:"]
            included = 0

            for hit in results:
                score = 1 - hit["distance"]
                if score < self.min_score:
                    continue

                meta = hit["metadata"]
                role = meta.get("role", "?")
                model = meta.get("model", "?")
                content = hit["content"]

                # Truncate long content
                if len(content) > 300:
                    content = content[:300] + "..."

                lines.append(f"\n[{role.upper()}] (score: {score:.2f}, model: {model})")
                lines.append(content)
                included += 1

            if included == 0:
                return f"No sufficiently relevant past conversations found for: '{query}'"

            return "\n".join(lines)

        except Exception as e:
            logger.error("Memory search failed for '%s': %s", query, e)
            return f"Memory search failed: {e}"
