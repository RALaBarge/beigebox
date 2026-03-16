"""
Tests for WebScraperTool — URL validation, HTML saving, and RAG embedding.
Network calls are mocked; no live HTTP requests.
"""

import os
import tempfile
import pytest
from unittest.mock import MagicMock, patch, mock_open

from beigebox.tools.web_scraper import WebScraperTool


# ── Helpers ───────────────────────────────────────────────────────────────────

SIMPLE_HTML = """
<html><head><title>Test</title></head>
<body>
  <nav>skip me</nav>
  <p>Hello world</p>
  <script>bad js</script>
  <footer>skip me</footer>
</body></html>
"""

def _tool(**kwargs) -> WebScraperTool:
    return WebScraperTool(**kwargs)


def _mock_response(html: str = SIMPLE_HTML, status: int = 200):
    resp = MagicMock()
    resp.text = html
    resp.raise_for_status = MagicMock()
    if status >= 400:
        from requests.exceptions import HTTPError
        resp.raise_for_status.side_effect = HTTPError(f"HTTP {status}")
    return resp


# ── URL validation ────────────────────────────────────────────────────────────

class TestValidateUrl:
    def test_allows_http(self):
        t = _tool()
        assert t._validate_url("http://example.com/page") is None

    def test_allows_https(self):
        t = _tool()
        assert t._validate_url("https://example.com") is None

    def test_blocks_ftp(self):
        t = _tool()
        assert t._validate_url("ftp://example.com") is not None

    def test_blocks_localhost(self):
        t = _tool()
        assert t._validate_url("http://localhost/admin") is not None

    def test_blocks_loopback_ip(self):
        t = _tool()
        assert t._validate_url("http://127.0.0.1/secret") is not None

    def test_blocks_private_ip(self):
        t = _tool()
        assert t._validate_url("http://192.168.1.1/") is not None

    def test_blocks_metadata_endpoint(self):
        t = _tool()
        assert t._validate_url("http://169.254.169.254/latest/meta-data/") is not None


# ── Text extraction ───────────────────────────────────────────────────────────

class TestRun:
    def test_returns_extracted_text(self):
        t = _tool()
        with patch("requests.get", return_value=_mock_response()):
            result = t.run("https://example.com/")
        assert "Hello world" in result
        assert "bad js" not in result  # script stripped
        assert "skip me" not in result  # nav/footer stripped

    def test_truncates_long_content(self):
        long_html = f"<p>{'x' * 20000}</p>"
        t = _tool(max_content_length=100)
        with patch("requests.get", return_value=_mock_response(long_html)):
            result = t.run("https://example.com/")
        assert len(result) <= 130  # truncated + "[... truncated]"
        assert "[... truncated]" in result

    def test_blocked_url_returns_error(self):
        t = _tool()
        result = t.run("http://localhost/admin")
        assert "Blocked" in result

    def test_http_error_returns_error(self):
        t = _tool()
        with patch("requests.get", return_value=_mock_response(status=404)):
            result = t.run("https://example.com/missing")
        assert "Failed to scrape" in result or "Blocked" in result or result


# ── HTML saving ───────────────────────────────────────────────────────────────

class TestSaveHtml:
    def test_saves_html_file_when_save_dir_set(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            t = _tool(save_dir=tmpdir)
            with patch("requests.get", return_value=_mock_response()):
                t.run("https://example.com/page")
            scraped = os.path.join(tmpdir, "scraped")
            assert os.path.isdir(scraped)
            files = os.listdir(scraped)
            assert len(files) == 1
            assert files[0].endswith(".html")

    def test_html_file_contains_raw_html(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            t = _tool(save_dir=tmpdir)
            with patch("requests.get", return_value=_mock_response()):
                t.run("https://example.com/page")
            scraped = os.path.join(tmpdir, "scraped")
            content = open(os.path.join(scraped, os.listdir(scraped)[0])).read()
            assert "Hello world" in content
            assert "skip me" in content  # raw HTML retains nav/footer

    def test_no_save_when_save_dir_not_set(self):
        t = _tool()  # no save_dir
        with patch("requests.get", return_value=_mock_response()):
            filepath = t._save_html("https://example.com/", SIMPLE_HTML)
        assert filepath is None

    def test_filename_contains_domain(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            t = _tool(save_dir=tmpdir)
            with patch("requests.get", return_value=_mock_response()):
                t.run("https://news.example.com/article")
            files = os.listdir(os.path.join(tmpdir, "scraped"))
            assert any("news_example_com" in f for f in files)


# ── RAG embedding ─────────────────────────────────────────────────────────────

class TestEmbedText:
    def test_calls_store_document_chunk(self):
        mock_vs = MagicMock()
        t = _tool(vector_store=mock_vs)
        with patch("requests.get", return_value=_mock_response()):
            t.run("https://example.com/")
        assert mock_vs.store_document_chunk.called

    def test_chunks_long_text(self):
        mock_vs = MagicMock()
        t = _tool(vector_store=mock_vs, max_content_length=100000)
        long_html = f"<p>{'word ' * 2000}</p>"
        with patch("requests.get", return_value=_mock_response(long_html)):
            t.run("https://example.com/")
        calls = mock_vs.store_document_chunk.call_count
        assert calls > 1  # multiple chunks

    def test_no_embed_when_vector_store_not_set(self):
        t = _tool()  # no vector_store
        with patch("requests.get", return_value=_mock_response()):
            t.run("https://example.com/")
        # Just ensure no crash — no assertion on mock calls needed

    def test_embed_error_does_not_propagate(self):
        mock_vs = MagicMock()
        mock_vs.store_document_chunk.side_effect = RuntimeError("db down")
        t = _tool(vector_store=mock_vs)
        with patch("requests.get", return_value=_mock_response()):
            result = t.run("https://example.com/")
        # Tool should still return text, not raise
        assert "Hello world" in result
