#!/usr/bin/env python3
"""
Re-run analysis on the 6 truncated cases with higher token limit.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx

BASE     = Path(__file__).resolve().parent.parent
CASES_F  = BASE / "workspace" / "out" / "ryans_open_cases.json"
RAG_JIRA = BASE / "workspace" / "out" / "rag" / "JIRA"
RAG_CONF = BASE / "workspace" / "out" / "rag" / "CONFLUENCE"
RAG_SF   = BASE / "workspace" / "out" / "rag" / "SF"
OUTPUT_F = BASE / "workspace" / "out" / "ryans_cases_rerun.md"

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL      = "qwen3:30b-a3b"

TRUNCATED_CASES = ["00308949", "00308794", "00308321", "00308207", "00307451", "00301345"]


def load_linked_context(case: dict) -> str:
    chunks = []
    for jf in case.get("linkedJiraFiles", []):
        path = RAG_JIRA / jf
        if path.exists():
            chunks.append(f"### JIRA: {jf}\n{path.read_text()[:3000]}")
    for j in case.get("jira", []):
        num = j.get("number", "")
        if num:
            matches = list(RAG_JIRA.glob(f"{num}*"))
            for m in matches[:1]:
                if m.name not in case.get("linkedJiraFiles", []):
                    chunks.append(f"### JIRA: {m.name}\n{m.read_text()[:3000]}")
    for cf in case.get("linkedConfluenceFiles", []):
        path = RAG_CONF / cf.get("file", "")
        if path.exists():
            chunks.append(f"### Confluence: {cf.get('title', '')}\n{path.read_text()[:3000]}")
    case_num = case.get("caseNumber", "")
    if case_num:
        for m in list(RAG_SF.glob(f"{case_num}*"))[:1]:
            chunks.append(f"### Full Case Record\n{m.read_text()[:4000]}")
    return "\n\n---\n\n".join(chunks) if chunks else ""


def build_prompt(case: dict, context: str) -> str:
    msgs = case.get("feedMessages", [])
    feed_summary = ""
    if msgs:
        feed_lines = []
        for m in msgs[-8:]:
            actor = m.get("actor", "?")
            body = m.get("body", "")[:300]
            feed_lines.append(f"  [{actor}]: {body}")
        feed_summary = "\n".join(feed_lines)

    prompt = f"""/no_think
You are a senior Kantata PSA support engineer. Analyze this support case and provide ALL FOUR sections — do not stop early:

1. **Root Cause Assessment** — what is most likely causing this issue
2. **Linked Evidence** — what the JIRA tickets and Confluence docs tell us
3. **Suggested Next Steps** — concrete actions to move toward resolution (be specific)
4. **Confidence Level** — Low/Medium/High with reasoning

## Case #{case.get('caseNumber')} — {case.get('subject')}

**Account:** {case.get('account', '?')}
**Status:** {case.get('status')} | **Priority:** {case.get('priority')}
**Internal Status:** {case.get('internalCaseStatus', '?')} | **SLA:** {case.get('slaStatus', '?')}
**Type:** {case.get('type', '?')} | **Created:** {case.get('created', '?')}
**Escalated:** {case.get('isEscalated', False)}

### Case Summary (from agent)
{case.get('caseSummary') or 'N/A'}

### Description
{(case.get('description') or 'N/A')[:2000]}

### Recent Conversation ({case.get('feedMessageCount', 0)} total messages)
{feed_summary or 'No messages available'}

### SF JIRA Links
{json.dumps(case.get('jira', []), indent=2) if case.get('jira') else 'None'}

"""
    if context:
        prompt += f"### Linked Local Knowledge Base Documents\n{context}\n\n"

    prompt += """IMPORTANT: You MUST complete ALL FOUR sections (Root Cause, Evidence, Next Steps, Confidence). Do not stop after two sections."""

    return prompt


def call_ollama(prompt: str) -> str:
    with httpx.Client(timeout=600.0) as client:
        resp = client.post(OLLAMA_URL, json={
            "model": MODEL,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.3,
                "num_predict": 2500,
            },
        })
        resp.raise_for_status()
        return resp.json().get("response", "")


def main():
    cases = json.loads(CASES_F.read_text())
    truncated = [c for c in cases if c.get("caseNumber") in TRUNCATED_CASES]
    print(f"Re-running {len(truncated)} truncated cases with num_predict=2500\n")

    lines = [
        "# Re-run: Previously Truncated Cases (Full Analysis)",
        f"**Generated:** {time.strftime('%Y-%m-%d %H:%M')}",
        f"**Model:** {MODEL} (local Ollama, num_predict=2500)",
        "", "---", "",
    ]

    for i, case in enumerate(truncated, 1):
        num = case.get("caseNumber", "?")
        subject = case.get("subject", "?")
        print(f"[{i}/{len(truncated)}] {num} — {subject[:55]}...", end=" ", flush=True)

        context = load_linked_context(case)
        prompt = build_prompt(case, context)

        t0 = time.time()
        try:
            analysis = call_ollama(prompt)
            elapsed = time.time() - t0
            print(f"done ({elapsed:.0f}s)")
        except Exception as e:
            analysis = f"**Failed:** {e}"
            print(f"FAILED: {e}")

        lines.extend([
            f"## Case {num}: {subject}",
            f"**Account:** {case.get('account', '?')} | **Priority:** {case.get('priority')} | **Internal Status:** {case.get('internalCaseStatus', '?')}",
            "",
            analysis.strip(),
            "", "---", "",
        ])

    OUTPUT_F.write_text("\n".join(lines))
    print(f"\nDone. Written to {OUTPUT_F}")


if __name__ == "__main__":
    main()
