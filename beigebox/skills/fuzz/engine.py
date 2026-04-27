"""Fuzzing engine: scoring, seed corpus, harness generation, subprocess driver, classifier."""

from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass
class CrashFinding:
    function_name: str
    crash_type: str
    reproducer: bytes
    stack_trace: str
    file_path: str
    line_number: int
    severity: str
    confidence: float


class RiskScorer:
    """Score functions 1-10 by vulnerability likelihood."""

    PARSING_KEYWORDS = ("parse", "decode", "deserialize", "load", "read", "extract", "uncompress")
    PROCESSING_KEYWORDS = ("process", "handle", "validate", "transform", "filter", "convert")
    STRING_KEYWORDS = ("concat", "join", "split", "substring", "truncate", "format")
    CRYPTO_KEYWORDS = ("encrypt", "decrypt", "hash", "sign", "verify", "hmac")

    def score(self, function_name: str, source: str) -> int:
        score = 5
        name = function_name.lower()
        lines = source.split("\n")

        if any(kw in name for kw in self.PARSING_KEYWORDS):
            score += 4
        elif any(kw in name for kw in self.PROCESSING_KEYWORDS):
            score += 3
        elif any(kw in name for kw in self.STRING_KEYWORDS):
            score += 2

        if any(kw in name for kw in self.CRYPTO_KEYWORDS):
            score += 2

        if any("while " in ln or " for " in ln or ln.lstrip().startswith("for ") for ln in lines):
            score += 1
        if any(function_name in ln and "def " not in ln for ln in lines[1:]):
            score += 1  # potential self-recursion
        if len(lines) > 50:
            score += 1

        if function_name.startswith("_"):
            score -= 2
        if len(lines) < 3:
            score -= 1
        if any("assert" in ln for ln in lines):
            score -= 1
        if "return" not in source:
            score -= 1

        return max(1, min(10, score))

    def score_functions(self, functions: list[dict]) -> list[dict]:
        for func in functions:
            func["risk_score"] = self.score(func["name"], func["source"])
        return sorted(functions, key=lambda x: x["risk_score"], reverse=True)


class SeedCorpusExtractor:
    """Extract seed inputs from docstrings, comments, and known parser edge cases."""

    def extract(self, source: str, function_name: str) -> list[bytes]:
        seeds: list[bytes] = []

        for m in re.finditer(r'"""(.*?)"""', source, re.DOTALL):
            for q in re.finditer(r"['\"](.*?)['\"]", m.group(1)):
                ex = q.group(1)
                if 0 < len(ex) < 500:
                    seeds.append(ex.encode("utf-8"))

        for line in source.split("\n"):
            if "#" in line:
                comment = line.split("#", 1)[1]
                for q in re.finditer(r"['\"](.*?)['\"]", comment):
                    ex = q.group(1)
                    if 0 < len(ex) < 500:
                        seeds.append(ex.encode("utf-8"))

        name = function_name.lower()
        if any(kw in name for kw in ("parse", "decode", "deserialize")):
            seeds.extend(self._parser_seeds())
        if any(kw in name for kw in ("concat", "split", "substring")):
            seeds.extend(self._string_seeds())

        seen: set[bytes] = set()
        unique: list[bytes] = []
        for s in seeds:
            if s not in seen:
                unique.append(s)
                seen.add(s)
        return unique

    def _parser_seeds(self) -> list[bytes]:
        return [
            b"",
            b"\x00",
            b"\n" * 100,
            b'{"a": 1}',
            b"[1, 2, 3]",
            b'{"' + b"x" * 1000 + b'": 1}',
            b"[" * 50 + b"]" * 50,
            b"\xff\xfe",
        ]

    def _string_seeds(self) -> list[bytes]:
        return [b"", b"a", b"a" * 1000, b"\x00" * 100, b"abc\ndef\n", b"\n"]


class CrashClassifier:
    """Filter library noise + expected exceptions; only report critical app crashes."""

    LIBRARY_PATTERNS = ("/site-packages/", "/lib/python", "<frozen", "json.py", "urllib", "requests")
    EXPECTED_EXCEPTIONS = ("ValueError", "KeyError", "TypeError", "AttributeError", "IndexError")
    CRITICAL_TYPES = ("Timeout", "RecursionError", "MemoryError", "SegmentationFault", "AssertionError")

    def is_app_crash(self, crash: dict[str, Any], app_root: str) -> bool:
        crash_type = crash.get("type", "")
        stack_trace = crash.get("stack_trace", "")

        if any(p in stack_trace for p in self.LIBRARY_PATTERNS):
            return False
        # Exact match — substring would over-filter ("MyValueError") and
        # over-promote ("Timeout-likeError" containing "Timeout").
        if crash_type in self.EXPECTED_EXCEPTIONS:
            return False
        if crash_type not in self.CRITICAL_TYPES:
            return False
        if app_root and app_root not in stack_trace:
            return False
        return True


class SmartHarnessGenerator:
    """Emit a self-contained Python harness that imports the target and runs a mutation loop."""

    def generate_basic_harness(
        self,
        function_name: str,
        source_file: str,
        parameter_type: str = "bytes",
        max_crashes: int = 5,
    ) -> str:
        """
        Build a standalone harness. Reads seed paths from argv (corpus dir).
        Loops mutating seeds, calls the target, catches crashes, prints JSON to stdout.
        """
        return _HARNESS_TEMPLATE.format(
            source_file=json.dumps(source_file),
            function_name=function_name,
            parameter_type=parameter_type,
            max_crashes=max_crashes,
        )

    def infer_parameter_type(self, source: str, parameter_name: str) -> str:
        m = re.search(rf"\b{re.escape(parameter_name)}\s*:\s*(\w+)", source)
        if m and m.group(1) in ("bytes", "str", "int", "float", "dict", "list"):
            return m.group(1)
        if f"{parameter_name}.decode" in source:
            return "bytes"
        if f"len({parameter_name})" in source or f"{parameter_name}[" in source:
            return "bytes"
        if f"int({parameter_name})" in source:
            return "str"
        return "bytes"


_HARNESS_TEMPLATE = '''\
"""Auto-generated fuzz harness. Do not edit."""
import importlib
import importlib.util
import json
import os
import pathlib
import random
import sys
import time
import traceback

SOURCE_FILE = {source_file}
FUNCTION_NAME = "{function_name}"
PARAM_TYPE = "{parameter_type}"
MAX_CRASHES = {max_crashes}

def _load_target():
    """Import the target's module respecting package boundaries.

    Walks up from SOURCE_FILE collecting parent dirs that contain ``__init__.py``;
    everything above that boundary goes on ``sys.path`` so relative imports inside
    the package resolve. Falls back to file-location loading for standalone .py.
    """
    src = pathlib.Path(SOURCE_FILE).resolve()
    pkg_parts = [src.stem]
    parent = src.parent
    while (parent / "__init__.py").exists():
        pkg_parts.insert(0, parent.name)
        parent = parent.parent
    sys.path.insert(0, str(parent))
    mod_name = ".".join(pkg_parts)
    try:
        mod = importlib.import_module(mod_name)
    except Exception:
        # Standalone module load with no relative imports — fall back to spec.
        spec = importlib.util.spec_from_file_location(mod_name or "_fuzz_target_mod", SOURCE_FILE)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[mod_name or "_fuzz_target_mod"] = mod
        spec.loader.exec_module(mod)
    return getattr(mod, FUNCTION_NAME)

def _coerce(data: bytes):
    if PARAM_TYPE == "bytes":
        return data
    if PARAM_TYPE == "str":
        return data.decode("utf-8", errors="replace")
    if PARAM_TYPE == "int":
        return int.from_bytes(data[:8] or b"\\x00", "little", signed=True)
    if PARAM_TYPE == "float":
        try:
            return float(data.decode("utf-8", errors="ignore") or "0")
        except ValueError:
            return 0.0
    return data

def _mutate(rng: random.Random, seeds: list[bytes]) -> bytes:
    if not seeds:
        return rng.randbytes(rng.randint(0, 64))
    base = bytearray(rng.choice(seeds))
    op = rng.choices(
        ("flip", "ins", "del", "splice", "trunc", "grow", "noise", "repeat", "blowup"),
        weights=(20, 15, 10, 10, 5, 15, 15, 5, 5),
    )[0]
    if op == "flip" and base:
        i = rng.randrange(len(base))
        base[i] ^= 1 << rng.randrange(8)
    elif op == "ins":
        base.insert(rng.randrange(len(base) + 1), rng.randrange(256))
    elif op == "del" and base:
        del base[rng.randrange(len(base))]
    elif op == "splice" and len(seeds) > 1:
        other = rng.choice(seeds)
        cut = rng.randrange(len(base) + 1)
        base = bytearray(base[:cut] + other[: rng.randint(0, len(other))])
    elif op == "trunc" and base:
        base = base[: rng.randint(0, len(base))]
    elif op == "grow":
        base.extend(rng.randbytes(rng.randint(1, 32)))
    elif op == "noise":
        base = bytearray(rng.randbytes(rng.randint(0, 256)))
    elif op == "repeat" and base:
        # Repeat a small fragment many times — exposes O(n^2) and unbounded loops.
        frag = bytes(base[: rng.randint(1, min(8, len(base)))])
        reps = rng.randint(64, 4096)
        base = bytearray(frag * reps)
    elif op == "blowup":
        # Generate a single-character payload of KB scale — exposes recursion
        # depth bombs (e.g. parser that recurses on every '[').
        ch = bytes([rng.choice((ord("["), ord("{{"), ord("("), 0, ord("a")))])
        base = bytearray(ch * rng.randint(1024, 32768))
    return bytes(base)

def _load_seeds(corpus_dir: str) -> list[bytes]:
    if not corpus_dir or not os.path.isdir(corpus_dir):
        return []
    out = []
    for name in sorted(os.listdir(corpus_dir)):
        try:
            with open(os.path.join(corpus_dir, name), "rb") as f:
                out.append(f.read())
        except OSError:
            pass
    return out

def main():
    corpus_dir = sys.argv[1] if len(sys.argv) > 1 else ""
    budget_seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 5.0
    seed = int(sys.argv[3]) if len(sys.argv) > 3 else 0xC0FFEE

    rng = random.Random(seed)
    seeds = _load_seeds(corpus_dir) or [b"", b"a", b"\\x00" * 8]
    target = _load_target()

    deadline = time.monotonic() + budget_seconds
    crashes = []
    iterations = 0
    seen_crash_keys = set()

    while time.monotonic() < deadline and len(crashes) < MAX_CRASHES:
        payload = _mutate(rng, seeds)
        try:
            target(_coerce(payload))
        except (ValueError, KeyError, TypeError, AttributeError, IndexError):
            pass  # expected — input validation rejecting garbage
        except (RecursionError, MemoryError, AssertionError) as exc:
            tb = traceback.format_exc()
            key = (type(exc).__name__, tb.splitlines()[-1] if tb else "")
            if key not in seen_crash_keys:
                seen_crash_keys.add(key)
                crashes.append({{
                    "type": type(exc).__name__,
                    "message": str(exc)[:500],
                    "reproducer_hex": payload.hex(),
                    "stack_trace": tb,
                }})
        except SystemExit:
            raise
        except BaseException as exc:
            tb = traceback.format_exc()
            key = (type(exc).__name__, tb.splitlines()[-1] if tb else "")
            if key not in seen_crash_keys:
                seen_crash_keys.add(key)
                crashes.append({{
                    "type": type(exc).__name__,
                    "message": str(exc)[:500],
                    "reproducer_hex": payload.hex(),
                    "stack_trace": tb,
                }})
        iterations += 1

    json.dump({{"iterations": iterations, "crashes": crashes}}, sys.stdout)
    sys.stdout.write("\\n")

if __name__ == "__main__":
    main()
'''


class Fuzzer:
    """Orchestrates harness execution in subprocess."""

    def __init__(self, timeout_seconds: int = 5, logger: Callable | None = None) -> None:
        self.timeout_seconds = timeout_seconds
        self.logger = logger

    async def fuzz_function(
        self,
        harness_code: str,
        function_name: str,
        file_path: str,
        seeds: list[bytes] | None = None,
        timeout_seconds: int | None = None,
    ) -> dict[str, Any]:
        timeout = timeout_seconds or self.timeout_seconds

        try:
            with tempfile.TemporaryDirectory(prefix="bb_fuzz_") as tmpdir:
                tmp = Path(tmpdir)
                harness_file = tmp / f"fuzz_{function_name}.py"
                harness_file.write_text(harness_code)

                corpus_dir = tmp / "corpus"
                corpus_dir.mkdir()
                for i, seed in enumerate(seeds or []):
                    (corpus_dir / f"seed_{i:04d}.bin").write_bytes(seed)

                return await self._run(harness_file, corpus_dir, timeout, function_name, file_path)
        except Exception as exc:
            return {
                "function": function_name,
                "file_path": file_path,
                "status": "error",
                "crashes": [],
                "iterations": 0,
                "duration_seconds": 0.0,
                "error": str(exc),
            }

    async def _run(
        self,
        harness_file: Path,
        corpus_dir: Path,
        timeout: int,
        function_name: str,
        file_path: str,
    ) -> dict[str, Any]:
        loop = asyncio.get_event_loop()
        start = loop.time()

        # Fresh seed per invocation so repeated runs explore different mutations
        # (Grok review #6). Reproducer hex still pins down any specific crash.
        seed = int.from_bytes(os.urandom(4), "little")

        proc = await asyncio.create_subprocess_exec(
            "python3",
            str(harness_file),
            str(corpus_dir),
            str(timeout),
            str(seed),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            # Give the subprocess a small grace window past its own budget so it can finalize.
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout + 5)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return {
                "function": function_name,
                "file_path": file_path,
                "status": "timeout",
                "crashes": [
                    {
                        "type": "Timeout",
                        "message": f"harness exceeded {timeout + 5}s",
                        "stack_trace": f"  File \"{file_path}\", in {function_name} (timeout)",
                    }
                ],
                "iterations": 0,
                "duration_seconds": round(loop.time() - start, 2),
            }

        elapsed = loop.time() - start
        out = stdout.decode("utf-8", errors="ignore").strip()
        err = stderr.decode("utf-8", errors="ignore").strip()

        result_blob: dict[str, Any] = {"iterations": 0, "crashes": []}
        if out:
            for line in reversed(out.splitlines()):
                line = line.strip()
                if line.startswith("{"):
                    try:
                        result_blob = json.loads(line)
                        break
                    except json.JSONDecodeError:
                        continue

        # If the harness itself crashed (top-level exception), surface it as a crash.
        if proc.returncode != 0 and not result_blob["crashes"]:
            result_blob["crashes"].append(
                {
                    "type": "HarnessCrash",
                    "message": f"harness exited rc={proc.returncode}",
                    "stack_trace": err[-2000:],
                }
            )

        return {
            "function": function_name,
            "file_path": file_path,
            "status": "complete",
            "crashes": result_blob["crashes"],
            "iterations": result_blob["iterations"],
            "duration_seconds": round(elapsed, 2),
            "stderr_tail": err[-500:] if err else "",
        }


class AdaptiveTimeAllocator:
    """Distribute a total time budget across functions weighted by risk + size.

    Guarantees ``sum(allocations.values()) <= total_budget_seconds`` provided
    ``len(functions) <= total_budget_seconds`` (each function needs ≥ 1s).
    Callers should cap selection upstream when budget is tighter than that.
    """

    def allocate_budget(
        self,
        functions: list[dict],
        total_budget_seconds: int = 120,
    ) -> dict[str, int]:
        if not functions:
            return {}

        weights: list[tuple[str, float]] = []
        for func in functions:
            risk = float(func.get("risk_score", 5))
            complexity = len(func["source"].split("\n"))
            weights.append((func["name"], risk + min(complexity / 10.0, 5.0)))

        total_weight = sum(w for _, w in weights) or 1.0

        # Floor at 1s per function before scaling — otherwise high-N runs would
        # produce sub-second allocations that the harness can't use anyway.
        shares: dict[str, float] = {
            name: max(1.0, total_budget_seconds * w / total_weight) for name, w in weights
        }

        # If the floor pushed us over budget, scale down proportionally without
        # re-flooring (Grok review #5: post-scale floor multiplied the overshoot).
        total = sum(shares.values())
        if total > total_budget_seconds:
            scale = total_budget_seconds / total
            shares = {k: v * scale for k, v in shares.items()}

        result = {k: max(1, int(round(v))) for k, v in shares.items()}

        # Final clamp: trim from the lowest allocations (which correspond to
        # lowest-weight functions, since the dict was built in weight order).
        overshoot = sum(result.values()) - total_budget_seconds
        if overshoot > 0:
            for name in sorted(result, key=lambda k: result[k]):
                if overshoot <= 0:
                    break
                trim = min(overshoot, result[name] - 1)
                if trim > 0:
                    result[name] -= trim
                    overshoot -= trim

        return result
