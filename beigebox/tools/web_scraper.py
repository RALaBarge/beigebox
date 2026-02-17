"""
Web scraper: fetch a URL and extract clean text content.
Uses requests + BeautifulSoup. No API key needed.
"""

import logging

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0"
}


class WebScraperTool:
    """Fetch a URL and return clean text content."""

    def __init__(self, max_content_length: int = 10000):
        self.max_content_length = max_content_length
        logger.info("WebScraperTool initialized (max_chars=%d)", max_content_length)

    def run(self, url: str) -> str:
        """Fetch URL and extract text content."""
        try:
            resp = requests.get(url, headers=DEFAULT_HEADERS, timeout=15)
            resp.raise_for_status()

            soup = BeautifulSoup(resp.text, "lxml")

            # Remove noise
            for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
                tag.decompose()

            text = soup.get_text(separator="\n", strip=True)

            # Collapse excessive whitespace
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            text = "\n".join(lines)

            if len(text) > self.max_content_length:
                text = text[: self.max_content_length] + "\n\n[... truncated]"

            logger.debug("Scraped %d chars from %s", len(text), url)
            return text

        except Exception as e:
            logger.error("Scrape failed for %s: %s", url, e)
            return f"Failed to scrape {url}: {e}"
