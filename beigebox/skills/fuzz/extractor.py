"""AST-based function discovery for the fuzz skill."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any


SKIP_DIRS = (".venv", "venv", "__pycache__", ".git", "node_modules", ".tox", ".mypy_cache")


class FunctionExtractor:
    """Extract individual Python functions from source for fuzzing."""

    def extract_functions(self, code: str, file_path: str) -> list[dict[str, Any]]:
        """Parse Python `code` and return one dict per **module-level** function.

        Skips:
        - nested functions (cannot be reached via ``getattr(module, name)``)
        - class methods (need an instance + state; a separate skill if we want them)
        - async functions (the harness calls the target synchronously)
        """
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return []

        functions: list[dict[str, Any]] = []
        for node in tree.body:  # iterate top-level only — no walk
            if isinstance(node, ast.FunctionDef):
                info = self._extract_function_info(node, code, file_path)
                if info is None:
                    continue
                self._mark_fuzzable(info)
                functions.append(info)
        return functions

    def extract_from_file(self, file_path: str) -> list[dict[str, Any]]:
        try:
            code = Path(file_path).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return []
        return self.extract_functions(code, str(file_path))

    def find_fuzzable_functions_in_repo(self, repo_path: str) -> list[dict[str, Any]]:
        all_funcs: list[dict[str, Any]] = []
        for py_file in Path(repo_path).rglob("*.py"):
            if any(skip in py_file.parts for skip in SKIP_DIRS):
                continue
            all_funcs.extend(self.extract_from_file(str(py_file)))
        return [f for f in all_funcs if f["is_fuzzable"]]

    def filter_by_risk(
        self,
        functions: list[dict],
        risk_scores: dict[str, int],
        max_functions: int = 25,
    ) -> list[dict]:
        for func in functions:
            func["risk_score"] = risk_scores.get(func["name"], 5)
        return sorted(functions, key=lambda x: x["risk_score"], reverse=True)[:max_functions]

    def _extract_function_info(
        self, node: ast.FunctionDef, code: str, file_path: str
    ) -> dict[str, Any] | None:
        lines = code.split("\n")
        line_start = node.lineno - 1
        line_end = node.end_lineno or len(lines)
        func_source = "\n".join(lines[line_start:line_end])

        positional = [arg.arg for arg in node.args.args]
        # Number of positional args that have a default value (defaults align right).
        num_defaults = len(node.args.defaults)
        # Tail-defaults: True if every arg after the first has a default.
        # Equivalent: len(positional) - num_defaults <= 1.
        tail_defaults_only = num_defaults >= max(0, len(positional) - 1)

        signature = f"def {node.name}({', '.join(positional)})"
        if node.returns is not None:
            signature += f" -> {ast.unparse(node.returns)}"
        signature += ":"

        return {
            "name": node.name,
            "source": func_source,
            "line_start": line_start + 1,
            "line_end": line_end,
            "parameters": positional,
            "tail_defaults_only": tail_defaults_only,
            "docstring": ast.get_docstring(node) or "",
            "signature": signature,
            "file_path": str(file_path),
            "is_fuzzable": False,
            "reason": "",
        }

    def _mark_fuzzable(self, info: dict[str, Any]) -> None:
        """A function is fuzzable iff:
        - it has at least one positional parameter (so we can feed it bytes), and
        - any extra parameters have defaults (so a one-arg call works), and
        - it is not a dunder, and
        - the body is more than a single line (trivial functions waste the budget).
        """
        name = info["name"]
        params = info["parameters"]
        source_lines = info["source"].split("\n")

        if not params:
            info["reason"] = "no_parameters"
            return
        if not info.get("tail_defaults_only", False):
            info["reason"] = "extra_required_args"
            return
        if name.startswith("__") and name.endswith("__"):
            info["reason"] = "special_method"
            return
        if len(source_lines) < 2:
            info["reason"] = "trivial_function"
            return

        info["is_fuzzable"] = True
        info["reason"] = "fuzzable"
