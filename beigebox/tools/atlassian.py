"""
AtlassianTool — REST access to Jira and Confluence on Atlassian Cloud.

One Atlassian Cloud API token authenticates against both:
  - Jira REST v3      → /rest/api/3/*
  - Confluence v1+v2  → /wiki/rest/api/* and /wiki/api/v2/*

Auth is HTTP Basic with the user's email + the API token.

Credentials come from env vars (set via ~/.beigebox/.env, mode 600):
    ATLASSIAN_BASE_URL=https://yourorg.atlassian.net
    ATLASSIAN_EMAIL=you@company.com
    ATLASSIAN_API_TOKEN=<token>

Stdlib only — no extra dependencies. Uses urllib + json + base64.

Tool input format (JSON string):
    {"action": "jira_search",     "jql": "assignee = currentUser()", "limit": 10}
    {"action": "jira_get",        "key": "SUP-1234"}
    {"action": "confluence_search","cql": "text ~ \\"preserve usage\\" AND space = SUP", "limit": 10}
    {"action": "confluence_get",  "id": "1234567"}
    {"action": "confluence_get",  "title": "Page Title", "space": "SUP"}

All actions return a compact, LLM-friendly string. Large responses are
truncated to keep the model's context manageable.
"""

from __future__ import annotations

import base64
import html
import json
import logging
import os
import re
import urllib.parse
import urllib.request
from typing import Any
from urllib.error import HTTPError, URLError

logger = logging.getLogger(__name__)

# Limits — keep payloads LLM-friendly
MAX_RESULTS = 25
TIMEOUT_S = 15.0
TRUNCATE_FIELD_CHARS = 500
TRUNCATE_PAGE_CHARS = 4000


class AtlassianTool:
    """Live REST access to Jira and Confluence on Atlassian Cloud."""

    description = (
        "Live access to Jira tickets and Confluence pages on Atlassian Cloud "
        "via REST. Use for fresh data when document_search has nothing or is stale.\n"
        "\n"
        "Input: JSON string with an `action` field. Available actions:\n"
        '  jira_search       — JQL query: {"action":"jira_search","jql":"project=SUP AND text ~ \\"preserve\\"","limit":10}\n'
        '  jira_get          — Single issue + comments: {"action":"jira_get","key":"SUP-1234"}\n'
        '  confluence_search — CQL query: {"action":"confluence_search","cql":"text ~ \\"preserve usage\\" AND space=SUP","limit":10}\n'
        '  confluence_get    — By id: {"action":"confluence_get","id":"1234567"}  OR  by title: {"action":"confluence_get","title":"Page Name","space":"SUP"}\n'
        "\n"
        "Returns plain-text summaries with keys, titles, statuses, URLs, and excerpts. "
        "Tables and code blocks in Confluence pages are preserved (REST returns clean storage format)."
    )

    def __init__(
        self,
        base_url: str | None = None,
        email: str | None = None,
        api_token: str | None = None,
    ):
        self.base_url = (base_url or os.environ.get("ATLASSIAN_BASE_URL", "")).rstrip("/")
        self.email = email or os.environ.get("ATLASSIAN_EMAIL", "")
        self.token = api_token or os.environ.get("ATLASSIAN_API_TOKEN", "")
        if not (self.base_url and self.email and self.token):
            logger.warning(
                "AtlassianTool: missing credentials "
                "(base_url=%s email=%s token=%s) — tool will return errors until configured",
                bool(self.base_url), bool(self.email), bool(self.token),
            )
        else:
            logger.info("AtlassianTool initialized for %s as %s", self.base_url, self.email)

    # ------------------------------------------------------------------
    # HTTP plumbing
    # ------------------------------------------------------------------

    def _auth_header(self) -> str:
        raw = f"{self.email}:{self.token}".encode("utf-8")
        return "Basic " + base64.b64encode(raw).decode("ascii")

    def _request(
        self,
        path: str,
        method: str = "GET",
        body: dict | None = None,
        params: dict | None = None,
    ) -> tuple[int, dict | str]:
        """
        Make an authenticated REST call. Returns (status, parsed_json_or_text).
        Never raises — errors are returned as (status, error_string).
        """
        if not (self.base_url and self.email and self.token):
            return (0, "Atlassian credentials not configured (set ATLASSIAN_BASE_URL, ATLASSIAN_EMAIL, ATLASSIAN_API_TOKEN)")

        url = f"{self.base_url}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)

        data: bytes | None = None
        headers = {
            "Accept": "application/json",
            "Authorization": self._auth_header(),
            "User-Agent": "beigebox-atlassian-tool/1.0",
        }
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                try:
                    return (resp.status, json.loads(raw))
                except json.JSONDecodeError:
                    return (resp.status, raw)
        except HTTPError as e:
            try:
                err_body = e.read().decode("utf-8", errors="replace")
            except Exception:
                err_body = ""
            return (e.code, f"HTTP {e.code}: {err_body[:400]}")
        except URLError as e:
            return (0, f"network error: {e.reason}")
        except Exception as e:
            return (0, f"unexpected error: {type(e).__name__}: {e}")

    # ------------------------------------------------------------------
    # Action handlers — Jira
    # ------------------------------------------------------------------

    def _jira_search(self, payload: dict) -> str:
        jql = (payload.get("jql") or "").strip()
        if not jql:
            return "jira_search error: missing 'jql' field"
        limit = min(int(payload.get("limit", 10)), MAX_RESULTS)
        fields = payload.get("fields") or ["summary", "status", "assignee", "updated", "priority"]

        status, data = self._request(
            "/rest/api/3/search/jql",
            method="POST",
            body={"jql": jql, "fields": fields, "maxResults": limit},
        )
        if status != 200 or not isinstance(data, dict):
            return f"jira_search failed: {data}"

        issues = data.get("issues", [])
        if not issues:
            return f"jira_search: 0 results for `{jql}`"

        lines = [f"jira_search: {len(issues)} result(s) for `{jql}`"]
        for it in issues:
            key = it.get("key", "?")
            f = it.get("fields", {}) or {}
            summary = f.get("summary", "")
            status_name = (f.get("status") or {}).get("name", "?")
            assignee = ((f.get("assignee") or {}).get("displayName")) or "unassigned"
            updated = (f.get("updated") or "")[:10]
            url = f"{self.base_url}/browse/{key}"
            lines.append(f"  {key}  [{status_name}]  {assignee}  {updated}  — {summary}")
            lines.append(f"      {url}")
        return "\n".join(lines)

    def _jira_get(self, payload: dict) -> str:
        key = (payload.get("key") or "").strip()
        if not key:
            return "jira_get error: missing 'key' field"
        status, data = self._request(
            f"/rest/api/3/issue/{urllib.parse.quote(key)}",
            params={"fields": "summary,status,assignee,reporter,priority,issuetype,created,updated,description,comment,labels,components"},
        )
        if status != 200 or not isinstance(data, dict):
            return f"jira_get failed for {key}: {data}"

        f = data.get("fields", {}) or {}
        url = f"{self.base_url}/browse/{key}"
        out = [
            f"=== {key}: {f.get('summary','')}",
            f"URL:       {url}",
            f"Status:    {(f.get('status') or {}).get('name','?')}",
            f"Type:      {(f.get('issuetype') or {}).get('name','?')}",
            f"Priority:  {(f.get('priority') or {}).get('name','?')}",
            f"Assignee:  {(f.get('assignee') or {}).get('displayName') or 'unassigned'}",
            f"Reporter:  {(f.get('reporter') or {}).get('displayName','?')}",
            f"Created:   {(f.get('created') or '')[:19]}",
            f"Updated:   {(f.get('updated') or '')[:19]}",
        ]
        labels = f.get("labels") or []
        if labels:
            out.append(f"Labels:    {', '.join(labels)}")
        components = [c.get("name") for c in (f.get("components") or []) if c.get("name")]
        if components:
            out.append(f"Components: {', '.join(components)}")
        desc = _adf_to_text(f.get("description"))
        if desc:
            out.append("\n--- Description ---")
            out.append(_truncate(desc, TRUNCATE_PAGE_CHARS))
        comments = ((f.get("comment") or {}).get("comments")) or []
        if comments:
            out.append(f"\n--- Comments ({len(comments)}) ---")
            for c in comments[-10:]:  # last 10
                author = (c.get("author") or {}).get("displayName", "?")
                created = (c.get("created") or "")[:19]
                body = _truncate(_adf_to_text(c.get("body")), TRUNCATE_FIELD_CHARS)
                out.append(f"\n[{created}] {author}:")
                out.append(body)
        return "\n".join(out)

    # ------------------------------------------------------------------
    # Action handlers — Confluence
    # ------------------------------------------------------------------

    def _confluence_search(self, payload: dict) -> str:
        cql = (payload.get("cql") or "").strip()
        if not cql:
            return "confluence_search error: missing 'cql' field"
        limit = min(int(payload.get("limit", 10)), MAX_RESULTS)

        status, data = self._request(
            "/wiki/rest/api/content/search",
            params={"cql": cql, "limit": limit, "expand": "space,version"},
        )
        if status != 200 or not isinstance(data, dict):
            return f"confluence_search failed: {data}"

        results = data.get("results", [])
        if not results:
            return f"confluence_search: 0 results for `{cql}`"

        lines = [f"confluence_search: {len(results)} result(s) for `{cql}`"]
        for r in results:
            rid = r.get("id", "?")
            title = r.get("title", "")
            space = (r.get("space") or {}).get("key", "?")
            updated = ((r.get("version") or {}).get("when") or "")[:10]
            url = f"{self.base_url}/wiki/spaces/{space}/pages/{rid}"
            lines.append(f"  [{space}] {title} (id={rid}, updated {updated})")
            lines.append(f"      {url}")
        return "\n".join(lines)

    def _confluence_get(self, payload: dict) -> str:
        page_id = (payload.get("id") or "").strip()
        if not page_id:
            # Allow lookup by title + optional space
            title = (payload.get("title") or "").strip()
            space = (payload.get("space") or "").strip()
            if not title:
                return "confluence_get error: provide either 'id' or 'title' (with optional 'space')"
            params = {"title": title, "limit": 1, "expand": "space,version"}
            if space:
                params["spaceKey"] = space
            status, data = self._request("/wiki/rest/api/content", params=params)
            if status != 200 or not isinstance(data, dict):
                return f"confluence_get (by title) failed: {data}"
            results = data.get("results", [])
            if not results:
                return f"confluence_get: no page found for title='{title}' space='{space or 'any'}'"
            page_id = results[0].get("id", "")

        # Fetch full content. body.storage preserves tables/code blocks; body.view is rendered HTML.
        status, data = self._request(
            f"/wiki/rest/api/content/{urllib.parse.quote(page_id)}",
            params={"expand": "body.storage,space,version,ancestors"},
        )
        if status != 200 or not isinstance(data, dict):
            return f"confluence_get failed for id={page_id}: {data}"

        title = data.get("title", "")
        space = (data.get("space") or {}).get("key", "?")
        version = (data.get("version") or {}).get("number", "?")
        updated = ((data.get("version") or {}).get("when") or "")[:19]
        url = f"{self.base_url}/wiki/spaces/{space}/pages/{page_id}"
        storage = ((data.get("body") or {}).get("storage") or {}).get("value", "") or ""
        text = _strip_storage_html(storage)

        out = [
            f"=== {title}",
            f"Space:   {space}",
            f"URL:     {url}",
            f"Version: {version} (updated {updated})",
            "",
            _truncate(text, TRUNCATE_PAGE_CHARS),
        ]
        return "\n".join(out)

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self, input_str: str) -> str:
        if not input_str or not input_str.strip():
            return "atlassian: empty input. Provide a JSON object with an 'action' field."
        try:
            payload = json.loads(input_str)
        except json.JSONDecodeError as e:
            return f"atlassian: invalid JSON input ({e}). Example: {{\"action\":\"jira_search\",\"jql\":\"project=SUP\"}}"
        if not isinstance(payload, dict):
            return "atlassian: input must be a JSON object"

        action = (payload.get("action") or "").strip().lower()
        try:
            if action == "jira_search":
                return self._jira_search(payload)
            if action == "jira_get":
                return self._jira_get(payload)
            if action == "confluence_search":
                return self._confluence_search(payload)
            if action == "confluence_get":
                return self._confluence_get(payload)
            return (
                f"atlassian: unknown action '{action}'. "
                "Valid actions: jira_search, jira_get, confluence_search, confluence_get"
            )
        except Exception as e:
            logger.exception("AtlassianTool action %s failed", action)
            return f"atlassian {action} failed: {type(e).__name__}: {e}"


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _truncate(s: str, n: int) -> str:
    if not s:
        return ""
    if len(s) <= n:
        return s
    return s[:n].rstrip() + f"\n…[truncated, {len(s) - n} chars more]"


def _strip_storage_html(html: str) -> str:
    """
    Convert Confluence storage-format XHTML to readable plain text.
    Preserves table cells and code-block content (the things the
    existing confluence_crawler parser was eating). Stdlib only.
    """
    if not html:
        return ""
    s = html
    # Convert headings to text with newlines
    s = re.sub(r"</?(h[1-6])>", "\n", s, flags=re.IGNORECASE)
    # Paragraphs and breaks
    s = re.sub(r"</p\s*>", "\n", s, flags=re.IGNORECASE)
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.IGNORECASE)
    # Lists
    s = re.sub(r"<li[^>]*>", "\n• ", s, flags=re.IGNORECASE)
    s = re.sub(r"</li\s*>", "", s, flags=re.IGNORECASE)
    # Table cells/rows — preserve content with separators
    s = re.sub(r"</t[dh]\s*>", " | ", s, flags=re.IGNORECASE)
    s = re.sub(r"</tr\s*>", "\n", s, flags=re.IGNORECASE)
    # Code blocks
    s = re.sub(r"<ac:structured-macro[^>]*ac:name=\"code\"[^>]*>", "\n```\n", s, flags=re.IGNORECASE)
    s = re.sub(r"</ac:structured-macro>", "\n```\n", s, flags=re.IGNORECASE)
    # CDATA
    s = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", s, flags=re.DOTALL)
    # Strip remaining tags
    s = re.sub(r"<[^>]+>", "", s)
    # Decode all HTML entities (named + numeric) — handles &ldquo; &ndash; &nbsp; etc.
    s = html.unescape(s)
    # Collapse runs of whitespace and excessive newlines
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def _adf_to_text(node: Any) -> str:
    """
    Flatten Atlassian Document Format (ADF) JSON to plain text.
    Jira's REST v3 returns descriptions and comments as ADF — a tree of
    {type, content, text} nodes. We do a depth-first walk and join text.
    """
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(_adf_to_text(n) for n in node)
    if isinstance(node, dict):
        node_type = node.get("type", "")
        text = node.get("text", "")
        children = _adf_to_text(node.get("content"))
        if node_type in ("paragraph", "heading", "listItem", "bulletList", "orderedList"):
            return (text + children + "\n") if (text or children) else ""
        if node_type == "hardBreak":
            return "\n"
        if node_type == "codeBlock":
            return f"\n```\n{children}\n```\n"
        if node_type == "table":
            return "\n" + children + "\n"
        if node_type in ("tableRow",):
            return children.strip() + "\n"
        if node_type in ("tableHeader", "tableCell"):
            return children.strip() + " | "
        return text + children
    return ""
