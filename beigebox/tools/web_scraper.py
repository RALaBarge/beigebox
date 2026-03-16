"""
Web scraper: fetch a URL and extract clean text content.
Uses requests + BeautifulSoup. No API key needed.

On every successful fetch:
  1. Saves the raw HTML to <save_dir>/scraped/<domain>_<timestamp>.html
     for permanent, re-parseable storage.
  2. Embeds the extracted text into the vector store (if one is configured)
     so scraped content is searchable via /beigebox/search.
"""

import hashlib
import ipaddress
import logging
import os
import re
import urllib.parse
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0"
}

# Chunk size for RAG embedding — keeps each chunk within typical embedding limits.
_CHUNK_CHARS = 1500


class WebScraperTool:
    """Fetch a URL, return clean text, save raw HTML, and embed into RAG."""

    description = 'Fetch and read a webpage. input = full URL. Example: {"tool": "web_scraper", "input": "https://example.com/page"}'

    capture_tool_io: bool = True
    max_context_chars: int = 6000

    def __init__(
        self,
        max_content_length: int = 10000,
        save_dir: str | None = None,
        vector_store=None,
    ):
        self.max_content_length = max_content_length
        self._save_dir = save_dir
        self._vector_store = vector_store
        logger.info(
            "WebScraperTool initialized (max_chars=%d, save_dir=%s, rag=%s)",
            max_content_length,
            save_dir or "disabled",
            vector_store is not None,
        )

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

    def _save_html(self, url: str, html: str) -> str | None:
        """Write raw HTML to <save_dir>/scraped/ and return the file path."""
        if not self._save_dir:
            return None
        try:
            scraped_dir = os.path.join(self._save_dir, "scraped")
            os.makedirs(scraped_dir, exist_ok=True)
            parsed = urllib.parse.urlparse(url)
            # Build a safe filename from the domain + path, capped at 80 chars.
            slug = re.sub(r"[^\w\-]", "_", f"{parsed.netloc}{parsed.path}")[:80]
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            filename = f"{slug}_{ts}.html"
            filepath = os.path.join(scraped_dir, filename)
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(html)
            logger.debug("Saved raw HTML → %s", filepath)
            return filepath
        except Exception as e:
            logger.warning("Failed to save raw HTML for %s: %s", url, e)
            return None

    def _embed_text(self, url: str, text: str, filepath: str | None) -> None:
        """Chunk text and embed into the vector store for RAG search."""
        if not self._vector_store or not text.strip():
            return
        try:
            blob_hash = hashlib.sha256(text.encode()).hexdigest()
            source = filepath or url
            chunks = [
                text[i : i + _CHUNK_CHARS]
                for i in range(0, len(text), _CHUNK_CHARS)
            ]
            for idx, chunk in enumerate(chunks):
                self._vector_store.store_document_chunk(
                    source_file=source,
                    chunk_index=idx,
                    char_offset=idx * _CHUNK_CHARS,
                    blob_hash=blob_hash,
                    text=chunk,
                )
            logger.debug("Embedded %d chunk(s) from %s", len(chunks), url)
        except Exception as e:
            logger.warning("RAG embed failed for %s: %s", url, e)

    def run(self, url: str) -> str:
        """Fetch URL, extract text, save HTML, embed into RAG."""
        err = self._validate_url(url)
        if err:
            logger.warning("WebScraperTool blocked URL %s: %s", url, err)
            return f"Blocked: {err}"
        try:
            resp = requests.get(url, headers=DEFAULT_HEADERS, timeout=15)
            resp.raise_for_status()

            raw_html = resp.text
            soup = BeautifulSoup(raw_html, "lxml")

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

            # Save raw HTML (preserves original structure, evergreen format)
            filepath = self._save_html(url, raw_html)

            # Embed extracted text into vector store for RAG
            self._embed_text(url, text, filepath)

            if len(text) > self.max_content_length:
                text = text[: self.max_content_length] + "\n\n[... truncated]"

            logger.debug("Scraped %d chars from %s", len(text), url)
            return text

        except Exception as e:
            logger.error("Scrape failed for %s: %s", url, e)
            return f"Failed to scrape {url}: {e}"
