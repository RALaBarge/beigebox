"""
ConfluenceCrawler — crawl Confluence pages via CDP (authenticated via mimic mode).

Uses Chrome DevTools Protocol to navigate Confluence, extract page content,
chunk, and embed into ChromaDB (document_search RAG).

Works by:
1. User activates CDP mimic mode (links browser cookies)
2. Crawler navigates Confluence via CDP
3. Extracts page text + metadata
4. Chunks and embeds into vector store

Input format (JSON string):
    {
        "action": "crawl",
        "url": "https://company.atlassian.net/wiki",
        "space": "SUP",
        "limit": 100
    }

Actions:
    crawl    — crawl Confluence space + embed into vector store
    preview  — show what pages would be crawled (no embedding)
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any
from urllib.parse import urljoin, urlparse

logger = logging.getLogger(__name__)


class ConfluenceCrawler:
    description = (
        "Crawl Confluence documentation via CDP (authenticated via browser cookies).\n"
        "Discovers pages in a space, extracts content, and embeds into vector store.\n"
        "\n"
        "Requires: CDP enabled + mimic mode activated (links your Confluence cookies)\n"
        "\n"
        "Input format: {\"action\": \"...\", \"url\": \"...\", \"space\": \"...\"}\n"
        "\n"
        "Actions:\n"
        '  crawl   — Crawl Confluence + embed: {"action": "crawl", "url": "https://company.atlassian.net/wiki", "space": "SUP", "limit": 100}\n'
        '  preview — Show pages without embedding:  {"action": "preview", "url": "...", "space": "SUP", "limit": 20}\n'
    )

    def __init__(self, cdp_tool=None, vector_store=None):
        """
        Initialize crawler with CDP tool + vector store.

        Args:
            cdp_tool: CDPTool instance (required for page navigation)
            vector_store: ChromaDB vector store for embedding (required for crawl action)
        """
        self._cdp = cdp_tool
        self._vector_store = vector_store

    def run(self, input_str: str) -> str:
        """Parse input JSON and dispatch to action."""
        try:
            params = json.loads(input_str.strip())
        except json.JSONDecodeError:
            return 'Error: input must be JSON {"action": "...", "url": "...", "space": "..."}'

        action = params.get("action", "").lower()
        url = params.get("url", "https://atlassian.atlassian.net/wiki").rstrip("/")
        space = params.get("space", "SUP")
        limit = params.get("limit", 100)

        if not self._cdp:
            return "Error: CDP tool not available. Enable CDP in config and activate mimic mode."

        try:
            if action == "crawl":
                return self._run_crawl(url, space, limit)
            elif action == "preview":
                return self._run_preview(url, space, limit)
            else:
                return f"Error: unknown action '{action}'. Use: crawl, preview"
        except Exception as exc:
            logger.error("ConfluenceCrawler error: %s", exc)
            return f"Error: {exc}"

    def _run_crawl(self, base_url: str, space_key: str, limit: int) -> str:
        """Crawl Confluence space and embed pages into vector store."""
        if not self._vector_store:
            return "Error: vector store not available. Enable document_search in config."

        logger.info("Crawling Confluence: %s (space=%s, limit=%d)", base_url, space_key, limit)

        pages = self._discover_pages(base_url, space_key, limit)
        if not pages:
            return f"No pages found in space {space_key}."

        logger.info("Discovered %d pages, embedding...", len(pages))

        embedded = 0
        failed = 0
        for page_url, title, content in pages:
            try:
                # Chunk large content
                chunks = self._chunk_text(content, chunk_size=800, overlap=80)
                for chunk_idx, chunk in enumerate(chunks):
                    if not chunk.strip():
                        continue

                    # Embed chunk
                    self._vector_store.add(
                        ids=[f"{space_key}_{title}_{chunk_idx}"],
                        documents=[chunk],
                        metadatas=[{
                            "source_file": f"{space_key}/{title}",
                            "page_url": page_url,
                            "chunk_index": chunk_idx,
                            "source_type": "document",
                            "space": space_key,
                        }],
                    )
                    embedded += 1
                logger.debug("Embedded %s", title)
            except Exception as e:
                logger.warning("Failed to embed %s: %s", title, e)
                failed += 1

        summary = (
            f"✓ Confluence crawl complete:\n"
            f"  Space: {space_key}\n"
            f"  Pages discovered: {len(pages)}\n"
            f"  Chunks embedded: {embedded}\n"
            f"  Failed: {failed}\n"
            f"  Now searchable via document_search tool"
        )
        logger.info(summary)
        return summary

    def _run_preview(self, base_url: str, space_key: str, limit: int) -> str:
        """Preview pages that would be crawled (no embedding)."""
        logger.info("Preview crawl: %s (space=%s, limit=%d)", base_url, space_key, limit)

        pages = self._discover_pages(base_url, space_key, limit)
        if not pages:
            return f"No pages found in space {space_key}."

        lines = [f"Preview: {len(pages)} pages in {space_key}:"]
        for page_url, title, content in pages[:limit]:
            char_count = len(content)
            lines.append(f"\n  • {title}")
            lines.append(f"    URL: {page_url}")
            lines.append(f"    Content: {char_count} chars, {char_count // 50} chunks (~800 chars each)")

        return "\n".join(lines)

    def _discover_pages(self, base_url: str, space_key: str, limit: int) -> list[tuple[str, str, str]]:
        """
        Discover pages in Confluence space via CDP.

        Returns: list of (page_url, title, content) tuples
        """
        pages = []

        # Construct space URL (Confluence Cloud standard)
        space_url = f"{base_url}/spaces/{space_key}/pages"
        logger.debug("Discovering pages: %s", space_url)

        try:
            # Navigate to space
            nav_result = self._cdp.run(json.dumps({
                "tool": "cdp.navigate",
                "input": space_url,
            }))
            if "Error" in nav_result:
                logger.error("Failed to navigate: %s", nav_result)
                return pages

            # Wait for page load
            time.sleep(2)

            # Extract page links via JavaScript
            # Confluence lists pages in the space overview
            links_result = self._cdp.run(json.dumps({
                "tool": "cdp.eval",
                "input": """
                Array.from(document.querySelectorAll('a[href*="/pages/"]'))
                  .filter(a => a.href.includes('/pages/') && !a.href.includes('?'))
                  .map(a => ({url: a.href, title: a.textContent.trim()}))
                  .filter(p => p.title && p.title.length > 0)
                  .slice(0, 100)
                """,
            }))

            # Parse links (eval returns stringified JSON)
            if "Error" not in links_result and "[" in links_result:
                try:
                    import ast
                    # Extract JSON from tool output
                    json_match = re.search(r"\[.*\]", links_result, re.DOTALL)
                    if json_match:
                        page_links = json.loads(json_match.group())
                        logger.info("Found %d page links", len(page_links))

                        for i, link_info in enumerate(page_links[:limit]):
                            page_url = link_info.get("url", "")
                            title = link_info.get("title", "")

                            if not page_url or not title:
                                continue

                            # Fetch page content
                            content = self._fetch_page(page_url)
                            if content:
                                pages.append((page_url, title, content))
                                logger.debug("Fetched: %s", title)

                except json.JSONDecodeError:
                    logger.warning("Could not parse page links JSON")
            else:
                logger.warning("No page links found on space page")

        except Exception as e:
            logger.error("Failed to discover pages: %s", e)

        return pages

    def _fetch_page(self, page_url: str) -> str:
        """Fetch page content via CDP."""
        try:
            # Navigate to page
            nav_result = self._cdp.run(json.dumps({
                "tool": "cdp.navigate",
                "input": page_url,
            }))
            if "Error" in nav_result:
                return ""

            time.sleep(1)  # Wait for content to load

            # Extract main content
            content_result = self._cdp.run(json.dumps({
                "tool": "cdp.eval",
                "input": """
                (function() {
                  // Confluence page content is typically in .ac-content or .wiki-content
                  let content = document.querySelector('.ac-content') ||
                                document.querySelector('.wiki-content') ||
                                document.querySelector('main') ||
                                document.body;
                  return content ? content.innerText : '';
                })()
                """,
            }))

            # Extract text from tool output
            if "Error" not in content_result:
                # Clean up tool wrapper and extract actual content
                if "Runtime.evaluate" in content_result or "result" in content_result:
                    # Try to extract the text value
                    match = re.search(r'"?value"?\s*:\s*"([^"]*)"', content_result)
                    if match:
                        return match.group(1)
                # Return as-is if it's clean text
                if len(content_result) > 100 and "Error" not in content_result:
                    return content_result

            return ""
        except Exception as e:
            logger.warning("Failed to fetch page %s: %s", page_url, e)
            return ""

    def _chunk_text(self, text: str, chunk_size: int = 800, overlap: int = 80) -> list[str]:
        """Split text into overlapping chunks."""
        if not text or len(text) < chunk_size:
            return [text] if text else []

        chunks = []
        start = 0
        while start < len(text):
            end = start + chunk_size
            chunk = text[start:end]
            chunks.append(chunk)
            start = end - overlap  # Overlap for context

        return chunks
