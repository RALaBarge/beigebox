"""End-to-end fuzz pipeline. Importable from a Trinity run or any other orchestrator."""

from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path
from typing import Any, Callable

from .engine import (
    AdaptiveTimeAllocator,
    CrashClassifier,
    Fuzzer,
    RiskScorer,
    SeedCorpusExtractor,
    SmartHarnessGenerator,
)
from .extractor import FunctionExtractor


# Map fuzzer crash types onto garlicpress.Finding (severity, type) so the output
# can be merged with static-analysis findings without translation.
_CRASH_MAP: dict[str, tuple[str, str]] = {
    "RecursionError": ("high", "resource_leak"),
    "MemoryError": ("high", "resource_leak"),
    "Timeout": ("high", "resource_leak"),
    "AssertionError": ("medium", "logic_error"),
    "SegmentationFault": ("critical", "security"),
    "HarnessCrash": ("low", "other"),
}


async def run_fuzzing(
    repo_path: str | Path,
    *,
    max_functions: int = 25,
    total_budget_seconds: int = 120,
    max_crashes_per_func: int = 5,
    concurrency: int = 2,
    logger: Callable | None = None,
) -> dict[str, Any]:
    """Discover fuzzable Python functions in `repo_path`, fuzz the top-risk ones, classify crashes.

    Returns:
        {
          "findings": [garlicpress-shape Finding dicts],
          "stats": {functions_discovered, functions_fuzzed, total_iterations,
                    total_duration_seconds, crashes_raw, crashes_classified},
          "raw_results": [per-function {function, status, crashes, iterations, ...}],
        }
    """
    repo_path = str(Path(repo_path).resolve())

    extractor = FunctionExtractor()
    scorer = RiskScorer()
    seeder = SeedCorpusExtractor()
    harness_gen = SmartHarnessGenerator()
    classifier = CrashClassifier()
    allocator = AdaptiveTimeAllocator()
    fuzzer = Fuzzer(logger=logger)

    discovered = extractor.find_fuzzable_functions_in_repo(repo_path)
    if logger:
        logger(f"discovered {len(discovered)} fuzzable functions")

    if not discovered:
        return {
            "findings": [],
            "stats": _empty_stats(),
            "raw_results": [],
        }

    scored = scorer.score_functions(discovered)
    # Each fuzzed function needs at least 1s; cap selection so the allocator
    # can honor its <= total_budget contract. Caller's max_functions is the
    # upper bound, not a guarantee.
    effective_max = max(1, min(max_functions, total_budget_seconds))
    selected = scored[:effective_max]
    budget = allocator.allocate_budget(selected, total_budget_seconds=total_budget_seconds)

    sem = asyncio.Semaphore(max(1, concurrency))

    async def _fuzz_one(func: dict[str, Any]) -> dict[str, Any]:
        # Whole-coroutine try/except so an exception in seed/harness setup
        # produces a per-function error result instead of cancelling sibling
        # fuzz tasks via asyncio.gather. Pairs with return_exceptions=True
        # below as a defence in depth.
        try:
            async with sem:
                param = func["parameters"][0] if func["parameters"] else "data"
                ptype = harness_gen.infer_parameter_type(func["source"], param)
                harness_code = harness_gen.generate_basic_harness(
                    function_name=func["name"],
                    source_file=func["file_path"],
                    parameter_type=ptype,
                    max_crashes=max_crashes_per_func,
                )
                seeds = seeder.extract(func["source"], func["name"])
                timeout = budget.get(func["name"], 5)
                result = await fuzzer.fuzz_function(
                    harness_code=harness_code,
                    function_name=func["name"],
                    file_path=func["file_path"],
                    seeds=seeds,
                    timeout_seconds=timeout,
                )
                result["risk_score"] = func["risk_score"]
                result["line_start"] = func["line_start"]
                result["line_end"] = func["line_end"]
                return result
        except Exception as exc:
            return {
                "function": func["name"],
                "file_path": func.get("file_path", ""),
                "status": "error",
                "crashes": [],
                "iterations": 0,
                "duration_seconds": 0.0,
                "error": f"{type(exc).__name__}: {exc}",
                "risk_score": func.get("risk_score", 0),
                "line_start": func.get("line_start", 0),
                "line_end": func.get("line_end", 0),
            }

    raw_gathered = await asyncio.gather(
        *(_fuzz_one(f) for f in selected), return_exceptions=True
    )
    raw_results = [
        r if isinstance(r, dict)
        else {"function": "<unknown>", "status": "error", "crashes": [],
              "iterations": 0, "duration_seconds": 0.0,
              "error": f"{type(r).__name__}: {r}"}
        for r in raw_gathered
    ]

    findings: list[dict[str, Any]] = []
    crashes_raw = 0
    crashes_classified = 0
    for res in raw_results:
        for crash in res.get("crashes", []):
            crashes_raw += 1
            if not classifier.is_app_crash(crash, repo_path):
                continue
            crashes_classified += 1
            findings.append(_to_finding(res, crash))

    stats = {
        "functions_discovered": len(discovered),
        "functions_fuzzed": len(selected),
        "total_iterations": sum(r.get("iterations", 0) for r in raw_results),
        "total_duration_seconds": round(sum(r.get("duration_seconds", 0.0) for r in raw_results), 2),
        "crashes_raw": crashes_raw,
        "crashes_classified": crashes_classified,
    }

    return {"findings": findings, "stats": stats, "raw_results": raw_results}


def _to_finding(res: dict[str, Any], crash: dict[str, Any]) -> dict[str, Any]:
    crash_type = crash.get("type", "Unknown")
    severity, ftype = _CRASH_MAP.get(crash_type, ("medium", "other"))

    file_path = res.get("file_path") or os.path.basename(res.get("function", ""))
    line = res.get("line_start", 0)
    func = res["function"]

    fid_seed = f"fuzz:{file_path}:{func}:{crash_type}:{crash.get('message', '')}"
    finding_id = "fuzz_" + hashlib.sha1(fid_seed.encode()).hexdigest()[:12]

    description = f"Fuzzer found {crash_type} in {func}: {crash.get('message', '').strip() or 'no message'}"
    evidence = (crash.get("stack_trace") or "").strip()
    if len(evidence) > 4000:
        evidence = evidence[:2000] + "\n... (truncated) ...\n" + evidence[-1500:]

    return {
        "finding_id": finding_id,
        "severity": severity,
        "type": ftype,
        "location": f"{file_path}:{line}",
        "description": description,
        "evidence": evidence,
        "traceability": {"file": file_path, "line": line, "git_sha": None},
        "fuzz_meta": {
            "function": func,
            "crash_type": crash_type,
            "reproducer_hex": crash.get("reproducer_hex", ""),
            "iterations": res.get("iterations", 0),
            "duration_seconds": res.get("duration_seconds", 0.0),
        },
    }


def _empty_stats() -> dict[str, Any]:
    return {
        "functions_discovered": 0,
        "functions_fuzzed": 0,
        "total_iterations": 0,
        "total_duration_seconds": 0.0,
        "crashes_raw": 0,
        "crashes_classified": 0,
    }
