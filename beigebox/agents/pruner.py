"""
Context Pruner — extractive compression of operator turn context.

Approach:
  1. Hard guards: skip first N turns; skip short contexts below min_chars.
  2. Extractive strip: verbatim deletion only — DONE-step lines, completed
     progress entries, repeated file paths, excess blank lines.
     No LLM rewrite. No synonym substitution. Token identity preserved.
  3. Dedicated git archive: write full context to workspace/.context_store/
     (its own isolated git repo, never pollutes project history). Commit it,
     embed the short SHA + repo path in the compressed output so the agent
     can retrieve full detail via `git -C <store_path> show <sha>`.
  4. SQLite index: every archived turn writes a row to operator_turns so runs
     are queryable by run_id → [(turn_n, sha, store_path), ...].
  5. LLM pass (last resort, delete-only): only runs if extractive result still
     exceeds llm_threshold. Prompt instructs the model to delete lines —
     never rephrase, never synonym-substitute.

Config (config.yaml):
    operator:
      context_pruning:
        enabled: false
        min_chars: 800          # skip contexts shorter than this (~200 tokens)
        skip_first_turns: 4     # never prune the establishment phase
        llm_threshold: 3000     # only run LLM pass if extractive result > this
        git_archive: true       # commit full context to dedicated store repo
        model: ""               # defaults to operator model
        timeout: 8
"""
from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path

import httpx

from beigebox.config import get_config, get_runtime_config

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a context pruner for an autonomous agent harness.
You receive a working context block and the name of the next step to execute.

RULES — follow exactly:
- DELETE lines that are not needed for the next step.
- NEVER rephrase, reword, or substitute synonyms. Keep the exact wording of every line you keep.
- NEVER merge two lines into one. Only keep or delete whole lines.
- Remove: completed-step detail lines, repeated file-path mentions, verbose progress notes, done-turn recap lines.
- Keep: the objective, next-step instruction, active file paths, hard constraints, warnings, blockers.
- If the context is already under 300 words, return it unchanged.

Return ONLY the compressed context — no commentary, no explanation.\
"""

_STORE_DIR_NAME = ".context_store"


class ContextPruner:
    """
    Extractive context pruner for autonomous operator turns.

    Prunes cur_question BEFORE it is sent to the model.
    Archives the full pre-prune context to an isolated git repo so nothing is
    permanently lost and any turn can be exactly replayed.
    """

    def __init__(
        self,
        model: str,
        backend_url: str,
        timeout: int = 8,
        min_chars: int = 800,
        skip_first_turns: int = 4,
        llm_threshold: int = 3000,
        git_archive: bool = True,
        workspace_path: str | None = None,
        sqlite_store=None,
    ):
        self._model = model
        self._backend_url = backend_url.rstrip("/")
        self._timeout = timeout
        self._min_chars = min_chars
        self._skip_first_turns = skip_first_turns
        self._llm_threshold = llm_threshold
        self._git_archive = git_archive
        self._workspace_path = workspace_path
        self._sqlite_store = sqlite_store
        self._enabled = True
        self._store_path: Path | None = None  # resolved on first archive

    # ------------------------------------------------------------------
    # Public API

    @property
    def enabled(self) -> bool:
        return self._enabled

    def prune(
        self,
        cur_question: str,
        next_step_name: str,
        turn_n: int = 0,
        run_id: str | None = None,
        model: str = "",
    ) -> str:
        """
        Compress cur_question for turn_n.

        Hard guards fire first:
          - turn_n < skip_first_turns  → return original (establishment phase)
          - len(cur_question) < min_chars → return original (not worth it)

        Then: git archive → extractive strip → optional LLM delete pass.
        Always returns original on any error.
        """
        if not self._enabled or not cur_question.strip():
            return cur_question

        if turn_n < self._skip_first_turns:
            logger.debug("Pruner: skipping turn %d (establishment phase)", turn_n)
            return cur_question

        if len(cur_question) < self._min_chars:
            logger.debug("Pruner: skipping (%d chars < min_chars=%d)", len(cur_question), self._min_chars)
            return cur_question

        try:
            original_chars = len(cur_question)

            # Step 1: archive full context to dedicated git repo
            sha = None
            store_path_str = None
            if self._git_archive and self._workspace_path:
                sha, store_path_str = self._archive_to_store(
                    cur_question, turn_n, next_step_name
                )
                # Index in SQLite
                if sha and run_id and self._sqlite_store:
                    try:
                        self._sqlite_store.store_operator_turn(
                            run_id=run_id,
                            turn_n=turn_n,
                            input_sha=sha,
                            input_chars=original_chars,
                            store_path=store_path_str,
                            model=model,
                        )
                    except Exception as db_err:
                        logger.debug("Pruner: SQLite store failed (non-fatal): %s", db_err)

            # Step 2: extractive strip — verbatim deletion, no rewrite
            extracted = self._strip_extractive(cur_question)

            # Compose result with replay reference if archived
            if sha and store_path_str:
                header = (
                    f"[context archived — replay: git -C {store_path_str} show {sha}]\n\n"
                )
                result = header + extracted
            else:
                result = extracted

            # Step 3: LLM delete pass only if still over threshold
            if len(result) > self._llm_threshold and self._model and self._backend_url:
                llm_result = self._llm_delete_pass(result, next_step_name)
                if llm_result and len(llm_result) < len(result):
                    result = llm_result

            if result and len(result) < len(cur_question):
                logger.debug(
                    "Pruner: turn %d %d→%d chars (sha=%s)",
                    turn_n, original_chars, len(result), sha or "none",
                )
                return result

            return cur_question

        except Exception as exc:
            logger.debug("Pruner failed (returning original): %s", exc)
            return cur_question

    # ------------------------------------------------------------------
    # Extractive strip — verbatim deletion only

    def _strip_extractive(self, text: str) -> str:
        """
        Delete known-bloat lines verbatim. Never rewrites a single word.

        Removes:
          - [DONE] entries from numbered step lists
          - "- Turn N: <name> done" progress log lines
          - Duplicate workspace file path mentions (keep first per path)
          - Runs of 3+ blank lines collapsed to 2
        """
        lines = text.split("\n")
        out: list[str] = []
        seen_paths: set[str] = set()

        for line in lines:
            # Completed step entries: "  1. [DONE] Step name"
            if re.match(r"^\s+\d+\.\s+\[DONE\]", line):
                continue

            # Done-turn progress log: "- Turn 2: Add auth done"
            if re.match(r"^-\s+Turn\s+\d+:.+done\s*$", line, re.IGNORECASE):
                continue

            # Deduplicate workspace path mentions
            path_match = re.search(r"(/workspace/\S+)", line)
            if path_match:
                path = path_match.group(1).rstrip(".,;)")
                if path in seen_paths:
                    continue
                seen_paths.add(path)

            out.append(line)

        result = re.sub(r"\n{3,}", "\n\n", "\n".join(out))
        return result.strip()

    # ------------------------------------------------------------------
    # Dedicated git archive store

    def _get_store_path(self) -> Path | None:
        """
        Resolve and initialise the dedicated context_store git repo.
        Located at workspace/.context_store/ — completely isolated from the
        project git repo so archive commits never pollute project history.
        """
        if self._store_path is not None:
            return self._store_path

        if not self._workspace_path:
            return None

        store = Path(self._workspace_path) / _STORE_DIR_NAME
        store.mkdir(parents=True, exist_ok=True)

        if not (store / ".git").exists():
            init = subprocess.run(
                ["git", "init", str(store)],
                capture_output=True, timeout=5,
            )
            if init.returncode != 0:
                logger.debug("Pruner: git init failed for store at %s", store)
                return None
            for cmd in [
                ["git", "config", "user.email", "beigebox@localhost"],
                ["git", "config", "user.name", "BeigeBox Context Archive"],
            ]:
                subprocess.run(cmd, cwd=str(store), capture_output=True, timeout=5)

        self._store_path = store
        return store

    def _archive_to_store(
        self, text: str, turn_n: int, step_name: str
    ) -> tuple[str | None, str | None]:
        """
        Write full context to the store repo and commit it.
        Returns (short_sha, store_path_str) or (None, None) on failure.
        """
        try:
            store = self._get_store_path()
            if store is None:
                return None, None

            fname = store / f"turn_{turn_n:03d}.md"
            fname.write_text(
                f"# Turn {turn_n} — {step_name}\n\n{text}",
                encoding="utf-8",
            )

            subprocess.run(
                ["git", "add", fname.name],
                cwd=str(store), capture_output=True, timeout=5,
            )
            commit = subprocess.run(
                ["git", "commit", "-m", f"turn {turn_n}: {step_name[:60]}"],
                cwd=str(store), capture_output=True, text=True, timeout=5,
            )
            if commit.returncode != 0:
                return None, None

            sha_result = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=str(store), capture_output=True, text=True, timeout=5,
            )
            if sha_result.returncode != 0:
                return None, None

            return sha_result.stdout.strip(), str(store)

        except Exception as exc:
            logger.debug("Pruner: git archive failed (non-fatal): %s", exc)
            return None, None

    # ------------------------------------------------------------------
    # LLM delete pass — last resort, delete-only

    def _llm_delete_pass(self, text: str, next_step_name: str) -> str | None:
        """
        Cheap LLM call with strict delete-only instructions.
        Returns None on failure so caller keeps the extractive result.
        """
        user_msg = f"Next step: {next_step_name}\n\nContext to compress:\n{text}"
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user",   "content": user_msg},
            ],
            "temperature": 0.1,
            "max_tokens": 1024,
            "stream": False,
        }
        try:
            with httpx.Client(timeout=self._timeout) as client:
                resp = client.post(
                    f"{self._backend_url}/v1/chat/completions",
                    json=payload,
                )
                resp.raise_for_status()
                from beigebox.response_normalizer import normalize_response
                return normalize_response(resp.json()).content.strip() or None
        except Exception as exc:
            logger.debug("Pruner LLM pass failed: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Factory

    @classmethod
    def from_config(
        cls,
        workspace_path: str | None = None,
        sqlite_store=None,
    ) -> "ContextPruner":
        """Build from config.yaml. Returns a disabled no-op pruner if not configured."""
        try:
            cfg = get_config()
            rt  = get_runtime_config()
            prune_cfg = cfg.get("operator", {}).get("context_pruning", {})
            enabled = rt.get(
                "context_pruning_enabled",
                prune_cfg.get("enabled", False),
            )
            if not enabled:
                return cls._disabled()

            backend_url = (
                cfg.get("embedding", {}).get("backend_url")
                or cfg.get("backend", {}).get("url", "http://localhost:11434")
            )
            model = (
                prune_cfg.get("model")
                or rt.get("default_model")
                or cfg.get("backend", {}).get("default_model", "")
            )
            return cls(
                model=model,
                backend_url=backend_url,
                timeout=int(prune_cfg.get("timeout", 8)),
                min_chars=int(prune_cfg.get("min_chars", 800)),
                skip_first_turns=int(prune_cfg.get("skip_first_turns", 4)),
                llm_threshold=int(prune_cfg.get("llm_threshold", 3000)),
                git_archive=bool(prune_cfg.get("git_archive", True)),
                workspace_path=workspace_path,
                sqlite_store=sqlite_store,
            )
        except Exception as exc:
            logger.warning("ContextPruner.from_config failed, pruning disabled: %s", exc)
            return cls._disabled()

    @classmethod
    def _disabled(cls) -> "ContextPruner":
        p = cls.__new__(cls)
        p._enabled = False
        p._model = ""
        p._backend_url = ""
        p._timeout = 8
        p._min_chars = 800
        p._skip_first_turns = 4
        p._llm_threshold = 3000
        p._git_archive = False
        p._workspace_path = None
        p._sqlite_store = None
        p._store_path = None
        return p
