#!/usr/bin/env python3
"""
Find parallel cases, shared errors, same-customer history, and linkable
patterns across Ryan's 22 open cases and the full 2,778-case RAG store.
Also ask the LLM for recommended SOQL queries for further investigation.

All inference stays on-box via Ollama.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

import httpx

BASE     = Path(__file__).resolve().parent.parent
CASES_F  = BASE / "workspace" / "out" / "ryans_open_cases.json"
RAG_SF   = BASE / "workspace" / "out" / "rag" / "SF"
RAG_JIRA = BASE / "workspace" / "out" / "rag" / "JIRA"
OUTPUT_F = BASE / "workspace" / "out" / "ryans_cases_parallels.md"

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL      = "qwen3:30b-a3b"


def find_parallel_cases(case: dict, all_sf_files: dict[str, str]) -> list[dict]:
    """Find other SF cases that share keywords, errors, or account with this case."""
    case_num = case.get("caseNumber", "")
    account = (case.get("account") or "").lower()
    subject = (case.get("subject") or "").lower()
    description = (case.get("description") or "").lower()
    summary = (case.get("caseSummary") or "").lower()
    combined = f"{subject} {description} {summary}"

    # Extract error signatures, job names, JIRA keys
    error_patterns = re.findall(r'(?:error|exception|fail|null\s*(?:object|pointer|reference))[^.]{0,80}', combined, re.IGNORECASE)
    job_names = re.findall(r'Job\w+', combined)
    jira_keys = set()
    for j in case.get("jira", []):
        if j.get("number"):
            jira_keys.add(j["number"])
    if case.get("internalJiraKey"):
        jira_keys.add(case["internalJiraKey"])

    # Extract distinctive technical terms (3+ chars, not common)
    stop = {"the","and","for","are","but","not","you","all","can","has","have","from",
            "this","that","with","they","been","will","when","what","case","issue",
            "please","would","should","could","about","after","before","customer",
            "kantata","support","team","update","status","open","closed","time"}
    tech_words = set()
    for w in re.findall(r'[a-z]{4,}', combined):
        if w not in stop:
            tech_words.add(w)

    # Also get feature-area terms
    feature_terms = set()
    for term in ["timesheet", "invoice", "credit note", "expense", "assignment",
                 "engagement", "proposal", "approval", "forecast", "utilization",
                 "performance analysis", "purchase order", "resource period",
                 "delivery element", "activity", "plan", "gantt", "what-if",
                 "intercompany", "exchange rate", "revenue rate", "employee cost",
                 "community portal", "holiday calendar"]:
        if term in combined:
            feature_terms.add(term)

    parallels = []
    for fname, content_preview in all_sf_files.items():
        # Skip self
        if case_num and fname.startswith(case_num):
            continue
        content_lower = content_preview.lower()

        score = 0
        reasons = []

        # Same account
        if account and len(account) > 3 and account in content_lower:
            score += 3
            reasons.append(f"same account ({account})")

        # Same JIRA key
        for jk in jira_keys:
            if jk in content_preview:
                score += 5
                reasons.append(f"shared JIRA {jk}")

        # Same job name
        for jn in job_names:
            if jn in content_preview:
                score += 4
                reasons.append(f"same job {jn}")

        # Feature area overlap
        for ft in feature_terms:
            if ft in content_lower:
                score += 1

        # Error pattern similarity
        for ep in error_patterns[:3]:
            ep_words = set(re.findall(r'[a-z]{4,}', ep.lower())) - stop
            if ep_words:
                overlap = sum(1 for w in ep_words if w in content_lower)
                if overlap >= 2:
                    score += 2
                    reasons.append("similar error pattern")
                    break

        # Technical term overlap
        tech_overlap = sum(1 for w in list(tech_words)[:20] if w in content_lower)
        if tech_overlap >= 5:
            score += tech_overlap // 3

        if score >= 4:
            # Extract case number from filename
            m = re.match(r'(\d{8})', fname)
            parallel_num = m.group(1) if m else fname[:20]
            parallels.append({
                "caseNumber": parallel_num,
                "file": fname,
                "score": score,
                "reasons": reasons[:4],
            })

    parallels.sort(key=lambda x: x["score"], reverse=True)
    return parallels[:8]


def find_parallel_jiras(case: dict, all_jira_files: dict[str, str]) -> list[dict]:
    """Find JIRA tickets that share keywords with this case."""
    subject = (case.get("subject") or "").lower()
    description = (case.get("description") or "").lower()
    summary = (case.get("caseSummary") or "").lower()
    combined = f"{subject} {description} {summary}"

    # Already-linked JIRAs
    linked = set()
    for j in case.get("jira", []):
        if j.get("number"):
            linked.add(j["number"])
    if case.get("internalJiraKey"):
        linked.add(case["internalJiraKey"])

    feature_terms = set()
    for term in ["timesheet", "invoice", "credit", "expense", "assignment",
                 "engagement", "proposal", "approval", "forecast", "utilization",
                 "performance analysis", "purchase order", "resource period",
                 "delivery element", "activity", "plan", "gantt", "what-if",
                 "intercompany", "exchange rate", "revenue rate", "employee cost",
                 "community portal", "holiday calendar", "null object",
                 "de-reference", "zero", "missing"]:
        if term in combined:
            feature_terms.add(term)

    stop = {"the","and","for","are","but","not","you","all","can","has","have","from",
            "this","that","with","they","been","will","when","what","issue","jira",
            "please","would","should","could","about","ticket","bug","feature"}
    tech_words = set(re.findall(r'[a-z]{4,}', combined)) - stop

    matches = []
    for fname, content_preview in all_jira_files.items():
        # Skip already linked
        jira_key = fname.split(" - ")[0].strip() if " - " in fname else fname[:10]
        if jira_key in linked:
            continue

        content_lower = content_preview.lower()
        score = 0
        reasons = []

        for ft in feature_terms:
            if ft in content_lower:
                score += 2
                reasons.append(ft)

        tech_overlap = sum(1 for w in list(tech_words)[:15] if w in content_lower)
        if tech_overlap >= 4:
            score += tech_overlap // 2
            reasons.append(f"{tech_overlap} keyword matches")

        if score >= 5:
            matches.append({
                "jiraKey": jira_key,
                "file": fname,
                "score": score,
                "reasons": reasons[:3],
            })

    matches.sort(key=lambda x: x["score"], reverse=True)
    return matches[:5]


def build_analysis_prompt(case: dict, parallel_cases: list, parallel_jiras: list) -> str:
    """Build prompt for Qwen to analyze parallels and suggest SOQL."""
    parallel_case_text = ""
    if parallel_cases:
        lines = []
        for p in parallel_cases[:5]:
            lines.append(f"- Case {p['caseNumber']} (score={p['score']}): {', '.join(p['reasons'])}")
        parallel_case_text = "\n".join(lines)
    else:
        parallel_case_text = "None found"

    parallel_jira_text = ""
    if parallel_jiras:
        lines = []
        for p in parallel_jiras[:5]:
            lines.append(f"- {p['jiraKey']} (score={p['score']}): {', '.join(p['reasons'])}")
        parallel_jira_text = "\n".join(lines)
    else:
        parallel_jira_text = "None found"

    existing_jiras = ""
    if case.get("jira"):
        existing_jiras = "\n".join(f"- {j.get('number','?')}: {j.get('name','')}" for j in case["jira"])

    return f"""/no_think
You are a senior Kantata PSA support engineer with deep Salesforce expertise. Analyze this case's parallel cases and JIRA connections, then suggest SOQL queries for further investigation.

## Case #{case.get('caseNumber')} — {case.get('subject')}
**Account:** {case.get('account', '?')} | **Priority:** {case.get('priority')}
**Internal Status:** {case.get('internalCaseStatus', '?')} | **SLA:** {case.get('slaStatus', '?')}

### Description
{(case.get('description') or 'N/A')[:1000]}

### Case Summary
{(case.get('caseSummary') or 'N/A')[:800]}

### Already-Linked JIRAs
{existing_jiras or 'None'}

### Parallel Cases Found (from 2,778 case RAG store)
{parallel_case_text}

### Potentially Related JIRAs (not yet linked)
{parallel_jira_text}

## Your Tasks:
1. **Parallel Analysis**: Are any of the parallel cases likely the SAME root cause? Grade each: Same Issue / Related / Coincidental
2. **JIRA Connections**: Should any of the unlinked JIRAs be linked to this case? Why?
3. **Pattern Detection**: Is this part of a broader pattern (e.g., affects multiple accounts, recurring bug, config issue)?
4. **SOQL Queries**: Suggest 2-3 SOQL queries that would help investigate this case further. Use the KimbleOne__ namespace prefix for managed-package objects/fields. Focus on queries that would reveal:
   - Data state that confirms/denies the suspected root cause
   - Scope of impact (how many records affected)
   - Related records that might provide evidence

Format SOQL as code blocks. Be specific about field names (use KimbleOne__DeliveryElement__c, KimbleOne__ActivityAssignment__c, etc.)."""


def call_ollama(prompt: str, timeout: float = 300.0) -> str:
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(OLLAMA_URL, json={
            "model": MODEL,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.3, "num_predict": 1200},
        })
        resp.raise_for_status()
        return resp.json().get("response", "")


def main():
    cases = json.loads(CASES_F.read_text(encoding="utf-8"))
    print(f"Loaded {len(cases)} open cases.")

    # Build indexes of all SF and JIRA files (first 1000 chars for matching)
    print("Building SF case index...", end=" ", flush=True)
    sf_index: dict[str, str] = {}
    for f in RAG_SF.glob("*.md"):
        sf_index[f.name] = f.read_text(encoding="utf-8")[:1000]
    print(f"{len(sf_index)} cases")

    print("Building JIRA index...", end=" ", flush=True)
    jira_index: dict[str, str] = {}
    for f in RAG_JIRA.glob("*.md"):
        jira_index[f.name] = f.read_text(encoding="utf-8")[:1000]
    print(f"{len(jira_index)} tickets")

    output_lines = [
        "# Ryan's Open Cases — Parallel Analysis & SOQL Recommendations",
        f"**Generated:** {time.strftime('%Y-%m-%d %H:%M')}",
        f"**Model:** {MODEL} (local Ollama)",
        f"**Open Cases:** {len(cases)} | **SF RAG:** {len(sf_index)} | **JIRA RAG:** {len(jira_index)}",
        "",
        "---",
        "",
    ]

    for i, case in enumerate(cases, 1):
        case_num = case.get("caseNumber", "?")
        subject = case.get("subject", "?")
        internal_status = case.get("internalCaseStatus", "?")
        print(f"[{i}/{len(cases)}] {case_num} [{internal_status}] — {subject[:55]}...", end=" ", flush=True)

        # Find parallels
        parallel_cases = find_parallel_cases(case, sf_index)
        parallel_jiras = find_parallel_jiras(case, jira_index)

        prompt = build_analysis_prompt(case, parallel_cases, parallel_jiras)

        t0 = time.time()
        try:
            analysis = call_ollama(prompt)
            elapsed = time.time() - t0
            print(f"done ({elapsed:.0f}s, {len(parallel_cases)}p/{len(parallel_jiras)}j)")
        except Exception as e:
            elapsed = time.time() - t0
            analysis = f"**Analysis failed:** {e}"
            print(f"FAILED ({elapsed:.0f}s): {e}")

        output_lines.extend([
            f"## Case {case_num}: {subject}",
            f"**Account:** {case.get('account', '?')} | **Priority:** {case.get('priority')} | **Internal Status:** {internal_status} | **SLA:** {case.get('slaStatus', '?')}",
            f"**Parallel cases:** {len(parallel_cases)} | **Potentially related JIRAs:** {len(parallel_jiras)}",
            "",
            analysis.strip(),
            "",
            "---",
            "",
        ])

    OUTPUT_F.write_text("\n".join(output_lines), encoding="utf-8")
    print(f"\nDone. Written to {OUTPUT_F}")


if __name__ == "__main__":
    main()
