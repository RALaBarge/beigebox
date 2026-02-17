"""
Google Custom Search tool.
Currently returns mock results for pipeline testing.
Swap in real API key + CSE ID in config.yaml when ready.
"""

import logging

logger = logging.getLogger(__name__)


MOCK_RESULTS = [
    {
        "title": "Mock Result 1 - Placeholder",
        "link": "https://example.com/result1",
        "snippet": "This is a mock search result. Configure google_search.api_key in config.yaml to enable real results.",
    },
    {
        "title": "Mock Result 2 - Placeholder",
        "link": "https://example.com/result2",
        "snippet": "Second mock result. The rest of the pipeline (parsing, injection) works identically with real data.",
    },
]


class GoogleSearchTool:
    """Google Custom Search. Mock mode when no API key is configured."""

    def __init__(self, api_key: str = "", cse_id: str = "", max_results: int = 5):
        self.api_key = api_key
        self.cse_id = cse_id
        self.max_results = max_results
        self.mock_mode = not (api_key and cse_id)

        if self.mock_mode:
            logger.info("GoogleSearchTool initialized in MOCK mode (no API key)")
        else:
            logger.info("GoogleSearchTool initialized with real API key")

    def run(self, query: str) -> str:
        """Execute search. Returns mock data if no API key configured."""
        if self.mock_mode:
            logger.debug("Mock search for '%s'", query)
            lines = []
            for r in MOCK_RESULTS[: self.max_results]:
                lines.append(f"[{r['title']}]({r['link']})\n{r['snippet']}")
            return "\n\n".join(lines)

        # Real implementation using Google Custom Search JSON API
        try:
            import requests

            resp = requests.get(
                "https://www.googleapis.com/customsearch/v1",
                params={
                    "key": self.api_key,
                    "cx": self.cse_id,
                    "q": query,
                    "num": min(self.max_results, 10),
                },
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()

            lines = []
            for item in data.get("items", []):
                lines.append(f"[{item['title']}]({item['link']})\n{item.get('snippet', '')}")
            return "\n\n".join(lines) or "No results found."

        except Exception as e:
            logger.error("Google search failed for '%s': %s", query, e)
            return f"Google search failed: {e}"
