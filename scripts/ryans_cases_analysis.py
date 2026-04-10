#!/usr/bin/env python3
"""
Fetch Ryan's Open Cases, cross-reference with local JIRA/Confluence RAG,
and attempt answers via local Ollama.

Read-only against Salesforce. All inference stays local (Ollama).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path

# Allow running from project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from beigebox.tools.sf_ingest import SfIngestTool

# ── Config ──────────────────────────────────────────────────────────────────

WS_URL   = "ws://localhost:9009"
OUT_DIR  = Path(__file__).resolve().parent.parent / "workspace" / "out" / "rag" / "SF"
RAG_JIRA = Path(__file__).resolve().parent.parent / "workspace" / "out" / "rag" / "JIRA"
RAG_CONF = Path(__file__).resolve().parent.parent / "workspace" / "out" / "rag" / "CONFLUENCE"
LIST_VIEW = "Ryan_s_Open_Cases"
RESULTS_FILE = Path(__file__).resolve().parent.parent / "workspace" / "out" / "ryans_open_cases.json"


async def main():
    tool = SfIngestTool(ws_url=WS_URL, timeout=60.0, out_dir=str(OUT_DIR))

    # ── Step 1: Fetch list view ─────────────────────────────────────────────
    print(f"[1/4] Fetching list view: {LIST_VIEW}")
    stubs = await tool.list_view(
        view_api_name=LIST_VIEW,
        fields=[
            "Case.Id", "Case.CaseNumber", "Case.Subject",
            "Case.Status", "Case.Priority", "Case.CreatedDate",
            "Case.Account_Name__c",
            "Case.Internal_Case_Status__c",
            "Case.SLA_Status__c",
            "Case.SLA_Due__c",
            "Case.SLA_Target_Date__c",
            "Case.LastCustomerUpdate__c",
            "Case.LastStatusChange__c",
        ],
        page_size=200,
    )
    print(f"       Found {len(stubs)} open cases")

    # ── Step 2: Fetch full details for each case ────────────────────────────
    print(f"[2/4] Fetching full details for {len(stubs)} cases...")
    cases = []
    for i, stub in enumerate(stubs, 1):
        case_id = stub.get("id", "")
        case_num = stub.get("caseNumber", "?")
        if not case_id:
            continue
        try:
            case = await tool.fetch_case(case_id)
            # Merge list-view-only fields from stub into case
            case["internalCaseStatus"] = stub.get("internal_Case_Status__c") or ""
            case["slaStatus"] = stub.get("sLA_Status__c") or ""
            case["slaDue"] = stub.get("sLA_Due__c") or ""
            case["slaTargetDate"] = stub.get("sLA_Target_Date__c") or ""
            case["lastCustomerUpdate"] = stub.get("lastCustomerUpdate__c") or ""
            case["lastStatusChange"] = stub.get("lastStatusChange__c") or ""
            # Also write markdown for RAG
            tool.write_case(case)
            jira_n = len(case.get("jira") or [])
            feed_n = len(case.get("feed") or [])
            print(f"       [{i}/{len(stubs)}] {case_num} - {case.get('subject','')[:60]} "
                  f"(jira={jira_n}, feed={feed_n}) [{case['internalCaseStatus']}]")
            cases.append(case)
        except Exception as e:
            print(f"       [{i}/{len(stubs)}] {case_num} FAILED: {e}")
        await asyncio.sleep(0.25)  # rate limit

    # ── Step 3: Cross-reference with JIRA and Confluence ────────────────────
    print(f"[3/4] Cross-referencing with local JIRA ({RAG_JIRA}) and Confluence ({RAG_CONF})...")

    # Build JIRA index (filename -> content snippet)
    jira_index: dict[str, str] = {}
    if RAG_JIRA.exists():
        for f in RAG_JIRA.glob("*.md"):
            jira_index[f.stem] = f.name

    # Build Confluence index
    conf_index: dict[str, tuple[str, str]] = {}  # keyword -> (filename, title)
    if RAG_CONF.exists():
        for f in RAG_CONF.glob("*.md"):
            # filename format: "<id> - <title>.md"
            parts = f.stem.split(" - ", 1)
            title = parts[1] if len(parts) > 1 else f.stem
            conf_index[title.lower()] = (f.name, title)

    for case in cases:
        case["_linked_jira_files"] = []
        case["_linked_conf_files"] = []

        # Direct JIRA links from SF fields
        jira_keys = set()
        for j in (case.get("jira") or []):
            num = j.get("number", "")
            if num:
                jira_keys.add(num)
        if case.get("internalJiraKey"):
            jira_keys.add(case["internalJiraKey"])
        if case.get("taggedJiraBugId"):
            jira_keys.add(case["taggedJiraBugId"])

        # Match JIRA files
        for jkey in jira_keys:
            jkey_clean = jkey.strip()
            for stem, fname in jira_index.items():
                if jkey_clean in stem:
                    case["_linked_jira_files"].append(fname)

        # Keyword-based Confluence matching
        subject = (case.get("subject") or "").lower()
        description = (case.get("description") or "").lower()
        combined_text = f"{subject} {description}"

        # Extract meaningful keywords (3+ chars, skip common words)
        stop_words = {
            "the", "and", "for", "are", "but", "not", "you", "all", "can",
            "had", "her", "was", "one", "our", "out", "has", "have", "from",
            "this", "that", "with", "they", "been", "will", "when", "what",
            "case", "issue", "error", "please", "would", "should", "could",
            "about", "after", "before", "being", "between", "both", "does",
            "each", "into", "just", "like", "more", "most", "only", "other",
            "some", "than", "them", "then", "there", "these", "very", "which",
            "while", "test", "need", "help", "able", "also",
        }
        words = set(re.findall(r'[a-z]{3,}', combined_text)) - stop_words

        # Also look for product/feature terms
        product_terms = set()
        for term in [
            "cpq", "connector", "resource management", "utilization",
            "utilisation", "availability", "time entry", "expense",
            "assignment", "proposal", "intercompany", "ica",
            "task plan", "csv", "import", "license", "dashboard",
            "report", "reporting", "jira", "integration",
            "analyzer", "communities", "implementation",
            "sandbox", "production", "api", "sso",
        ]:
            if term in combined_text:
                product_terms.add(term)

        search_terms = words | product_terms
        matches = []
        for conf_title_lower, (conf_fname, conf_title) in conf_index.items():
            overlap = sum(1 for w in search_terms if w in conf_title_lower)
            if overlap >= 2:
                confidence = min(overlap / max(len(search_terms) * 0.3, 1), 1.0)
                matches.append((confidence, conf_fname, conf_title))

        # Keep matches above ~50% confidence
        matches.sort(reverse=True)
        case["_linked_conf_files"] = [
            {"file": m[1], "title": m[2], "confidence": round(m[0], 2)}
            for m in matches[:5] if m[0] >= 0.4
        ]

        jira_count = len(case["_linked_jira_files"])
        conf_count = len(case["_linked_conf_files"])
        if jira_count or conf_count:
            print(f"       {case['caseNumber']}: {jira_count} JIRA, {conf_count} Confluence matches")

    # ── Save results ────────────────────────────────────────────────────────
    print(f"[4/4] Saving results to {RESULTS_FILE}")
    RESULTS_FILE.parent.mkdir(parents=True, exist_ok=True)

    # Build a clean summary for each case
    summary = []
    for case in cases:
        entry = {
            "caseNumber": case.get("caseNumber"),
            "subject": case.get("subject"),
            "status": case.get("status"),
            "priority": case.get("priority"),
            "type": case.get("type"),
            "account": case.get("accountNameField") or case.get("accountName"),
            "created": case.get("createdDate"),
            "lastModified": case.get("lastModifiedDate"),
            "internalCaseStatus": case.get("internalCaseStatus"),
            "slaStatus": case.get("slaStatus"),
            "slaDue": case.get("slaDue"),
            "slaTargetDate": case.get("slaTargetDate"),
            "lastCustomerUpdate": case.get("lastCustomerUpdate"),
            "lastStatusChange": case.get("lastStatusChange"),
            "isClosed": case.get("isClosed"),
            "isEscalated": case.get("isEscalated"),
            "caseSummary": case.get("caseSummary"),
            "description": (case.get("description") or "")[:2000],
            "internalJiraKey": case.get("internalJiraKey"),
            "taggedJiraBugId": case.get("taggedJiraBugId"),
            "jira": case.get("jira"),
            "feedMessageCount": len(case.get("feed") or []),
            "feedMessages": [
                {"date": m.get("date","")[:19], "actor": m.get("actor",""), "body": m.get("body","")[:500]}
                for m in (case.get("feed") or [])[:10]  # last 10 messages
            ],
            "linkedJiraFiles": case.get("_linked_jira_files", []),
            "linkedConfluenceFiles": case.get("_linked_conf_files", []),
        }
        summary.append(entry)

    RESULTS_FILE.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(f"\nDone. {len(summary)} cases saved to {RESULTS_FILE}")
    print(f"Use this file for the LLM analysis step.")


if __name__ == "__main__":
    asyncio.run(main())
