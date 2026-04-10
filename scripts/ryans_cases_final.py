#!/usr/bin/env python3
"""
Final consolidated analysis: re-check all answers with Slack context,
generate draft replies in Ryan's voice, and produce one master MD file.

Reads:
  - ryans_open_cases.json (case data)
  - ryans_cases_analysis.md (original analysis)
  - ryans_cases_rerun.md (rerun of truncated cases)
  - ryans_cases_parallels.md (parallel cases + JIRA linkage)
  - ral_notes.json (Ryan's own case notes)
  - workspace/out/rag/SLACK/ (Slack channel history)
  - workspace/out/rag/SF/ (full case records)
  - workspace/out/rag/JIRA/ (JIRA tickets)
  - 2600/skills/tone-ryan/SKILL.md (Ryan's tone pack)

Writes:
  - workspace/out/ryans_cases_FINAL.md (one file, everything)
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import httpx

BASE       = Path(__file__).resolve().parent.parent
CASES_F    = BASE / "workspace" / "out" / "ryans_open_cases.json"
ANALYSIS_F = BASE / "workspace" / "out" / "ryans_cases_analysis.md"
RERUN_F    = BASE / "workspace" / "out" / "ryans_cases_rerun.md"
PARALLELS_F= BASE / "workspace" / "out" / "ryans_cases_parallels.md"
RAL_F      = BASE / "workspace" / "out" / "ral_notes.json"
RAG_SF     = BASE / "workspace" / "out" / "rag" / "SF"
RAG_JIRA   = BASE / "workspace" / "out" / "rag" / "JIRA"
RAG_SLACK  = BASE / "workspace" / "out" / "rag" / "SLACK"
TONE_F     = BASE / "2600" / "skills" / "tone-ryan" / "SKILL.md"
OUTPUT_F   = BASE / "workspace" / "out" / "ryans_cases_FINAL.md"

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL      = "qwen3:30b-a3b"

RERUN_CASES = {"00308949", "00308794", "00308321", "00308207", "00307451", "00301345"}


def extract_section(md_text: str, case_num: str) -> str:
    """Extract a case section from a markdown report by case number."""
    pattern = rf'## Case {case_num}[:\s].*?(?=\n## Case |\Z)'
    m = re.search(pattern, md_text, re.DOTALL)
    return m.group(0).strip() if m else ""


def find_slack_mentions(case_num: str, slack_dir: Path) -> str:
    """Search Slack channel files for mentions of a case number or related terms."""
    hits = []
    if not slack_dir.exists():
        return ""

    # Search main channel files and daily chunks
    for f in slack_dir.rglob("*.md"):
        content = f.read_text(encoding="utf-8")
        if case_num in content:
            # Extract surrounding context (lines around the mention)
            lines = content.split("\n")
            for i, line in enumerate(lines):
                if case_num in line:
                    start = max(0, i - 2)
                    end = min(len(lines), i + 8)
                    chunk = "\n".join(lines[start:end])
                    hits.append(f"**[{f.name}]**\n{chunk}")
                    if len(hits) >= 5:
                        break
        if len(hits) >= 5:
            break

    return "\n\n".join(hits[:5]) if hits else ""


def find_slack_keyword_mentions(subject: str, account: str, slack_dir: Path) -> str:
    """Search Slack for keywords from case subject/account."""
    if not slack_dir.exists():
        return ""

    # Extract distinctive keywords from subject
    stop = {"the","and","for","are","but","not","you","all","can","has","have","from",
            "this","that","with","case","issue","error","not","when","does","unable"}
    words = [w for w in re.findall(r'[a-zA-Z]{4,}', subject) if w.lower() not in stop]
    search_terms = words[:4]  # top 4 keywords
    if account and len(account) > 3:
        search_terms.append(account.split()[0])  # first word of account name

    hits = []
    for f in slack_dir.rglob("*.md"):
        content = f.read_text(encoding="utf-8")
        content_lower = content.lower()
        matches = sum(1 for t in search_terms if t.lower() in content_lower)
        if matches >= 2:
            # Find the most relevant chunk
            lines = content.split("\n")
            for i, line in enumerate(lines):
                line_lower = line.lower()
                if any(t.lower() in line_lower for t in search_terms):
                    start = max(0, i - 1)
                    end = min(len(lines), i + 6)
                    chunk = "\n".join(lines[start:end])
                    hits.append(f"**[{f.name}]**\n{chunk}")
                    if len(hits) >= 3:
                        break
        if len(hits) >= 3:
            break

    return "\n\n".join(hits[:3]) if hits else ""


def get_ral_notes(case_num: str, ral_data: dict) -> str:
    """Get Ryan's own analysis notes for a case."""
    notes = [n for n in ral_data.get("ral_notes", []) if n.get("caseNumber") == case_num]
    if not notes:
        return ""
    lines = []
    for n in notes:
        lines.append(f"**{n.get('date', '?')} RAL:** {n.get('note', '')}")
    return "\n".join(lines)


def call_ollama(prompt: str) -> str:
    with httpx.Client(timeout=600.0) as client:
        resp = client.post(OLLAMA_URL, json={
            "model": MODEL,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.3, "num_predict": 3000},
        })
        resp.raise_for_status()
        return resp.json().get("response", "")


def build_final_prompt(case: dict, analysis: str, parallels: str,
                       slack_context: str, ral_notes: str, tone_pack: str) -> str:
    """Build the master prompt for final analysis + draft reply."""

    internal_status = case.get("internalCaseStatus", "?")
    is_with_dev = internal_status == "With Development"

    return f"""/no_think
You are a senior Kantata PSA support engineer named Ryan LaBarge. You have access to the full case analysis, parallel cases, Slack team discussions, and your own prior notes. Your task:

1. **VERIFY** — Re-check the original analysis. Does the Slack context or any new info change the root cause assessment? Note any corrections.
2. **UPDATED ANALYSIS** — Provide the definitive root cause, evidence, and next steps. Be specific.
3. **CONFIDENCE** — Rate Low/Medium/High with reasoning.
4. **DRAFT REPLY** — Write a suggested customer response in Ryan's voice (see tone pack below). If this case is "With Development", the reply should be a status update. If "Awaiting Customer" or "Response Required", address their last message. If "Review" or "New", provide initial findings.

## Case #{case.get('caseNumber')} — {case.get('subject')}
**Account:** {case.get('account', '?')} | **Priority:** {case.get('priority')}
**Internal Status:** {internal_status} | **SLA:** {case.get('slaStatus', '?')}
**Status:** {case.get('status')} | **Type:** {case.get('type', '?')}

### Case Summary
{(case.get('caseSummary') or 'N/A')[:1500]}

### Description
{(case.get('description') or 'N/A')[:1000]}

### Original AI Analysis
{analysis[:2000] if analysis else 'N/A'}

### Parallel Cases & JIRA Linkage
{parallels[:1500] if parallels else 'N/A'}

### Ryan's Own Notes (RAL)
{ral_notes if ral_notes else 'None for this case'}

### Slack Team Discussions
{slack_context[:2000] if slack_context else 'No Slack mentions found'}

### Tone Pack (Ryan LaBarge's Voice)
{tone_pack[:1500]}

IMPORTANT: You MUST complete ALL FOUR sections. The draft reply should be ready to paste into Salesforce — written as Ryan, in his voice, addressed to the customer by first name if visible in the case data."""


def main():
    cases = json.loads(CASES_F.read_text())
    analysis_md = ANALYSIS_F.read_text() if ANALYSIS_F.exists() else ""
    rerun_md = RERUN_F.read_text() if RERUN_F.exists() else ""
    parallels_md = PARALLELS_F.read_text() if PARALLELS_F.exists() else ""
    tone_pack = TONE_F.read_text() if TONE_F.exists() else ""

    ral_data = {}
    if RAL_F.exists():
        ral_data = json.loads(RAL_F.read_text())

    print(f"Loaded {len(cases)} cases")
    print(f"Analysis: {len(analysis_md)} chars")
    print(f"Rerun: {len(rerun_md)} chars")
    print(f"Parallels: {len(parallels_md)} chars")
    print(f"RAL notes: {len(ral_data.get('ral_notes', []))} notes")
    print(f"Tone pack: {len(tone_pack)} chars")
    print(f"Slack dir: {RAG_SLACK} (exists={RAG_SLACK.exists()})")

    # Count slack files
    slack_files = list(RAG_SLACK.rglob("*.md")) if RAG_SLACK.exists() else []
    print(f"Slack files: {len(slack_files)}")
    print()

    output_lines = [
        "# Ryan's Open Cases — FINAL Analysis + Draft Replies",
        f"**Generated:** {time.strftime('%Y-%m-%d %H:%M')}",
        f"**Model:** {MODEL} (local Ollama, num_predict=3000)",
        f"**Cases:** {len(cases)}",
        f"**Data sources:** SF Cases (2778) + JIRA (9147) + Confluence (2002) + Slack ({len(slack_files)} files) + RAL notes ({len(ral_data.get('ral_notes', []))})",
        "",
        "---",
        "",
    ]

    for i, case in enumerate(cases, 1):
        num = case.get("caseNumber", "?")
        subject = case.get("subject", "?")
        internal = case.get("internalCaseStatus", "?")
        print(f"[{i}/{len(cases)}] {num} [{internal}] — {subject[:50]}...", end=" ", flush=True)

        # Get the best analysis (rerun if it was truncated, otherwise original)
        if num in RERUN_CASES and rerun_md:
            analysis = extract_section(rerun_md, num)
        else:
            analysis = extract_section(analysis_md, num)

        parallels = extract_section(parallels_md, num)

        # Search Slack for case mentions
        slack_context = find_slack_mentions(num, RAG_SLACK)
        if not slack_context:
            slack_context = find_slack_keyword_mentions(
                subject, case.get("account", ""), RAG_SLACK
            )

        ral_notes = get_ral_notes(num, ral_data)

        prompt = build_final_prompt(case, analysis, parallels, slack_context, ral_notes, tone_pack)

        t0 = time.time()
        try:
            result = call_ollama(prompt)
            elapsed = time.time() - t0
            slack_tag = "+" if slack_context else "-"
            ral_tag = "+" if ral_notes else "-"
            print(f"done ({elapsed:.0f}s) [slack:{slack_tag} ral:{ral_tag}]")
        except Exception as e:
            result = f"**Failed:** {e}"
            print(f"FAILED: {e}")

        output_lines.extend([
            f"## Case {num}: {subject}",
            f"**Account:** {case.get('account', '?')} | **Priority:** {case.get('priority')} | **Internal Status:** {internal} | **SLA:** {case.get('slaStatus', '?')}",
            "",
            result.strip(),
            "",
            "---",
            "",
        ])

    OUTPUT_F.write_text("\n".join(output_lines), encoding="utf-8")
    print(f"\nFinal report written to {OUTPUT_F}")
    print(f"Size: {OUTPUT_F.stat().st_size / 1024:.0f} KB")


if __name__ == "__main__":
    main()
