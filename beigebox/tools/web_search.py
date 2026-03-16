"""
DuckDuckGo web search.
Free, no API key required. Default search provider.
"""

import logging
from ddgs import DDGS

logger = logging.getLogger(__name__)


class WebSearchTool:
    """DuckDuckGo web search via duckduckgo_search."""

    description = 'Search the web. input = plain search query string. Example: {"tool": "web_search", "input": "linux audio stacks ALSA PulseAudio 2024"}'

    # Class-level flags checked by operator._run_tool() via getattr to decide
    # whether to write the output to the blob store and how much to truncate
    # when injecting it back into the operator's context window.
    capture_tool_io: bool = True
    max_context_chars: int = 4000

    def __init__(self, max_results: int = 5):
        self.max_results = max_results
        logger.info("WebSearchTool initialized (max_results=%d)", max_results)

    def run(self, query: str) -> str:
        """Execute a web search and return results as text."""
        try:
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=self.max_results))
            if not results:
                return "No results found."
            lines = []
            for r in results:
                lines.append(f"[{r.get('title', '')}]({r.get('href', '')})\n{r.get('body', '')}")
            return "\n\n".join(lines)
        except Exception as e:
            logger.error("Search failed for '%s': %s", query, e)
            return f"Search failed: {e}"
