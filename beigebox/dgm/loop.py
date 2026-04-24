"""
DGM Loop — the main Darwin Gödel Machine self-improvement loop.

The loop:
  1. Sample N real requests from wiretap history as evaluation probes
  2. Ask proposer to suggest a config change (informed by active rubric + history)
  3. Apply the change via ConfigPatcher
  4. Re-run the same probes with the new config
  5. Ask judge to compare before vs after (pairwise, rotating rubric)
  6. KEEP if judge says B wins with confidence > threshold, else REVERT
  7. Tick the rubric rotator (may rotate to next rubric)
  8. Emit Tap events throughout
  9. Repeat for n_iterations

Goodhart's Law mitigation:
  The rubric rotates every rotation_interval iterations (default: 5). Because
  the system doesn't know which rubric comes next, it can't specialise toward
  any single metric — it must improve across all rubrics simultaneously.

Convergence:
  If no improvement is found for patience consecutive iterations, the loop
  stops early. This prevents wasted compute on a plateau.

Usage (from CLI or API):
    loop = DGMLoop.from_config()
    result = await loop.run(n_iterations=20)
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx

from beigebox.config import get_config
from beigebox.dgm.judge import DGMJudge
from beigebox.dgm.patcher import ConfigPatcher, Patch
from beigebox.dgm.proposer import DGMProposer
from beigebox.dgm.rubrics import RubricRotator

logger = logging.getLogger(__name__)


# ── Tap helper ────────────────────────────────────────────────────────────

def _tap(event_type: str, content: str, run_id: str = "", meta: dict | None = None) -> None:
    """Emit a structured event to the Tap wire log. Swallows all errors."""
    try:
        from beigebox.main import get_state
        state = get_state()
        if state.proxy and state.proxy.wire:
            state.proxy.wire.log(
                direction="inbound",
                role="dgm",
                content=content,
                event_type=event_type,
                source="dgm",
                run_id=run_id,
                meta=meta or {},
            )
    except Exception:
        pass


# ── Data types ─────────────────────────────────────────────────────────────

@dataclass
class Probe:
    """A single request/response pair sampled from wiretap history."""
    request: str        # The user message text
    response: str       # The original response (used as baseline "A")


@dataclass
class IterationResult:
    """Result of one DGM iteration."""
    iteration: int
    key: str
    old_value: Any
    new_value: Any
    reasoning: str
    winner: str             # "A" (reverted), "B" (kept), "tie" (reverted)
    confidence: float
    judge_reasoning: str
    rubric_name: str
    kept: bool              # True if the change was kept
    latency_ms: float

    def to_dict(self) -> dict:
        return {
            "iteration": self.iteration,
            "key": self.key,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "reasoning": self.reasoning,
            "winner": self.winner,
            "confidence": self.confidence,
            "judge_reasoning": self.judge_reasoning,
            "rubric": self.rubric_name,
            "kept": self.kept,
            "latency_ms": self.latency_ms,
        }


@dataclass
class DGMRunResult:
    """Summary of a complete DGM run."""
    run_id: str
    iterations_run: int
    iterations_kept: int
    stopped_early: bool
    stop_reason: str
    history: list[IterationResult] = field(default_factory=list)
    total_ms: float = 0.0

    @property
    def keep_rate(self) -> float:
        if not self.iterations_run:
            return 0.0
        return self.iterations_kept / self.iterations_run

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "iterations_run": self.iterations_run,
            "iterations_kept": self.iterations_kept,
            "keep_rate": self.keep_rate,
            "stopped_early": self.stopped_early,
            "stop_reason": self.stop_reason,
            "total_ms": self.total_ms,
            "history": [h.to_dict() for h in self.history],
        }


# ── Main loop ──────────────────────────────────────────────────────────────

class DGMLoop:
    """
    The Darwin Gödel Machine self-improvement loop for BeigeBox.

    Iteratively proposes, tests, and keeps (or reverts) config changes to
    improve response quality as judged by a rotating pairwise rubric.

    Args:
        judge:              DGMJudge instance.
        proposer:           DGMProposer instance.
        patcher:            ConfigPatcher instance.
        rotator:            RubricRotator instance.
        n_probes:           Number of requests sampled per iteration (default 3).
                            More = more reliable, slower.
        confidence_threshold: Min judge confidence to keep a change (default 0.65).
                            Lower = accept more changes (explore more).
                            Higher = only keep clear improvements (conservative).
        patience:           Stop early if no improvement in this many iterations.
        proxy_url:          Where to send probe requests (BeigeBox endpoint).
        probe_model:        Model to use when re-running probes (empty = config default).
        probe_timeout:      Timeout for each probe request in seconds.
    """

    def __init__(
        self,
        judge: DGMJudge,
        proposer: DGMProposer,
        patcher: ConfigPatcher,
        rotator: RubricRotator,
        n_probes: int = 3,
        confidence_threshold: float = 0.65,
        patience: int = 10,
        proxy_url: str = "http://localhost:1337",
        probe_model: str = "",
        probe_timeout: float = 60.0,
    ) -> None:
        self._judge = judge
        self._proposer = proposer
        self._patcher = patcher
        self._rotator = rotator
        self._n_probes = max(1, n_probes)
        self._confidence_threshold = confidence_threshold
        self._patience = patience
        self._proxy_url = proxy_url.rstrip("/")
        self._probe_model = probe_model
        self._probe_timeout = probe_timeout

    @classmethod
    def from_config(cls, **overrides) -> "DGMLoop":
        """
        Construct a DGMLoop from the active BeigeBox config.

        Reads judge_model, proposer_model, proxy_url from config.yaml.
        Individual parameters can be overridden via kwargs.
        """
        cfg = get_config()
        proxy_url = overrides.pop("proxy_url", "http://localhost:1337")

        # Use the routing model (usually 3B) for both judge and proposer
        routing_model = (
            cfg.get("models", {}).get("profiles", {}).get("routing", "qwen3:4b")
        )
        judge_model = overrides.pop("judge_model", routing_model)
        proposer_model = overrides.pop("proposer_model", routing_model)
        rotation_interval = overrides.pop("rotation_interval", 5)

        return cls(
            judge=DGMJudge(judge_model=judge_model, backend_url=proxy_url),
            proposer=DGMProposer(proposer_model=proposer_model, backend_url=proxy_url),
            patcher=ConfigPatcher(),
            rotator=RubricRotator(rotation_interval=rotation_interval),
            proxy_url=proxy_url,
            **overrides,
        )

    async def run(self, n_iterations: int = 20) -> DGMRunResult:
        """
        Run the DGM loop for up to n_iterations iterations.

        Each iteration:
          - Samples probes from wiretap history
          - Proposes a config change
          - Applies the change
          - Re-runs probes with the new config
          - Judges before vs after
          - Keeps or reverts based on confidence_threshold
          - Ticks the rubric rotator

        Args:
            n_iterations: Maximum number of iterations to run.

        Returns:
            DGMRunResult with full history of what was tried and kept.
        """
        run_id = str(uuid.uuid4())[:8]
        t_run_start = time.monotonic()
        history: list[IterationResult] = []
        no_improvement_streak = 0
        kept_count = 0

        _tap("dgm.run_start", f"DGM run {run_id} starting ({n_iterations} iterations)", run_id)
        logger.info(
            "dgm.run_start run_id=%s n_iterations=%d n_probes=%d threshold=%.2f "
            "patience=%d rotation_interval=%d",
            run_id, n_iterations, self._n_probes, self._confidence_threshold,
            self._patience, self._rotator.rotation_interval,
        )

        stopped_early = False
        stop_reason = "completed"

        for i in range(n_iterations):
            iter_start = time.monotonic()
            rubric = self._rotator.current()

            logger.info(
                "dgm.iteration start=%d/%d rubric=%s streak=%d",
                i + 1, n_iterations, rubric.name, no_improvement_streak,
            )

            # ── 1. Sample probes ────────────────────────────────────────
            probes = await self._sample_probes(run_id)
            if not probes:
                logger.warning("dgm.iteration no probes available — skipping iteration %d", i + 1)
                continue

            # ── 2. Propose a change ─────────────────────────────────────
            history_dicts = [h.to_dict() for h in history]
            proposal = await self._proposer.propose(rubric, history_dicts)
            if not proposal:
                logger.warning("dgm.iteration proposer returned nothing — skipping iteration %d", i + 1)
                no_improvement_streak += 1
                if no_improvement_streak >= self._patience:
                    stopped_early = True
                    stop_reason = f"patience={self._patience} exhausted"
                    break
                continue

            patch = proposal.patch

            # ── 3. Validate and apply the change ───────────────────────
            patch_result = self._patcher.apply(patch)
            if not patch_result.ok:
                logger.warning(
                    "dgm.iteration patch_failed key=%s error=%s",
                    patch.key, patch_result.error,
                )
                no_improvement_streak += 1
                continue

            # Small settle time — hot-reload is mtime-based, needs a moment
            await asyncio.sleep(0.5)

            # ── 4. Re-run probes with new config ────────────────────────
            new_responses = await self._run_probes(probes, run_id)

            # ── 5. Judge before vs after ────────────────────────────────
            verdicts = []
            for probe, new_resp in zip(probes, new_responses):
                verdict = await self._judge.compare(
                    request=probe.request,
                    response_a=probe.response,    # A = original (before)
                    response_b=new_resp,           # B = new (after change)
                    rubric=rubric,
                )
                verdicts.append(verdict)
                logger.debug(
                    "dgm.probe_verdict winner=%s confidence=%.2f probe=%r",
                    verdict.winner, verdict.confidence, probe.request[:60],
                )

            # Aggregate across probes: B wins if majority say B won
            b_wins = sum(1 for v in verdicts if v.b_wins())
            avg_confidence = sum(v.confidence for v in verdicts) / len(verdicts) if verdicts else 0.0
            overall_winner = "B" if b_wins > len(verdicts) / 2 else "A"
            best_reasoning = max(verdicts, key=lambda v: v.confidence).reasoning if verdicts else ""

            # ── 6. Keep or revert ───────────────────────────────────────
            keep = (overall_winner == "B" and avg_confidence >= self._confidence_threshold)

            if keep:
                kept_count += 1
                no_improvement_streak = 0
                logger.info(
                    "dgm.kept key=%s value=%r confidence=%.2f rubric=%s",
                    patch.key, patch.value, avg_confidence, rubric.name,
                )
                _tap(
                    "dgm.improvement",
                    f"[{rubric.name}] KEPT {patch.key}={patch.value!r} "
                    f"(confidence={avg_confidence:.2f}): {best_reasoning}",
                    run_id,
                    {"key": patch.key, "value": patch.value, "confidence": avg_confidence},
                )
            else:
                self._patcher.revert(patch, patch_result.original)
                no_improvement_streak += 1
                logger.info(
                    "dgm.reverted key=%s value=%r winner=%s confidence=%.2f rubric=%s",
                    patch.key, patch.value, overall_winner, avg_confidence, rubric.name,
                )
                _tap(
                    "dgm.no_improvement",
                    f"[{rubric.name}] REVERTED {patch.key}={patch.value!r} "
                    f"(confidence={avg_confidence:.2f}, winner={overall_winner})",
                    run_id,
                    {"key": patch.key, "value": patch.value, "confidence": avg_confidence},
                )

            iter_result = IterationResult(
                iteration=i + 1,
                key=patch.key,
                old_value=patch_result.original,
                new_value=patch.value,
                reasoning=patch.reasoning,
                winner=overall_winner,
                confidence=avg_confidence,
                judge_reasoning=best_reasoning,
                rubric_name=rubric.name,
                kept=keep,
                latency_ms=(time.monotonic() - iter_start) * 1000,
            )
            history.append(iter_result)

            # ── 7. Tick rubric rotator ──────────────────────────────────
            rotated = self._rotator.tick()
            if rotated:
                _tap(
                    "dgm.rubric_rotated",
                    f"Rubric rotated to: {self._rotator.current().name}",
                    run_id,
                    self._rotator.to_dict(),
                )

            # ── 8. Check early stopping ─────────────────────────────────
            if no_improvement_streak >= self._patience:
                stopped_early = True
                stop_reason = f"patience={self._patience} exhausted (no improvement)"
                logger.info("dgm.early_stop reason=%s", stop_reason)
                _tap("dgm.early_stop", stop_reason, run_id)
                break

        total_ms = (time.monotonic() - t_run_start) * 1000
        result = DGMRunResult(
            run_id=run_id,
            iterations_run=len(history),
            iterations_kept=kept_count,
            stopped_early=stopped_early,
            stop_reason=stop_reason,
            history=history,
            total_ms=total_ms,
        )

        _tap(
            "dgm.run_complete",
            f"DGM run {run_id} complete: {kept_count}/{len(history)} kept "
            f"({result.keep_rate:.0%}) in {total_ms/1000:.1f}s",
            run_id,
            result.to_dict(),
        )
        logger.info(
            "dgm.run_complete run_id=%s kept=%d/%d keep_rate=%.2f total_ms=%.0f",
            run_id, kept_count, len(history), result.keep_rate, total_ms,
        )
        return result

    async def _sample_probes(self, run_id: str) -> list[Probe]:
        """
        Sample recent request/response pairs from wiretap history.

        Falls back to a set of built-in probes if history is unavailable
        (e.g., fresh install with no wiretap data yet).
        """
        probes = await self._probes_from_wiretap()
        if probes:
            return probes[:self._n_probes]

        # Fallback: use static probes for systems with no history yet
        logger.debug("dgm.probes using fallback static probes (no wiretap history)")
        return _FALLBACK_PROBES[:self._n_probes]

    async def _probes_from_wiretap(self) -> list[Probe]:
        """
        Read recent user/assistant pairs from the SQLite store.

        Returns up to n_probes * 3 candidates (caller picks n_probes).
        Returns empty list if store is unavailable.
        """
        try:
            from beigebox.main import get_state
            state = get_state()
            if not state.sqlite_store:
                return []

            rows = state.sqlite_store.get_recent_conversations(
                limit=self._n_probes * 3,
            )
            probes = []
            for row in rows:
                user_msg = row.get("user_message", "")
                assistant_msg = row.get("assistant_message", "")
                if user_msg and assistant_msg:
                    probes.append(Probe(request=user_msg, response=assistant_msg))
            return probes
        except Exception as exc:
            logger.debug("dgm.probes wiretap read failed: %s", exc)
            return []

    async def _run_probes(self, probes: list[Probe], run_id: str) -> list[str]:
        """
        Re-run each probe request against the current (patched) config.

        Runs probes concurrently to keep iteration time reasonable.
        Returns a list of response strings parallel to probes.
        If a probe fails, returns the original response (treats as no-change).
        """
        tasks = [self._run_single_probe(p, run_id) for p in probes]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        responses = []
        for probe, result in zip(probes, results):
            if isinstance(result, Exception):
                logger.warning("dgm.probe_failed: %s — using original response", result)
                responses.append(probe.response)
            else:
                responses.append(result)
        return responses

    async def _run_single_probe(self, probe: Probe, run_id: str) -> str:
        """Send a single probe request and return the response text."""
        payload: dict = {
            "messages": [{"role": "user", "content": probe.request}],
            "stream": False,
        }
        if self._probe_model:
            payload["model"] = self._probe_model

        async with httpx.AsyncClient(timeout=self._probe_timeout) as client:
            resp = await client.post(
                self._proxy_url + "/v1/chat/completions",
                json=payload,
            )
            resp.raise_for_status()

        from beigebox.response_normalizer import normalize_response
        return normalize_response(resp.json()).content


# ── Fallback probes for fresh installs ────────────────────────────────────
# Used when wiretap history is empty. Covers a range of request types
# so the judge has diverse signals.

_FALLBACK_PROBES = [
    Probe(
        request="What is the difference between a list and a tuple in Python?",
        response="A list is mutable and uses square brackets [...], while a tuple is immutable and uses parentheses (...). Lists are better for collections that change; tuples for fixed data.",
    ),
    Probe(
        request="Explain how TCP handshake works.",
        response="TCP uses a 3-way handshake: SYN (client→server), SYN-ACK (server→client), ACK (client→server). This establishes a reliable, ordered connection before data transfer.",
    ),
    Probe(
        request="Write a Python function to reverse a string.",
        response='def reverse_string(s: str) -> str:\n    return s[::-1]',
    ),
    Probe(
        request="What is gradient descent?",
        response="Gradient descent is an optimization algorithm that iteratively adjusts parameters in the direction that reduces a loss function, using the negative gradient as the update direction.",
    ),
    Probe(
        request="How do I find duplicate values in a list in Python?",
        response="seen = set()\nduplicates = [x for x in lst if x in seen or seen.add(x)]",
    ),
]
