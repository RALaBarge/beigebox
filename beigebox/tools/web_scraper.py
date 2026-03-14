"""
Web scraper: fetch a URL and extract clean text content.
Uses requests + BeautifulSoup. No API key needed.
"""

import ipaddress
import logging
import re
import urllib.parse

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0"
}


class WebScraperTool:
    """Fetch a URL and return clean text content."""

    description = 'Fetch and read a webpage. input = full URL. Example: {"tool": "web_scraper", "input": "https://example.com/page"}'

    capture_tool_io: bool = True
    max_context_chars: int = 6000

    def __init__(self, max_content_length: int = 10000):
        self.max_content_length = max_content_length
        logger.info("WebScraperTool initialized (max_chars=%d)", max_content_length)

    def _validate_url(self, url: str) -> str | None:
        """Return error string if URL is disallowed, else None."""
        try:
            parsed = urllib.parse.urlparse(url)
        except Exception:
            return "invalid URL"
        if parsed.scheme not in ("http", "https"):
            return f"scheme '{parsed.scheme}' not allowed (http/https only)"
        host = parsed.hostname or ""
        # Block localhost and loopback
        if host in ("localhost", "127.0.0.1", "::1") or host.endswith(".localhost"):
            return "private/localhost addresses not allowed"
        # Block private IP ranges
        try:
            addr = ipaddress.ip_address(host)
            if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved:
                return f"private IP address not allowed: {host}"
        except ValueError:
            pass  # hostname, not a raw IP — allow
        # Block cloud metadata endpoints
        if re.search(r"169\.254\.\d+\.\d+", host):
            return "link-local/metadata addresses not allowed"
        return None

    def run(self, url: str) -> str:
        """Fetch URL and extract text content."""
        err = self._validate_url(url)
        if err:
            logger.warning("WebScraperTool blocked URL %s: %s", url, err)
            return f"Blocked: {err}"
        try:
            resp = requests.get(url, headers=DEFAULT_HEADERS, timeout=15)
            resp.raise_for_status()

            soup = BeautifulSoup(resp.text, "lxml")

            # decompose() removes each tag and its subtree from the parse tree
            # entirely, so get_text() below never sees script/style content.
            for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
                tag.decompose()

            text = soup.get_text(separator="\n", strip=True)

            # Filter blank lines and strip leading/trailing whitespace per line.
            # This collapses the excessive blank lines that get_text() emits
            # between block elements into a clean single-newline-separated text.
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            text = "\n".join(lines)

            if len(text) > self.max_content_length:
                text = text[: self.max_content_length] + "\n\n[... truncated]"

            logger.debug("Scraped %d chars from %s", len(text), url)
            return text

        except Exception as e:
            logger.error("Scrape failed for %s: %s", url, e)
            return f"Failed to scrape {url}: {e}"
