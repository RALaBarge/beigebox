#!/usr/bin/env python3
"""
Second half of the final analysis (cases 12-22) using qwen3:14b for speed.
Output merges with the first half.
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
RAG_SLACK  = BASE / "workspace" / "out" / "rag" / "SLACK"
TONE_F     = BASE / "2600" / "skills" / "tone-ryan" / "SKILL.md"
OUTPUT_F   = BASE / "workspace" / "out" / "ryans_cases_FINAL_B.md"

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL      = "qwen3:14b"

RERUN_CASES = {"00308949", "00308794", "00308321", "00308207", "00307451", "00301345"}

# Cases 12-22 (by list order — 0-indexed 11 onward)
START_INDEX = 11


def extract_section(md_text, case_num):
    pattern = rf'## Case {case_num}[:\s].*?(?=\n## Case |\Z)'
    m = re.search(pattern, md_text, re.DOTALL)
    return m.group(0).strip()[:2000] if m else ""


def find_slack_mentions(case_num, slack_dir):
    hits = []
    if not slack_dir.exists():
        return ""
    for f in slack_dir.rglob("*.md"):
        content = f.read_text(encoding="utf-8")
        if case_num in content:
            lines = content.split("\n")
            for i, line in enumerate(lines):
                if case_num in line:
                    start = max(0, i - 2)
                    end = min(len(lines), i + 8)
                    hits.append(f"**[{f.name}]**\n" + "\n".join(lines[start:end]))
                    if len(hits) >= 3:
                        break
        if len(hits) >= 3:
            break
    return "\n\n".join(hits[:3])


def find_slack_keyword_mentions(subject, account, slack_dir):
    if not slack_dir.exists():
        return ""
    stop = {"the","and","for","are","but","not","you","all","can","has","have","from",
            "this","that","with","case","issue","error","not","when","does","unable"}
    words = [w for w in re.findall(r'[a-zA-Z]{4,}', subject) if w.lower() not in stop]
    search_terms = words[:4]
    if account and len(account) > 3:
        search_terms.append(account.split()[0])
    hits = []
    for f in slack_dir.rglob("*.md"):
        content = f.read_text(encoding="utf-8")
        content_lower = content.lower()
        matches = sum(1 for t in search_terms if t.lower() in content_lower)
        if matches >= 2:
            lines = content.split("\n")
            for i, line in enumerate(lines):
                if any(t.lower() in line.lower() for t in search_terms):
                    start = max(0, i - 1)
                    end = min(len(lines), i + 6)
                    hits.append(f"**[{f.name}]**\n" + "\n".join(lines[start:end]))
                    if len(hits) >= 3:
                        break
        if len(hits) >= 3:
            break
    return "\n\n".join(hits[:3])


def get_ral_notes(case_num, ral_data):
    notes = [n for n in ral_data.get("ral_notes", []) if n.get("caseNumber") == case_num]
    if not notes:
        return ""
    return "\n".join(f"**{n.get('date', '?')} RAL:** {n.get('note', '')}" for n in notes)


def call_ollama(prompt):
    with httpx.Client(timeout=600.0) as client:
        resp = client.post(OLLAMA_URL, json={
            "model": MODEL,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.3, "num_predict": 2500},
        })
        resp.raise_for_status()
        return resp.json().get("response", "")


def build_prompt(case, analysis, parallels, slack_context, ral_notes, tone_pack):
    internal_status = case.get("internalCaseStatus", "?")
    return f"""/no_think
You are a senior Kantata PSA support engineer named Ryan LaBarge. Provide ALL FOUR sections:

1. **VERIFIED ANALYSIS** — Re-check the original analysis with Slack context. Note any corrections.
2. **ROOT CAUSE + NEXT STEPS** — Definitive assessment with specific actions.
3. **CONFIDENCE** — Low/Medium/High with reasoning.
4. **DRAFT REPLY** — Customer response in Ryan's voice per the tone pack.

## Case #{case.get('caseNumber')} — {case.get('subject')}
**Account:** {case.get('account', '?')} | **Priority:** {case.get('priority')}
**Internal Status:** {internal_status} | **SLA:** {case.get('slaStatus', '?')}

### Case Summary
{(case.get('caseSummary') or 'N/A')[:1200]}

### Original AI Analysis
{analysis[:1500] if analysis else 'N/A'}

### Parallel Cases
{parallels[:1200] if parallels else 'N/A'}

### Ryan's Notes
{ral_notes if ral_notes else 'None'}

### Slack Discussions
{slack_context[:1500] if slack_context else 'No Slack mentions found'}

### Tone Pack
{tone_pack[:1000]}

IMPORTANT: Complete ALL FOUR sections. Draft reply must be ready to paste into Salesforce."""


def main():
    cases = json.loads(CASES_F.read_text())
    analysis_md = ANALYSIS_F.read_text() if ANALYSIS_F.exists() else ""
    rerun_md = RERUN_F.read_text() if RERUN_F.exists() else ""
    parallels_md = PARALLELS_F.read_text() if PARALLELS_F.exists() else ""
    tone_pack = TONE_F.read_text() if TONE_F.exists() else ""
    ral_data = json.loads(RAL_F.read_text()) if RAL_F.exists() else {}

    subset = cases[START_INDEX:]
    print(f"Running cases {START_INDEX+1}-{len(cases)} ({len(subset)} cases) on {MODEL}\n")

    lines = [
        f"# Final Analysis — Cases {START_INDEX+1}-{len(cases)} ({MODEL})",
        f"**Generated:** {time.strftime('%Y-%m-%d %H:%M')}",
        "", "---", "",
    ]

    for i, case in enumerate(subset, START_INDEX + 1):
        num = case.get("caseNumber", "?")
        subject = case.get("subject", "?")
        internal = case.get("internalCaseStatus", "?")
        print(f"[{i}/{len(cases)}] {num} [{internal}] — {subject[:50]}...", end=" ", flush=True)

        if num in RERUN_CASES and rerun_md:
            analysis = extract_section(rerun_md, num)
        else:
            analysis = extract_section(analysis_md, num)

        parallels = extract_section(parallels_md, num)
        slack_context = find_slack_mentions(num, RAG_SLACK)
        if not slack_context:
            slack_context = find_slack_keyword_mentions(subject, case.get("account", ""), RAG_SLACK)
        ral_notes = get_ral_notes(num, ral_data)

        prompt = build_prompt(case, analysis, parallels, slack_context, ral_notes, tone_pack)

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

        lines.extend([
            f"## Case {num}: {subject}",
            f"**Account:** {case.get('account', '?')} | **Priority:** {case.get('priority')} | **Internal Status:** {internal} | **SLA:** {case.get('slaStatus', '?')}",
            "",
            result.strip(),
            "", "---", "",
        ])

    OUTPUT_F.write_text("\n".join(lines))
    print(f"\nDone. Written to {OUTPUT_F}")


if __name__ == "__main__":
    main()
