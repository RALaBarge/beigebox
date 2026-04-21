"""
Trinity Function Extractor - Extract functions from source code for fuzzing.
"""

import ast
import re
from typing import List, Dict, Tuple, Any, Optional
from pathlib import Path

from .logger import TrinityLogger, TrinityLogConfig


class FunctionExtractor:
    """Extract individual functions from source code for fuzzing."""

    def __init__(self, logger: Optional[TrinityLogger] = None):
        self.logger = logger if logger is not None else TrinityLogger("noop", TrinityLogConfig(enabled=False))

    def extract_functions(self, code: str, file_path: str) -> List[Dict[str, Any]]:
        """
        Parse Python code and extract all functions.

        Returns:
            [
                {
                    "name": "parse_json",
                    "source": "def parse_json(data): ...",
                    "line_start": 42,
                    "line_end": 50,
                    "parameters": ["data"],
                    "is_fuzzable": True,
                    "reason": "accepts_data_parameter",
                    "docstring": "...",
                    "signature": "def parse_json(data: str) -> dict:",
                },
                ...
            ]
        """
        functions = []

        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            self.logger.warn("SyntaxError parsing file — returning empty function list",
                             phase="function_extractor", file=file_path, exc_msg=str(e))
            return []

        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                func_info = self._extract_function_info(node, code, file_path)
                if func_info and self._is_fuzzable(func_info):
                    functions.append(func_info)

        self.logger.debug("extracted fuzzable functions from file",
                          phase="function_extractor", file=file_path, count=len(functions))
        return functions

    def _extract_function_info(self, node: ast.FunctionDef, code: str, file_path: str) -> Dict[str, Any]:
        """Extract information about a single function."""
        lines = code.split('\n')

        # Get source code
        line_start = node.lineno - 1
        line_end = node.end_lineno or len(lines)

        func_source = '\n'.join(lines[line_start:line_end])

        # Extract parameters
        parameters = [arg.arg for arg in node.args.args]

        # Get docstring
        docstring = ast.get_docstring(node) or ""

        # Build signature
        args_str = ', '.join(parameters)
        signature = f"def {node.name}({args_str}):"

        # Check for return type annotation
        if node.returns:
            return_type = ast.unparse(node.returns) if hasattr(ast, 'unparse') else "..."
            signature += f" -> {return_type}:"

        return {
            "name": node.name,
            "source": func_source,
            "line_start": line_start + 1,  # 1-indexed
            "line_end": line_end,
            "parameters": parameters,
            "docstring": docstring,
            "signature": signature,
            "file_path": file_path,
            "is_fuzzable": False,  # Set by _is_fuzzable
            "reason": "",
        }

    def _is_fuzzable(self, func_info: Dict[str, Any]) -> bool:
        """
        Decide if function is worth fuzzing.

        Criteria:
        - Has at least 1 parameter (not self._private helpers)
        - Has a return statement or side effect
        - Not a trivial function
        - Not a special method
        """
        name = func_info['name']
        source = func_info['source']
        parameters = func_info['parameters']

        # Filter: Must have parameters to fuzz
        if not parameters:
            func_info['reason'] = "no_parameters"
            return False

        # Warn about self-only methods that would generate invalid harnesses
        if parameters == ['self']:
            self.logger.debug("skipping function with only 'self' parameter — would generate invalid harness",
                              phase="function_extractor", func=name)
            func_info['reason'] = "self_only_parameter"
            return False

        # Filter: Skip special methods
        if name.startswith('__') and name.endswith('__'):
            func_info['reason'] = "special_method"
            return False

        # Filter: Skip very trivial functions
        lines = source.split('\n')
        if len(lines) < 2:
            func_info['reason'] = "trivial_function"
            return False

        # Passes filters
        func_info['is_fuzzable'] = True
        func_info['reason'] = "fuzzable"
        return True

    def extract_from_file(self, file_path: str) -> List[Dict[str, Any]]:
        """Extract functions from a file on disk."""
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                code = f.read()
            return self.extract_functions(code, str(file_path))
        except Exception as e:
            self.logger.warn("Exception reading file — skipping",
                             phase="function_extractor", file=file_path, exc_msg=str(e))
            return []

    def filter_by_risk(
        self,
        functions: List[Dict],
        risk_scores: Dict[str, int],
        max_functions: int = 25,
    ) -> List[Dict]:
        """
        Filter to high-risk functions and limit count.

        Args:
            functions: Extracted functions
            risk_scores: {function_name: risk_score}
            max_functions: Max functions to return

        Returns: Top N high-risk functions
        """
        # Score each function
        for func in functions:
            func['risk_score'] = risk_scores.get(func['name'], 5)

        # Sort by risk (descending)
        sorted_funcs = sorted(functions, key=lambda x: x['risk_score'], reverse=True)

        # Return top N
        return sorted_funcs[:max_functions]

    def find_fuzzable_functions_in_repo(self, repo_path: str) -> List[Dict[str, Any]]:
        """
        Recursively find all fuzzable functions in repository.

        Returns list of all fuzzable functions with file paths.
        """
        all_functions = []
        repo_path = Path(repo_path)

        # Recursively find Python files
        for py_file in repo_path.rglob('*.py'):
            # Skip test files, venv, etc
            if any(skip in str(py_file) for skip in ['.venv', '__pycache__', '.git', 'node_modules']):
                continue

            functions = self.extract_from_file(str(py_file))
            all_functions.extend(functions)

        return all_functions
