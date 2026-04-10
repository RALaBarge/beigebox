"""
SF Ingest — MCP tool exposing Salesforce ingestion primitives.

Wraps the discrete capabilities developed for the Salesforce RAG ingestion
pipeline (list-view paging, native-case discovery, per-case fetch, markdown
writer, privacy scrubbers) as methods on a single MCP tool so the operator
agent can invoke them via JSON dispatch without shelling out to the
`salesforce_ingest.py` script.

All Aura calls route through the shared `BBClient.aura()` helper, which talks
to BrowserBox (`ws://localhost:9009`) via `inject.aura` and strips SF's
`while(1);` anti-JSON-hijack prefix / aura error envelopes.

Config (config.yaml):
    tools:
      sf_ingest:
        enabled: true
        ws_url: ws://localhost:9009
        timeout: 120
        out_dir: ./workspace/out/rag/SF

Invocation (single-tool dispatch by method — all input is JSON):
    {"method":"list_view","view_api_name":"All_Open_Cases_SXPortal"}
    {"method":"discover_native"}
    {"method":"discover_native","cutoff":"2024-01-01","views":["All_Open_Cases_SXPortal"]}
    {"method":"fetch_case","case_id":"500..."}
    {"method":"write_case","case":{...}}
    {"method":"scrub","text":"...","mode":"emails"}

Privacy semantics (matches salesforce_ingest.py):
    - Account.Name: KEPT
    - Email addresses in text bodies: scrubbed → [email]
    - Feed actors classified as external customers → "[customer]"
    - Internal users keep their displayName
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from beigebox.tools._bb_client import BBClient

logger = logging.getLogger(__name__)

# ── Constants (duplicated from salesforce_ingest.py — do NOT import it) ──────

CASE_FIELDS = [
    # identity / system
    "Case.Id",
    "Case.CaseNumber",
    "Case.OwnerId",
    "Case.IsClosed",
    "Case.IsEscalated",
    # status / workflow
    "Case.Status",
    "Case.Priority",
    "Case.Origin",
    "Case.Type",                          # dropdown half of "Case Details" section
    # description / content
    "Case.Subject",
    "Case.Description",                   # text half of "Case Details" section
    "Case.Case_Summary__c",               # custom case summary field
    # account / contact
    "Case.Account.Name",
    "Case.AccountId",
    "Case.Account_Name__c",               # custom account name field
    # dates
    "Case.CreatedDate",
    "Case.ClosedDate",
    "Case.LastModifiedDate",
    # internal status (emoji picklist — "With Development", "Awaiting Customer", etc.)
    "Case.Internal_Case_Status__c",
    # SLA fields
    "Case.SLA_Status__c",
    "Case.SLA_Due__c",
    "Case.SLA_Target_Date__c",
    "Case.LastCustomerUpdate__c",
    "Case.LastStatusChange__c",
    # jira links (live SF custom fields, distinct from the Jira_Issues_CASE__r related list)
    "Case.Internal_Jira_Key__c",
    "Case.Tagged_Existing_Jira_Bug_Issue__c",
    # known issue + external ref
    "Case.Known_Issue__r.Name",
    "Case.External_ID__c",
]

JIRA_FIELDS = [
    "Jira_Issue__c.Id",
    "Jira_Issue__c.Name",
    "Jira_Issue__c.Jira_Issue_Name__c",
    "Jira_Issue__c.Jira_Issue_Number__c",
    "Jira_Issue__c.Jira_URL__c",
]

LIST_VIEWS = ["All_Open_Cases_SXPortal", "All_Closed_Cases_SXPortal"]

NATIVE_CUTOFF = "2024-01-01"

# Internal org names — actors from these companies are treated as agents, not customers.
# Override via config or subclass for different orgs.
_INTERNAL_ORG_NAMES = set()  # populated at init from config

CUSTOMER_USER_TYPES = {
    "CsnOnly", "PowerCustomerSuccess", "CustomerSuccess",
    "CspLitePortal", "GuestUser", "PowerPartner",
}


# ── Privacy helpers (pure, re-exported as the `scrub` method) ────────────────

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")


def scrub_emails(text: str) -> str:
    return _EMAIL_RE.sub("[email]", text or "")


def is_customer_actor(actor: dict) -> bool:
    """True if a feed actor looks like an external customer (vs internal agent)."""
    if not actor:
        return False
    utype = (actor.get("userType") or "").strip()
    if utype in CUSTOMER_USER_TYPES:
        return True
    company = (actor.get("companyName") or "").lower()
    if company and not any(org in company for org in _INTERNAL_ORG_NAMES):
        return True
    return False


def actor_label(actor: dict) -> str:
    """Real name for internal agents, [customer] for externals."""
    if is_customer_actor(actor):
        return "[customer]"
    return (actor or {}).get("displayName") or "[unknown]"


def _sanitize_filename(s: str) -> str:
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", s or "")
    s = re.sub(r"\s+", " ", s).strip()
    return s[:80].rstrip(". ")


# ── Tool ─────────────────────────────────────────────────────────────────────

class SfIngestTool:
    """
    MCP-invokable tool: Salesforce ingestion primitives via BrowserBox/Aura.

    Dispatch is by a JSON `method` field; `run()` returns a JSON string.
    """

    capture_tool_io: bool  = True
    max_context_chars: int = 6000

    description = (
        "Salesforce ingestion primitives via BrowserBox + Aura. "
        'Input is JSON with a "method" field.\n'
        "Methods:\n"
        '  {"method":"list_view","view_api_name":"...","fields":[...],"sort_by":[...],'
        '"page_size":100,"max_records":null}  → page a Case list view\n'
        '  {"method":"discover_native","cutoff":"2024-01-01","views":[...]}  '
        '→ dedup native cases across views\n'
        '  {"method":"fetch_case","case_id":"500..."}  '
        '→ core fields + Jira related list + chatter feed\n'
        '  {"method":"write_case","case":{...},"out_dir":"..."}  '
        '→ write per-case markdown, return path\n'
        '  {"method":"scrub","text":"...","mode":"emails"}  '
        '→ privacy helpers (emails | actor_label | is_customer_actor)'
    )

    def __init__(
        self,
        ws_url: str = "ws://localhost:9009",
        timeout: float = 120.0,
        out_dir: str | Path | None = None,
        internal_org_names: list[str] | None = None,
    ):
        global _INTERNAL_ORG_NAMES
        if internal_org_names:
            _INTERNAL_ORG_NAMES = {n.lower() for n in internal_org_names}
        self._ws_url  = ws_url
        self._timeout = timeout
        # Default output: <app_root>/workspace/out/rag/SF
        if out_dir:
            self._out_dir = Path(out_dir)
        else:
            self._out_dir = (
                Path(__file__).parent.parent.parent
                / "workspace" / "out" / "rag" / "SF"
            )

    def _client(self) -> BBClient:
        return BBClient(ws_url=self._ws_url, timeout=self._timeout)

    # ── MCP entry point ────────────────────────────────────────────────

    def run(self, input_str: str) -> str:
        try:
            params = json.loads(input_str.strip()) if input_str and input_str.strip() else {}
        except json.JSONDecodeError:
            return json.dumps({"error": 'input must be JSON with a "method" field'})

        if not isinstance(params, dict):
            return json.dumps({"error": "input must be a JSON object"})

        method = params.get("method", "")
        if not method:
            return json.dumps({"error": 'missing "method" field'})

        try:
            if method == "list_view":
                view = params.get("view_api_name", "")
                if not view:
                    return json.dumps({"error": 'list_view requires "view_api_name"'})
                result = asyncio.run(self.list_view(
                    view_api_name = view,
                    fields        = params.get("fields"),
                    sort_by       = params.get("sort_by"),
                    page_size     = int(params.get("page_size", 100)),
                    max_records   = params.get("max_records"),
                ))
            elif method == "discover_native":
                result = asyncio.run(self.discover_native(
                    cutoff = params.get("cutoff", NATIVE_CUTOFF),
                    views  = params.get("views"),
                ))
            elif method == "fetch_case":
                case_id = params.get("case_id", "")
                if not case_id:
                    return json.dumps({"error": 'fetch_case requires "case_id"'})
                result = asyncio.run(self.fetch_case(case_id))
            elif method == "write_case":
                case = params.get("case")
                if not isinstance(case, dict):
                    return json.dumps({"error": 'write_case requires "case" object'})
                result = self.write_case(case, out_dir=params.get("out_dir"))
            elif method == "scrub":
                text = params.get("text", "")
                mode = params.get("mode", "emails")
                result = self.scrub(text, mode=mode)
            else:
                return json.dumps({"error": f"unknown method: {method}"})
        except TimeoutError as e:
            return json.dumps({"error": f"timeout: {e}"})
        except RuntimeError as e:
            return json.dumps({"error": str(e)})
        except Exception as e:
            logger.error("sf_ingest: %s failed — %s: %s", method, type(e).__name__, e)
            return json.dumps({"error": f"{type(e).__name__}: {e}"})

        try:
            return json.dumps(result, default=str)
        except Exception:
            return json.dumps({"result": str(result)})

    # ── Methods ────────────────────────────────────────────────────────

    async def list_view(
        self,
        view_api_name: str,
        fields: list[str] | None = None,
        sort_by: list[str] | None = None,
        page_size: int = 100,
        max_records: int | None = None,
    ) -> list[dict]:
        """
        Page through any Case list view via
        `aura://ListUiController/ACTION$postListRecordsByName`.

        Returns flat list of {id, caseNumber, createdDate, ...} dicts with one
        entry per record field. Honors SF's 2000-record cap and stops cleanly.
        """
        fields  = fields  or ["Case.Id", "Case.CaseNumber", "Case.CreatedDate"]
        sort_by = sort_by if sort_by is not None else ["-Case.CreatedDate"]

        out: list[dict] = []
        token: str | None = None
        page = 0
        client = self._client()

        while True:
            query: dict[str, Any] = {
                "fields":   fields,
                "pageSize": page_size,
                "sortBy":   sort_by,
            }
            if token:
                query["pageToken"] = token

            rv = await client.aura(
                "aura://ListUiController/ACTION$postListRecordsByName",
                {
                    "listRecordsQuery": query,
                    "listViewApiName":  view_api_name,
                    "objectApiName":    "Case",
                },
            )

            recs = rv.get("records", []) or []
            for r in recs:
                f = r.get("fields", {}) or {}
                row: dict[str, Any] = {}
                for fld in fields:
                    key = fld.split(".", 1)[-1]
                    row[_camel_key(key)] = (f.get(key, {}) or {}).get("value", "")
                # Stable canonical keys that list_view_records used
                if "id" not in row and "Id" in row:
                    row["id"] = row.pop("Id")
                out.append(row)
                if max_records is not None and len(out) >= max_records:
                    return out

            token = rv.get("nextPageToken")
            page += 1
            if not token or len(recs) < page_size:
                break
            try:
                if int(token) >= 2000:
                    logger.warning("[sf_ingest.list_view] %s: hit SF 2000-record cap",
                                   view_api_name)
                    break
            except (TypeError, ValueError):
                pass
            if page > 50:
                logger.warning("[sf_ingest.list_view] %s: hit page cap", view_api_name)
                break

        return out

    async def discover_native(
        self,
        cutoff: str = NATIVE_CUTOFF,
        views: list[str] | None = None,
    ) -> list[dict]:
        """
        Page each configured list view newest-first, stop at `cutoff`, dedupe
        by Id. Returns a list of {id, caseNumber, createdDate} stubs for all
        SF-native cases created on/after `cutoff`.
        """
        views = views or list(LIST_VIEWS)
        all_recs: dict[str, dict] = {}
        client = self._client()

        for view in views:
            token: str | None = None
            page = 0
            stopped_at_cutoff = False

            while True:
                query: dict[str, Any] = {
                    "fields":   ["Case.Id", "Case.CaseNumber", "Case.CreatedDate"],
                    "pageSize": 100,
                    "sortBy":   ["-Case.CreatedDate"],
                }
                if token:
                    query["pageToken"] = token

                rv = await client.aura(
                    "aura://ListUiController/ACTION$postListRecordsByName",
                    {
                        "listRecordsQuery": query,
                        "listViewApiName":  view,
                        "objectApiName":    "Case",
                    },
                )

                recs = rv.get("records", []) or []
                for r in recs:
                    f = r.get("fields", {}) or {}
                    created = (f.get("CreatedDate", {}) or {}).get("value", "")[:10]
                    if created and created < cutoff:
                        stopped_at_cutoff = True
                        break
                    stub = {
                        "id":          (f.get("Id", {}) or {}).get("value", ""),
                        "caseNumber":  (f.get("CaseNumber", {}) or {}).get("value", ""),
                        "createdDate": created,
                    }
                    if stub["id"]:
                        all_recs.setdefault(stub["id"], stub)

                if stopped_at_cutoff:
                    break
                token = rv.get("nextPageToken")
                page += 1
                if not token or len(recs) < 100:
                    break
                try:
                    if int(token) >= 2000:
                        logger.warning("[sf_ingest.discover_native] %s: hit SF 2000-record cap", view)
                        break
                except (TypeError, ValueError):
                    pass
                if page > 50:
                    logger.warning("[sf_ingest.discover_native] %s: hit page cap", view)
                    break

        return list(all_recs.values())

    async def fetch_case(self, case_id: str) -> dict:
        """
        Fetch core Case fields + linked JIRA related list + chatter feed.
        Mirrors `salesforce_ingest.fetch_case`. Returns a JSON-serializable dict.
        """
        client = self._client()

        rec = await client.aura(
            "aura://RecordUiController/ACTION$getRecordWithFields",
            {"recordId": case_id, "fields": CASE_FIELDS},
        )
        f = rec.get("fields", {}) or {}

        def fv(name: str, sub: str | None = None) -> Any:
            d = f.get(name, {}) or {}
            v = d.get("value")
            if sub and isinstance(v, dict):
                return ((v.get("fields") or {}).get(sub, {}) or {}).get("value")
            return v

        case: dict[str, Any] = {
            # identity / system
            "id":               case_id,
            "caseNumber":       fv("CaseNumber") or "",
            "ownerId":          fv("OwnerId") or "",
            "isClosed":         fv("IsClosed"),
            "isEscalated":      fv("IsEscalated"),
            # status / workflow
            "status":           fv("Status") or "",
            "priority":         fv("Priority") or "",
            "origin":           fv("Origin") or "",
            "type":             fv("Type") or "",            # dropdown half of "Case Details"
            # description / content
            "subject":          fv("Subject") or "",
            "description":      fv("Description") or "",    # text half of "Case Details"
            "caseSummary":      fv("Case_Summary__c") or "",  # the "Case Summary" Ryan asked for
            # account / contact
            "accountName":      fv("Account", "Name") or "",
            "accountId":        fv("AccountId") or "",
            "accountNameField": fv("Account_Name__c") or "",  # custom account name field
            # dates
            "createdDate":      fv("CreatedDate") or "",
            "closedDate":       fv("ClosedDate") or "",
            "lastModifiedDate": fv("LastModifiedDate") or "",
            # jira links (live SF custom — separate from the Jira_Issues_CASE__r related list)
            "internalJiraKey":  fv("Internal_Jira_Key__c") or "",
            "taggedJiraBugId":  fv("Tagged_Existing_Jira_Bug_Issue__c") or "",
            # known issue + external ref
            "knownIssue":       fv("Known_Issue__r", "Name") or "",
            "externalId":       fv("External_ID__c") or "",
            # populated below
            "jira":             [],
            "feed":             [],
        }

        # JIRA related list — best-effort
        try:
            rv = await client.aura(
                "aura://RelatedListUiController/ACTION$postRelatedListRecords",
                {
                    "parentRecordId": case_id,
                    "relatedListId":  "Jira_Issues_CASE__r",
                    "listRecordsQuery": {
                        "fields":   JIRA_FIELDS,
                        "pageSize": 50,
                        "sortBy":   [],
                    },
                },
            )
            for j in rv.get("records", []) or []:
                jf = j.get("fields", {}) or {}
                case["jira"].append({
                    "number": (jf.get("Jira_Issue_Number__c", {}) or {}).get("value", ""),
                    "name":   (jf.get("Jira_Issue_Name__c", {}) or {}).get("value", ""),
                    "url":    (jf.get("Jira_URL__c", {}) or {}).get("value", ""),
                })
        except Exception as e:
            logger.debug("sf_ingest.fetch_case: jira skipped for %s: %s", case_id, e)

        # Chatter feed — best-effort
        try:
            rv = await client.aura(
                "serviceComponent://ui.chatter.components.aura.components.forceChatter."
                "chatter.RecordFeedContainerController/ACTION$getCompactFeedModel",
                {"firstPageSize": 100, "nextPageSize": 100, "recordId": case_id},
            )
            fm       = rv.get("feedModel") or {}
            elements = ((fm.get("feedElementCollection") or {}).get("elements") or [])
            for e in elements:
                actor = e.get("actor") or {}
                body  = (e.get("body") or {}).get("text") or ""
                case["feed"].append({
                    "type":  e.get("type") or e.get("feedElementType") or "",
                    "date":  e.get("createdDate") or "",
                    "actor": actor_label(actor),
                    "body":  scrub_emails(body),
                })
        except Exception as e:
            logger.debug("sf_ingest.fetch_case: feed skipped for %s: %s", case_id, e)

        case["ingestedAt"] = datetime.now(timezone.utc).isoformat()
        return case

    def write_case(self, case: dict, out_dir: str | Path | None = None) -> str:
        """
        Markdown writer matching `salesforce_ingest.write_case` format.
        Default `out_dir` is `workspace/out/rag/SF/`. Returns absolute path.
        """
        target = Path(out_dir) if out_dir else self._out_dir
        target.mkdir(parents=True, exist_ok=True)

        case_num = case.get("caseNumber") or "unknown"
        subject  = case.get("subject") or ""
        fname    = f"{case_num} - {_sanitize_filename(subject)}.md"
        out_path = target / fname

        # Build the metadata header. Account_Name__c (custom field) wins when populated;
        # fall back to Account.Name (standard relationship) so we always show *something*.
        account_display = case.get("accountNameField") or case.get("accountName") or "—"
        type_str = case.get("type") or ""
        origin_str = case.get("origin") or ""
        escalated_marker = " 🚨" if case.get("isEscalated") else ""

        lines: list[str] = [
            f"# {case_num}: {subject}{escalated_marker}",
            "",
            f"**Status:** {case.get('status','')}  •  **Priority:** {case.get('priority','')}"
            + (f"  •  **Type:** {type_str}" if type_str else "")
            + (f"  •  **Origin:** {origin_str}" if origin_str else ""),
            f"**Created:** {case.get('createdDate','')}  •  "
            f"**Closed:** {case.get('closedDate') or 'open'}",
            f"**Last Modified:** {case.get('lastModifiedDate','')}",
            f"**Account:** {account_display}",
        ]
        if case.get("knownIssue"):
            lines.append(f"**Known Issue:** {case['knownIssue']}")
        if case.get("internalJiraKey") or case.get("taggedJiraBugId"):
            jira_bits = []
            if case.get("internalJiraKey"):
                jira_bits.append(f"Internal: {case['internalJiraKey']}")
            if case.get("taggedJiraBugId"):
                jira_bits.append(f"Tagged Bug: {case['taggedJiraBugId']}")
            lines.append(f"**Jira Refs:** {' • '.join(jira_bits)}")
        if case.get("externalId"):
            lines.append(f"**External ID:** {case['externalId']}")
        lines.append("")

        # Case Summary (custom field — surfaces what the team writes for triage)
        if case.get("caseSummary"):
            lines += ["## Case Summary", "", scrub_emails(case["caseSummary"]), ""]

        if case.get("description"):
            lines += ["## Description", "", scrub_emails(case["description"]), ""]

        jira = case.get("jira") or []
        if jira:
            lines += ["## Linked JIRA Issues", ""]
            for j in jira:
                num  = j.get("number") or "?"
                name = j.get("name") or ""
                url  = j.get("url") or ""
                lines.append(f"- **{num}** {name} — {url}".rstrip(" —"))
            lines.append("")

        feed_msgs = [m for m in (case.get("feed") or []) if m.get("body")]
        if feed_msgs:
            lines += [f"## Conversation ({len(feed_msgs)} messages)", ""]
            for m in feed_msgs:
                ts = (m.get("date") or "")[:19].replace("T", " ")
                lines += [f"### {ts} — {m.get('actor','')}", "", m["body"], ""]

        out_path.write_text("\n".join(lines), encoding="utf-8")
        return str(out_path)

    def scrub(self, text: Any, mode: str = "emails") -> Any:
        """
        Privacy helpers:
          - mode="emails"         → scrub_emails(text)  (text: str)
          - mode="actor_label"    → actor_label(actor)  (text: dict)
          - mode="is_customer_actor" → bool             (text: dict)
        """
        if mode == "emails":
            return scrub_emails(text if isinstance(text, str) else str(text or ""))
        if mode == "actor_label":
            return actor_label(text if isinstance(text, dict) else {})
        if mode == "is_customer_actor":
            return is_customer_actor(text if isinstance(text, dict) else {})
        return {"error": f"unknown scrub mode: {mode}"}


def _camel_key(field_name: str) -> str:
    """Map `CaseNumber` → `caseNumber`, leave already-lowered keys alone."""
    if not field_name:
        return field_name
    return field_name[0].lower() + field_name[1:]
