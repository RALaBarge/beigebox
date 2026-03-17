"""
EvalRunner — loads and runs eval suites against the BeigeBox proxy.

Suite format (YAML or JSON):
  name: "Basic routing smoke test"
  model: "llama3.2:3b"       # default model for all cases
  cases:
    - id: "hello"
      input: "Say hello"
      scorer: contains
      expect:
        contains: ["hello", "hi"]

    - id: "math"
      input: "What is 2+2?"
      scorer: contains
      expect:
        contains: ["4"]

    - id: "no_hate"
      input: "Write a poem about spring"
      scorer: not_contains
      expect:
        not_contains: ["hate", "kill", "violence"]

    - id: "json_format"
      input: "Return JSON with key 'status' set to 'ok'"
      scorer: regex
      expect:
        regex: '"status":\\s*"ok"'

    - id: "quality_check"
      input: "Explain quantum entanglement in one sentence"
      scorer: llm_judge
      expect:
        llm_judge: "The response accurately describes quantum entanglement in plain language"
        judge_model: "qwen3:4b"    # optional per-case override
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from uuid import uuid4

import httpx

from beigebox.eval.models import EvalCase, EvalResult, EvalSuite
from beigebox.eval.scorer import SCORERS, score_llm_judge, score_route_check

logger = logging.getLogger(__name__)


class EvalRunner:
    """
    Runs eval suites synchronously against a BeigeBox proxy endpoint.
    Results are returned as EvalResult objects and optionally stored in SQLite.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:1337",
        sqlite_store=None,
        judge_model: str = "",
        judge_backend_url: str = "",
        api_key: str = "",
    ):
        self.base_url = base_url.rstrip("/")
        self.sqlite_store = sqlite_store
        self.judge_model = judge_model
        self.judge_backend_url = judge_backend_url or base_url
        self._headers: dict[str, str] = {}
        if api_key:
            self._headers["Authorization"] = f"Bearer {api_key}"

    # ── Suite loading ──────────────────────────────────────────────────────

    @staticmethod
    def load_suite(path: str | Path) -> EvalSuite:
        """Load an eval suite from a YAML or JSON file."""
        p = Path(path)
        text = p.read_text(encoding="utf-8")
        if p.suffix in (".yaml", ".yml"):
            try:
                import yaml
                data = yaml.safe_load(text)
            except ImportError:
                raise ImportError(
                    "PyYAML required for YAML eval suites: pip install pyyaml"
                )
        else:
            data = json.loads(text)

        cases = []
        for c in data.get("cases", []):
            expect = c.get("expect", {})
            route = c.get("route", "")
            # Allow route: at the case level — merge into expect for route_check
            if route and "route" not in expect:
                expect = dict(expect, route=route)
            cases.append(EvalCase(
                id=c["id"],
                input=c["input"],
                scorer=c.get("scorer", "contains"),
                expect=expect,
                route=route,
                model=c.get("model", ""),
                system=c.get("system", ""),
                tags=c.get("tags", []),
                meta=c.get("meta", {}),
            ))
        return EvalSuite(
            name=data.get("name", p.stem),
            cases=cases,
            model=data.get("model", ""),
            base_url=data.get("base_url", ""),
            tags=data.get("tags", []),
            meta=data.get("meta", {}),
        )

    # ── Run ───────────────────────────────────────────────────────────────

    def run_suite(self, suite: EvalSuite) -> list[EvalResult]:
        """Run all cases in the suite. Returns list of EvalResult."""
        run_id = uuid4().hex[:16]
        base_url = suite.base_url or self.base_url
        results: list[EvalResult] = []

        logger.info(
            "Eval suite '%s' — %d case(s) run_id=%s base_url=%s",
            suite.name, len(suite.cases), run_id, base_url,
        )

        for case in suite.cases:
            result = self._run_case(case, suite, base_url, run_id)
            results.append(result)
            if self.sqlite_store:
                try:
                    self.sqlite_store.store_eval_result(suite.name, result)
                except Exception as e:
                    logger.warning("store_eval_result failed (case=%s): %s", case.id, e)

        passed = sum(1 for r in results if r.passed)
        logger.info(
            "Eval suite '%s' complete — %d/%d passed (run_id=%s)",
            suite.name, passed, len(results), run_id,
        )
        return results

    def _run_case(
        self, case: EvalCase, suite: EvalSuite, base_url: str, run_id: str
    ) -> EvalResult:
        """Run a single eval case against the proxy."""
        model = case.model or suite.model or ""
        messages: list[dict] = []
        if case.system:
            messages.append({"role": "system", "content": case.system})
        messages.append({"role": "user", "content": case.input})

        body: dict = {"messages": messages, "stream": False}
        if model:
            body["model"] = model

        t0 = time.monotonic()
        output = ""
        error = ""
        route_meta: dict = {}

        if case.scorer == "route_check":
            # Cheap path — call /api/v1/route-check instead of running full inference.
            # No model is loaded; only the routing pipeline runs.
            try:
                rc_resp = httpx.post(
                    f"{base_url}/api/v1/route-check",
                    json={"input": case.input},
                    headers=self._headers,
                    timeout=30.0,
                )
                rc_resp.raise_for_status()
                route_meta = rc_resp.json()
                output = f"route={route_meta.get('route', '')} model={route_meta.get('model', '')}"
            except Exception as e:
                error = str(e)
        else:
            try:
                resp = httpx.post(
                    f"{base_url}/v1/chat/completions",
                    json=body,
                    headers=self._headers,
                    timeout=120.0,
                )
                resp.raise_for_status()
                output = resp.json()["choices"][0]["message"]["content"]
            except Exception as e:
                error = str(e)

        latency_ms = round((time.monotonic() - t0) * 1000, 1)

        # Score
        if error:
            passed, score, reason = False, 0.0, f"request failed: {error}"
        elif case.scorer == "route_check":
            passed, score, reason = score_route_check(output, case.expect, meta=route_meta)
        elif case.scorer == "llm_judge":
            judge_model = case.expect.get("judge_model") or self.judge_model or model
            passed, score, reason = score_llm_judge(
                output, case.expect, judge_model, self.judge_backend_url
            )
        else:
            scorer_fn = SCORERS.get(case.scorer, SCORERS["contains"])
            passed, score, reason = scorer_fn(output, case.expect)

        status = "PASS" if passed else "FAIL"
        logger.info(
            "[%s] %s — scorer=%s latency=%.0fms score=%.2f %s",
            status, case.id, case.scorer, latency_ms, score, reason,
        )

        return EvalResult(
            case_id=case.id,
            input=case.input,
            output=output,
            passed=passed,
            score=score,
            scorer=case.scorer,
            model=model,
            latency_ms=latency_ms,
            run_id=run_id,
            reason=reason,
            error=error,
        )
