#!/usr/bin/env python3
"""
Analyze Ryan's open cases using local Ollama (qwen3:30b-a3b).
Reads the fetched case JSON, loads linked JIRA/Confluence files,
and asks the LLM to assess root cause + suggest next steps.

All inference stays on-box via Ollama.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx

# ── Paths ───────────────────────────────────────────────────────────────────

BASE     = Path(__file__).resolve().parent.parent
CASES_F  = BASE / "workspace" / "out" / "ryans_open_cases.json"
RAG_JIRA = BASE / "workspace" / "out" / "rag" / "JIRA"
RAG_CONF = BASE / "workspace" / "out" / "rag" / "CONFLUENCE"
RAG_SF   = BASE / "workspace" / "out" / "rag" / "SF"
OUTPUT_F = BASE / "workspace" / "out" / "ryans_cases_analysis.md"

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL      = "qwen3:30b-a3b"


def load_linked_context(case: dict) -> str:
    """Load content from linked JIRA and Confluence files."""
    chunks = []

    # JIRA files linked by key
    for jf in case.get("linkedJiraFiles", []):
        path = RAG_JIRA / jf
        if path.exists():
            content = path.read_text(encoding="utf-8")[:2000]
            chunks.append(f"### JIRA: {jf}\n{content}")

    # Also check SF's jira field for keys we can find locally
    for j in case.get("jira", []):
        num = j.get("number", "")
        if num:
            # Try to find the file
            matches = list(RAG_JIRA.glob(f"{num}*"))
            for m in matches[:1]:
                if m.name not in [jf for jf in case.get("linkedJiraFiles", [])]:
                    content = m.read_text(encoding="utf-8")[:2000]
                    chunks.append(f"### JIRA: {m.name}\n{content}")

    # Confluence files
    for cf in case.get("linkedConfluenceFiles", []):
        fname = cf.get("file", "")
        path = RAG_CONF / fname
        if path.exists():
            content = path.read_text(encoding="utf-8")[:2000]
            chunks.append(f"### Confluence: {cf.get('title', fname)} (confidence: {cf.get('confidence', '?')})\n{content}")

    # Also load the SF markdown if it exists (has the full chatter feed)
    case_num = case.get("caseNumber", "")
    if case_num:
        sf_matches = list(RAG_SF.glob(f"{case_num}*"))
        for m in sf_matches[:1]:
            content = m.read_text(encoding="utf-8")[:3000]
            chunks.append(f"### Full Case Record\n{content}")

    return "\n\n---\n\n".join(chunks) if chunks else ""


def build_prompt(case: dict, context: str) -> str:
    """Build the analysis prompt for a single case."""
    feed_summary = ""
    msgs = case.get("feedMessages", [])
    if msgs:
        feed_lines = []
        for m in msgs[-5:]:  # last 5 messages
            actor = m.get("actor", "?")
            body = m.get("body", "")[:200]
            feed_lines.append(f"  [{actor}]: {body}")
        feed_summary = "\n".join(feed_lines)

    prompt = f"""/no_think
You are a senior Kantata PSA support engineer. Analyze this support case and provide:
1. **Root Cause Assessment** — what is most likely causing this issue
2. **Linked Evidence** — what the JIRA tickets and Confluence docs tell us
3. **Suggested Next Steps** — concrete actions to move toward resolution
4. **Confidence Level** — how confident you are in your assessment (Low/Medium/High)

## Case #{case.get('caseNumber')} — {case.get('subject')}

**Account:** {case.get('account', '?')}
**Status:** {case.get('status')} | **Priority:** {case.get('priority')}
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
        prompt += f"""### Linked Local Knowledge Base Documents
{context}

"""

    prompt += """Provide a concise, actionable analysis. Focus on what the support engineer should do NEXT to resolve this case. If there's a known JIRA bug, call it out explicitly."""

    return prompt


def call_ollama(prompt: str, timeout: float = 300.0) -> str:
    """Call local Ollama and return the response."""
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(OLLAMA_URL, json={
            "model": MODEL,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.3,
                "num_predict": 1000,
            },
        })
        resp.raise_for_status()
        return resp.json().get("response", "")


def main():
    cases = json.loads(CASES_F.read_text(encoding="utf-8"))
    print(f"Loaded {len(cases)} cases. Analyzing with {MODEL} via local Ollama...\n")

    output_lines = [
        "# Ryan's Open Cases — AI Analysis",
        f"**Generated:** {time.strftime('%Y-%m-%d %H:%M')}",
        f"**Model:** {MODEL} (local Ollama)",
        f"**Cases:** {len(cases)}",
        "",
        "---",
        "",
    ]

    for i, case in enumerate(cases, 1):
        case_num = case.get("caseNumber", "?")
        subject = case.get("subject", "?")
        print(f"[{i}/{len(cases)}] Analyzing {case_num} — {subject[:60]}...", end=" ", flush=True)

        context = load_linked_context(case)
        prompt = build_prompt(case, context)

        t0 = time.time()
        try:
            analysis = call_ollama(prompt)
            elapsed = time.time() - t0
            print(f"done ({elapsed:.1f}s)")
        except Exception as e:
            elapsed = time.time() - t0
            analysis = f"**Analysis failed:** {e}"
            print(f"FAILED ({elapsed:.1f}s): {e}")

        output_lines.extend([
            f"## Case {case_num}: {subject}",
            f"**Account:** {case.get('account', '?')} | **Priority:** {case.get('priority')} | **Status:** {case.get('status')}",
            f"**JIRA links:** {len(case.get('jira',[]))} SF, {len(case.get('linkedJiraFiles',[]))} local | **Confluence:** {len(case.get('linkedConfluenceFiles',[]))}",
            "",
            analysis.strip(),
            "",
            "---",
            "",
        ])

    # Write output
    OUTPUT_F.write_text("\n".join(output_lines), encoding="utf-8")
    print(f"\nAnalysis complete. Written to {OUTPUT_F}")


if __name__ == "__main__":
    main()
