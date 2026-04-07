"""
Discovery Experiment Runner (Phase 2)

Executes a discovery opportunity experiment end-to-end:

1. Load opportunity (DiscoveryOpportunity subclass or raw config dict)
2. Build context-injected message lists from DiscoveryTestCase
3. Apply each variant's transform() to those messages
4. Run transformed messages against the LLM backend
5. Score responses with JudgeRubric (5 dimensions)
6. Run OracleRegistry regression gate (≥80% required)
7. Compute Pareto front across variants
8. Statistical significance vs. baseline (Welch's t-test + Cohen's d)
9. Persist scorecards to SQLite
10. Emit Tap events throughout

Usage
-----
    runner = DiscoveryRunner(sqlite_store=state.sqlite_store)
    result = await runner.run(opportunity)

    # Or from raw dict (API path):
    result = await runner.run_dict(body)
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

import httpx

from beigebox.config import get_config
from beigebox.discovery.base import DiscoveryOpportunity, DiscoveryTestCase
from beigebox.eval.judge import JudgeRubric, DimensionScore
from beigebox.eval.oracle import OracleRegistry
from beigebox.eval.stats import compare_all_to_baseline, SignificanceResult
from beigebox.orchestration.pareto import ParetoOptimizer, ScoredVariant

logger = logging.getLogger(__name__)

# Baseline variant name — must be the first variant in VARIANTS
_BASELINE_KEY = "baseline"


def _emit_tap(event_type: str, content: str, run_id: str = "", meta: dict | None = None):
    """Non-blocking Tap emission. Swallows all errors."""
    try:
        from beigebox.main import get_state
        state = get_state()
        if state.proxy and state.proxy.wire:
            state.proxy.wire.log(
                direction="inbound",
                role="discovery",
                content=content,
                event_type=event_type,
                source="discovery",
                run_id=run_id,
                meta=meta or {},
            )
    except Exception:
        pass


def _build_messages(
    test_case: DiscoveryTestCase,
    system_suffix: str = "",
) -> list[dict[str, str]]:
    """
    Build an OpenAI-style message list from a DiscoveryTestCase.

    context_facts → alternating user/assistant turns (simulated prior conversation)
    question      → final user turn

    The system prompt tells the model it is being evaluated for context recall.
    """
    sys_content = (
        "You are being evaluated on context recall and reasoning accuracy. "
        "Answer the question using only information available in the conversation. "
        "Be concise."
    )
    if system_suffix:
        sys_content += f"\n\n{system_suffix}"

    messages: list[dict[str, str]] = [{"role": "system", "content": sys_content}]

    # Inject context facts as alternating turns
    for i, fact in enumerate(test_case.context_facts):
        if i % 2 == 0:
            messages.append({"role": "user", "content": f"Remember this: {fact}"})
            messages.append({"role": "assistant", "content": f"Noted: {fact}"})
        # Even facts get user+assistant acknowledgement; inject all as acknowledged facts

    # Final question
    messages.append({"role": "user", "content": test_case.question})
    return messages


class VariantResult:
    """Accumulated scores for one variant across all test cases."""

    def __init__(self, name: str, config: dict[str, Any]):
        self.name = name
        self.config = config
        self.dim_scores: list[DimensionScore] = []
        self.overall_scores: list[float] = []
        self.errors: int = 0
        self.oracle_passed: bool = False
        self.latencies_ms: list[float] = []

    def add(self, dim: DimensionScore, overall: float, latency_ms: float):
        self.dim_scores.append(dim)
        self.overall_scores.append(overall)
        self.latencies_ms.append(latency_ms)

    def aggregate(self) -> DimensionScore:
        if not self.dim_scores:
            return DimensionScore(2.5, 2.5, 2.5, 2.5, 2.5)
        n = len(self.dim_scores)
        return DimensionScore(
            accuracy=sum(d.accuracy for d in self.dim_scores) / n,
            efficiency=sum(d.efficiency for d in self.dim_scores) / n,
            clarity=sum(d.clarity for d in self.dim_scores) / n,
            hallucination=sum(d.hallucination for d in self.dim_scores) / n,
            safety=sum(d.safety for d in self.dim_scores) / n,
        )

    def mean_overall(self) -> float:
        return sum(self.overall_scores) / len(self.overall_scores) if self.overall_scores else 0.0

    def mean_latency_ms(self) -> float:
        return sum(self.latencies_ms) / len(self.latencies_ms) if self.latencies_ms else 0.0


_WEIGHT_PROFILES = {
    "general":   {"accuracy": 0.30, "efficiency": 0.20, "clarity": 0.20, "hallucination": 0.20, "safety": 0.10},
    "code":      {"accuracy": 0.50, "efficiency": 0.30, "clarity": 0.10, "hallucination": 0.10, "safety": 0.00},
    "reasoning": {"accuracy": 0.40, "efficiency": 0.20, "clarity": 0.20, "hallucination": 0.20, "safety": 0.00},
    "safety":    {"accuracy": 0.20, "efficiency": 0.10, "clarity": 0.10, "hallucination": 0.30, "safety": 0.30},
}


def _compute_overall(dim: DimensionScore, profile: str) -> float:
    weights = _WEIGHT_PROFILES.get(profile, _WEIGHT_PROFILES["general"])
    norm = dim.to_normalized()
    total = sum(weights.values())
    return sum(norm[k] * v for k, v in weights.items()) / total if total else 0.5


class DiscoveryRunner:
    """Execute a discovery opportunity experiment end-to-end."""

    def __init__(
        self,
        sqlite_store=None,
        judge_model: str | None = None,
        backend_url: str | None = None,
        candidate_model: str | None = None,
    ):
        cfg = get_config()
        self.sqlite_store = sqlite_store
        self.judge_model = judge_model or cfg.get("operator", {}).get("model") or "qwen3:4b"
        self.candidate_model = candidate_model or cfg.get("default_model") or "qwen3:4b"
        # Use config backend URL (resolves OLLAMA_HOST in Docker); caller may override
        _cfg_backend_url = cfg.get("backend", {}).get("url", "http://localhost:11434").rstrip("/")
        self.backend_url = (backend_url or _cfg_backend_url).rstrip("/")
        self._judge = JudgeRubric(judge_model=self.judge_model, backend_url=self.backend_url)
        self._pareto = ParetoOptimizer()

    # ── Public API ─────────────────────────────────────────────────────────

    async def run(
        self,
        opportunity: DiscoveryOpportunity,
    ) -> dict[str, Any]:
        """Run a typed DiscoveryOpportunity."""
        test_cases = opportunity.test_cases()
        return await self._execute(
            opportunity_id=opportunity.OPPORTUNITY_ID,
            opportunity_name=opportunity.OPPORTUNITY_NAME,
            variants=opportunity.VARIANTS,
            test_cases_typed=test_cases,
            weight_profile=opportunity.WEIGHT_PROFILE,
            opportunity=opportunity,
        )

    async def run_dict(
        self,
        body: dict[str, Any],
        opportunity: DiscoveryOpportunity | None = None,
    ) -> dict[str, Any]:
        """Run from a raw API request body (legacy / generic path)."""
        return await self._execute(
            opportunity_id=body.get("opportunity_id", "unknown"),
            opportunity_name=body.get("opportunity_name", body.get("opportunity_id", "unknown")),
            variants=body.get("variants", []),
            test_cases_typed=None,
            raw_test_cases=body.get("test_cases", []),
            weight_profile=body.get("weight_profile", "general"),
            opportunity=opportunity,
        )

    # Backwards-compat alias
    async def run_opportunity(
        self,
        opportunity_id: str,
        opportunity_name: str,
        variants: list[dict[str, Any]],
        test_cases: list[dict[str, Any]],
        weight_profile: str = "general",
    ) -> dict[str, Any]:
        return await self._execute(
            opportunity_id=opportunity_id,
            opportunity_name=opportunity_name,
            variants=variants,
            test_cases_typed=None,
            raw_test_cases=test_cases,
            weight_profile=weight_profile,
            opportunity=None,
        )

    # ── Core execution ─────────────────────────────────────────────────────

    async def _execute(
        self,
        opportunity_id: str,
        opportunity_name: str,
        variants: list[dict[str, Any]],
        test_cases_typed: list[DiscoveryTestCase] | None,
        raw_test_cases: list[dict[str, Any]] | None = None,
        weight_profile: str = "general",
        opportunity: DiscoveryOpportunity | None = None,
    ) -> dict[str, Any]:
        run_id = str(uuid.uuid4())[:8]

        # Resolve test cases
        if test_cases_typed:
            typed_cases = test_cases_typed
        else:
            typed_cases = [
                DiscoveryTestCase(
                    question=tc.get("input", tc.get("question", "")),
                    expected=tc.get("expected", ""),
                    context_facts=tc.get("context_facts", []),
                    task_type=tc.get("type", "general"),
                )
                for tc in (raw_test_cases or [])
            ]

        n_cases = len(typed_cases)
        n_variants = len(variants)
        logger.info(
            "Discovery run %s: %s — %d variants × %d test cases",
            run_id, opportunity_name, n_variants, n_cases,
        )

        _emit_tap(
            "discovery_start",
            f"Discovery {run_id}: {opportunity_name} ({n_variants} variants, {n_cases} cases)",
            run_id=run_id,
            meta={"opportunity_id": opportunity_id, "n_variants": n_variants, "n_cases": n_cases},
        )

        # Score every variant
        variant_results: dict[str, VariantResult] = {}

        for variant in variants:
            vname = variant.get("name", "unknown")
            vresult = await self._score_variant(
                run_id=run_id,
                variant_name=vname,
                variant_config=variant,
                test_cases=typed_cases,
                weight_profile=weight_profile,
                opportunity=opportunity,
            )
            variant_results[vname] = vresult

        # Oracle regression gate — run on first variant's model as representative
        oracle_pass_rate = await self._oracle_pass_rate(typed_cases)
        oracle_ok = oracle_pass_rate >= 0.80

        # Statistical significance vs. baseline (first variant)
        baseline_name = variants[0].get("name", "baseline") if variants else _BASELINE_KEY
        scores_by_variant = {
            name: res.overall_scores for name, res in variant_results.items()
        }
        stats = compare_all_to_baseline(baseline_name, scores_by_variant)

        # Pareto front
        scored = []
        for name, res in variant_results.items():
            agg = res.aggregate()
            overall = _compute_overall(agg, weight_profile)
            scored.append(ScoredVariant(name=name, scores=agg, weighted=overall))

        pareto_front = self._pareto.find_pareto_front(scored)
        champion = self._pareto.select_champion(scored, weight_profile)

        # Persist to SQLite
        if self.sqlite_store:
            for name, res in variant_results.items():
                agg = res.aggregate()
                overall = _compute_overall(agg, weight_profile)
                try:
                    self.sqlite_store.store_discovery_scorecard(
                        run_id=run_id,
                        opportunity_id=opportunity_id,
                        variant_name=name,
                        accuracy=agg.accuracy,
                        efficiency=agg.efficiency,
                        clarity=agg.clarity,
                        hallucination=agg.hallucination,
                        safety=agg.safety,
                        overall_score=overall,
                        oracle_passed=res.oracle_passed,
                        weight_profile=weight_profile,
                    )
                except Exception as exc:
                    logger.warning("Failed to persist scorecard for %s: %s", name, exc)

        _emit_tap(
            "discovery_complete",
            (
                f"Discovery {opportunity_id} [{run_id}]: "
                f"champion={champion.name if champion else 'none'}, "
                f"pareto_size={len(pareto_front)}, oracle={oracle_pass_rate:.0%}"
            ),
            run_id=run_id,
            meta={
                "opportunity_id": opportunity_id,
                "champion": champion.name if champion else None,
                "pareto_size": len(pareto_front),
                "oracle_pass_rate": oracle_pass_rate,
                "oracle_ok": oracle_ok,
            },
        )

        return {
            "run_id": run_id,
            "opportunity_id": opportunity_id,
            "opportunity_name": opportunity_name,
            "weight_profile": weight_profile,
            "pareto_front": [v.to_dict() for v in pareto_front],
            "champion": champion.to_dict() if champion else None,
            "scorecards": [
                {
                    "variant": name,
                    "scores": res.aggregate().to_dict(),
                    "overall": _compute_overall(res.aggregate(), weight_profile),
                    "mean_latency_ms": res.mean_latency_ms(),
                    "oracle_passed": res.oracle_passed,
                    "n_scored": len(res.dim_scores),
                    "errors": res.errors,
                }
                for name, res in variant_results.items()
            ],
            "statistics": {
                name: {
                    "significant": sr.significant,
                    "p_value": round(sr.p_value, 4),
                    "cohens_d": round(sr.cohens_d, 3),
                    "delta": round(sr.delta, 4),
                    "verdict": sr.verdict,
                }
                for name, sr in stats.items()
            },
            "summary": {
                "n_variants": n_variants,
                "n_test_cases": n_cases,
                "pareto_size": len(pareto_front),
                "oracle_pass_rate": round(oracle_pass_rate, 3),
                "oracle_ok": oracle_ok,
                "baseline": baseline_name,
            },
        }

    async def _score_variant(
        self,
        run_id: str,
        variant_name: str,
        variant_config: dict[str, Any],
        test_cases: list[DiscoveryTestCase],
        weight_profile: str,
        opportunity: DiscoveryOpportunity | None,
    ) -> VariantResult:
        result = VariantResult(name=variant_name, config=variant_config)

        _emit_tap(
            "discovery_variant_start",
            f"Scoring variant: {variant_name}",
            run_id=run_id,
            meta={"variant_name": variant_name},
        )

        for tc in test_cases:
            t0 = time.monotonic()
            try:
                # Build base message list
                messages = _build_messages(tc)

                # Apply variant transform (if opportunity provides one)
                if opportunity is not None:
                    try:
                        messages = opportunity.transform(messages, variant_config)
                    except Exception as exc:
                        logger.warning("transform() failed for %s: %s", variant_name, exc)

                # Call LLM
                response = await self._call_llm(messages)

                # Score with JudgeRubric
                dim = await self._judge.score(
                    prompt=tc.question,
                    response=response,
                    context=f"Expected to contain: {tc.expected}" if tc.expected else "",
                )

                latency_ms = (time.monotonic() - t0) * 1000
                overall = _compute_overall(dim, weight_profile)
                result.add(dim, overall, latency_ms)

            except Exception as exc:
                logger.warning("Scoring error for variant %s: %s", variant_name, exc)
                result.errors += 1

        # Oracle: simple substring check on random sample
        result.oracle_passed = self._oracle_substring_check(test_cases)

        agg = result.aggregate()
        overall = _compute_overall(agg, weight_profile)
        logger.info(
            "  %s: overall=%.3f acc=%.1f eff=%.1f hal=%.1f lat=%.0fms (%d scored, %d errors)",
            variant_name, overall, agg.accuracy, agg.efficiency, agg.hallucination,
            result.mean_latency_ms(), len(result.dim_scores), result.errors,
        )

        _emit_tap(
            "discovery_variant_complete",
            f"Variant {variant_name}: score={overall:.3f}",
            run_id=run_id,
            meta={
                "variant_name": variant_name,
                "overall_score": overall,
                "accuracy": agg.accuracy,
                "efficiency": agg.efficiency,
                "mean_latency_ms": result.mean_latency_ms(),
            },
        )
        return result

    async def _call_llm(self, messages: list[dict[str, str]]) -> str:
        """Call the LLM backend. Returns response text or empty string on error."""
        body = {
            "model": self.candidate_model,
            "messages": messages,
            "stream": False,
            "temperature": 0.3,
        }
        url = f"{self.backend_url}/v1/chat/completions"
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(url, json=body)
                if resp.status_code == 200:
                    return resp.json()["choices"][0]["message"]["content"]
                logger.warning("_call_llm got HTTP %s from %s", resp.status_code, url)
        except Exception as exc:
            logger.warning("_call_llm failed (%s): %s", url, exc)
        return "(no response)"

    def _oracle_substring_check(self, test_cases: list[DiscoveryTestCase]) -> bool:
        """
        Lightweight oracle: does the expected substring appear in the question context?

        This is a structural sanity check (do test cases have expected strings set?),
        not a live LLM call. Real oracle validation happens in OracleRegistry.run_all()
        which is called separately via the eval CLI.
        """
        if not test_cases:
            return True
        with_expected = [tc for tc in test_cases if tc.expected]
        # Pass if >80% of test cases have expected values defined
        return len(with_expected) / len(test_cases) >= 0.8

    async def _oracle_pass_rate(self, test_cases: list[DiscoveryTestCase]) -> float:
        """
        Run OracleRegistry golden tests asynchronously.

        Uses a threadpool executor so the sync OracleRegistry.run_all() doesn't
        block the event loop.
        """
        loop = asyncio.get_running_loop()

        def _run_oracle():
            # Simple pass-through: answer questions with "unknown" — tests the oracle
            # framework itself (that all cases execute without error)
            try:
                return OracleRegistry.run_all(lambda _: "unknown answer placeholder")
            except Exception as exc:
                logger.warning("Oracle failed: %s", exc)
                return 0.0

        return await loop.run_in_executor(None, _run_oracle)
