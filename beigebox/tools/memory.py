"""
Memory tool — searches past conversations via ChromaDB.

This is the RAG retrieval tool. The decision LLM can invoke it when
it detects the user is referencing past conversations.

Examples the decision LLM would route here:
  "What did we discuss about Docker networking?"
  "Remember that recipe from last week?"
  "What was the solution to that Python error?"

Query preprocessing (optional):
  When query_preprocess=True, a fast LLM call extracts keywords and entities
  from the raw query before hitting ChromaDB. This improves recall for vague
  or conversational queries like "what did we decide last week?" where the raw
  text embeds poorly compared to "decision architecture database schema".
"""

import logging

import httpx

logger = logging.getLogger(__name__)

_PREPROCESS_SYSTEM = (
    "Extract 3–8 search keywords or short noun phrases from the user's query. "
    "Return only the keywords, comma-separated, no explanation, no punctuation."
)
_PREPROCESS_TIMEOUT = 8.0   # seconds — fast model, should be well within this


class MemoryTool:
    """Semantic search over stored conversation history."""

    def __init__(
        self,
        vector_store=None,
        max_results: int = 3,
        min_score: float = 0.3,
        query_preprocess: bool = False,
        query_preprocess_model: str = "",
        backend_url: str = "http://localhost:11434",
    ):
        """
        Args:
            vector_store:            VectorStore instance for querying ChromaDB.
            max_results:             Maximum number of results to return.
            min_score:               Minimum similarity score (0-1) to include.
            query_preprocess:        If True, run a fast LLM pass to extract keywords
                                     from the raw query before embedding.
            query_preprocess_model:  Model name to use for preprocessing (should be
                                     small/fast, e.g. "llama3.2:3b").
            backend_url:             Ollama base URL for the preprocessing call.
        """
        self.vector_store           = vector_store
        self.max_results            = max_results
        self.min_score              = min_score
        self.query_preprocess       = query_preprocess and bool(query_preprocess_model)
        self.query_preprocess_model = query_preprocess_model
        self.backend_url            = backend_url.rstrip("/")
        logger.info(
            "MemoryTool initialized (max_results=%d, min_score=%.1f, preprocess=%s model=%s)",
            max_results, min_score,
            self.query_preprocess, query_preprocess_model or "—",
        )

    # ------------------------------------------------------------------
    # Query preprocessing
    # ------------------------------------------------------------------

    def _preprocess(self, raw_query: str) -> str:
        """
        Run a fast LLM call to extract keywords from the raw query.
        Falls back to the original query on any failure.
        """
        try:
            resp = httpx.post(
                f"{self.backend_url}/v1/chat/completions",
                json={
                    "model":      self.query_preprocess_model,
                    "messages":   [
                        {"role": "system",  "content": _PREPROCESS_SYSTEM},
                        {"role": "user",    "content": raw_query},
                    ],
                    "stream":     False,
                    "max_tokens": 64,
                    "temperature": 0.0,
                },
                timeout=_PREPROCESS_TIMEOUT,
            )
            resp.raise_for_status()
            keywords = resp.json()["choices"][0]["message"]["content"].strip()
            if keywords:
                logger.debug("memory preprocess: %r → %r", raw_query, keywords)
                return keywords
        except Exception as e:
            logger.debug("memory preprocess failed, using raw query: %s", e)
        return raw_query

    # ------------------------------------------------------------------
    # Tool entry point
    # ------------------------------------------------------------------

    def run(self, query: str) -> str:
        """Search conversation history for relevant past messages."""
        if not self.vector_store:
            return "Memory search unavailable — vector store not initialized."

        search_query = self._preprocess(query) if self.query_preprocess else query

        try:
            results = self.vector_store.search(search_query, n_results=self.max_results)

            if not results:
                return f"No relevant past conversations found for: '{query}'"

            lines = [f"Found {len(results)} relevant past messages:"]
            included = 0

            for hit in results:
                score = 1 - hit["distance"]
                if score < self.min_score:
                    continue

                meta    = hit["metadata"]
                role    = meta.get("role", "?")
                model   = meta.get("model", "?")
                content = hit["content"]

                if len(content) > 300:
                    content = content[:300] + "..."

                lines.append(f"\n[{role.upper()}] (score: {score:.2f}, model: {model})")
                lines.append(content)
                included += 1

            if included == 0:
                return f"No sufficiently relevant past conversations found for: '{query}'"

            return "\n".join(lines)

        except Exception as e:
            logger.error("Memory search failed for '%s': %s", search_query, e)
            return f"Memory search failed: {e}"
