"""
DuckDuckGo web search via LangChain.
Free, no API key required. Default search provider.
"""

import logging
from langchain_community.tools import DuckDuckGoSearchResults

logger = logging.getLogger(__name__)


class WebSearchTool:
    """Wraps LangChain's DuckDuckGo search."""

    def __init__(self, max_results: int = 5):
        self.max_results = max_results
        self.search = DuckDuckGoSearchResults(max_results=max_results)
        logger.info("WebSearchTool initialized (max_results=%d)", max_results)

    def run(self, query: str) -> str:
        """Execute a web search and return results as text."""
        try:
            results = self.search.invoke(query)
            logger.debug("Search for '%s' returned results", query)
            return results
        except Exception as e:
            logger.error("Search failed for '%s': %s", query, e)
            return f"Search failed: {e}"
