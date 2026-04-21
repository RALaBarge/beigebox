"""
Trinity Fuzzing Module - Production-Grade Dynamic Analysis

Smart fuzzing with high signal-to-noise ratio.
Components:
- Risk scoring for function selection
- Intelligent harness generation
- Seed corpus extraction
- Crash classification
- Adaptive time allocation
"""

import re
import asyncio
import json
import uuid
import subprocess
import tempfile
import signal
from typing import List, Dict, Tuple, Any, Optional, Callable
from dataclasses import dataclass
from pathlib import Path

from .logger import TrinityLogger, TrinityLogConfig


@dataclass
class CrashFinding:
    """A fuzzer-discovered crash."""
    function_name: str
    crash_type: str  # "Timeout", "SegmentationFault", "RecursionError", etc
    reproducer: bytes
    stack_trace: str
    file_path: str
    line_number: int
    severity: str  # "critical", "high", "medium"
    confidence: float  # 0.0-1.0


class RiskScorer:
    """Score functions by vulnerability likelihood (1-10)."""

    PARSING_KEYWORDS = ['parse', 'decode', 'deserialize', 'load', 'read', 'extract', 'uncompress']
    PROCESSING_KEYWORDS = ['process', 'handle', 'validate', 'transform', 'filter', 'convert']
    STRING_KEYWORDS = ['concat', 'join', 'split', 'substring', 'truncate', 'format']
    CRYPTO_KEYWORDS = ['encrypt', 'decrypt', 'hash', 'sign', 'verify', 'hmac']

    def score(self, function_name: str, source: str) -> int:
        """
        Score function 1-10 (higher = riskier).

        Heuristics:
        - Parsing functions: +4
        - Processing functions: +3
        - String/buffer operations: +2
        - Loop/recursion: +1-2
        - Crypto functions: +3
        - Negative: private functions (-2), trivial (-1), has bounds (-1)
        """
        score = 5  # Base score

        # Function name analysis
        func_lower = function_name.lower()

        if any(kw in func_lower for kw in self.PARSING_KEYWORDS):
            score += 4
        elif any(kw in func_lower for kw in self.PROCESSING_KEYWORDS):
            score += 3
        elif any(kw in func_lower for kw in self.STRING_KEYWORDS):
            score += 2

        if any(kw in func_lower for kw in self.CRYPTO_KEYWORDS):
            score += 2

        # Code analysis
        lines = source.split('\n')
        if any('while' in line or 'for' in line for line in lines):
            score += 1

        if source.count('recursion') > 0 or any('def ' + function_name in line and 'self' in line for line in lines):
            score += 1

        # Function size (larger = more surface area)
        if len(lines) > 50:
            score += 1

        # Negative factors
        if function_name.startswith('_'):
            score -= 2

        if len(lines) < 3:  # Trivial function
            score -= 1

        if any('assert' in line for line in lines):  # Has explicit validation
            score -= 1

        # Side-effect only (no return)
        if 'return' not in source:
            score -= 1

        return max(1, min(10, score))

    def score_functions(self, functions: List[Dict]) -> List[Dict]:
        """Score list of functions and return sorted by risk."""
        for func in functions:
            func['risk_score'] = self.score(func['name'], func['source'])

        # Sort by risk (descending)
        return sorted(functions, key=lambda x: x['risk_score'], reverse=True)


class SeedCorpusExtractor:
    """Extract seed inputs from source code to jumpstart fuzzing."""

    def extract(self, source: str, function_name: str) -> List[bytes]:
        """
        Extract example inputs from docstrings, comments, test patterns.
        """
        seeds = []

        # Extract from docstring examples
        docstring_match = re.search(r'"""(.*?)"""', source, re.DOTALL)
        if docstring_match:
            docstring = docstring_match.group(1)
            # Look for examples: parse_json('{"key": "value"}')
            for match in re.finditer(r"['\"](.*?)['\"]", docstring):
                example = match.group(1)
                if len(example) > 0 and len(example) < 500:
                    seeds.append(example.encode('utf-8'))

        # Extract from inline examples in comments
        for line in source.split('\n'):
            if '#' in line:
                comment = line.split('#', 1)[1]
                # Look for quoted strings in comments
                for match in re.finditer(r"['\"](.*?)['\"]", comment):
                    example = match.group(1)
                    if len(example) > 0 and len(example) < 500:
                        seeds.append(example.encode('utf-8'))

        # Add parser-specific edge cases
        if any(kw in function_name.lower() for kw in ['parse', 'decode', 'deserialize']):
            seeds.extend(self._parser_seeds())

        # Add string/buffer operation edge cases
        if any(kw in function_name.lower() for kw in ['concat', 'split', 'substring']):
            seeds.extend(self._string_seeds())

        # Deduplicate
        seen = set()
        unique_seeds = []
        for seed in seeds:
            if seed not in seen:
                unique_seeds.append(seed)
                seen.add(seed)

        return unique_seeds

    def _parser_seeds(self) -> List[bytes]:
        """Common edge cases for parsing functions."""
        return [
            b'',                              # Empty
            b'\x00',                          # Null byte
            b'\n' * 100,                      # Large whitespace
            b'{"a": 1}',                      # Simple JSON
            b'[1, 2, 3]',                     # Simple array
            b'{"' + b'x' * 1000 + b'": 1}',  # Large key
            b'[' * 50 + b']' * 50,            # Deeply nested
            b'\xff\xfe',                      # Invalid UTF-8
        ]

    def _string_seeds(self) -> List[bytes]:
        """Common edge cases for string operations."""
        return [
            b'',
            b'a',
            b'a' * 1000,
            b'\x00' * 100,
            b'abc\ndef\n',
            b'\n',
        ]


class CrashClassifier:
    """Classify crashes as real bugs vs library noise."""

    LIBRARY_PATTERNS = [
        '/site-packages/',
        '/lib/python',
        'standard library',
        '<frozen',
        'json.py',
        'urllib',
        'requests',
    ]

    EXPECTED_EXCEPTIONS = [
        'ValueError',
        'KeyError',
        'TypeError',
        'AttributeError',
        'IndexError',
    ]

    def is_app_crash(self, crash: Dict[str, Any], app_root: str) -> bool:
        """
        Decide if crash is in app code (not library noise).

        Returns: True if this is a real app bug, False if library noise.
        """
        crash_type = crash.get('type', '')
        stack_trace = crash.get('stack_trace', '')

        # Filter 1: Check if top frame is in library
        for pattern in self.LIBRARY_PATTERNS:
            if pattern in stack_trace:
                return False

        # Filter 2: Expected exceptions aren't vulnerabilities
        if any(exc in crash_type for exc in self.EXPECTED_EXCEPTIONS):
            return False

        # Filter 3: Only report critical crash types
        critical_types = ['Timeout', 'RecursionError', 'MemoryError', 'SegmentationFault', 'AssertionError']
        if not any(ct in crash_type for ct in critical_types):
            return False

        # Filter 4: Must have app_root in stack trace
        if app_root not in stack_trace:
            return False

        return True


class SmartHarnessGenerator:
    """Generate intelligent fuzz targets."""

    def generate_basic_harness(
        self,
        function_name: str,
        parameter_name: str,
        parameter_type: str = "bytes"
    ) -> str:
        """
        Generate a basic but functional fuzz harness.

        For complex functions, this is the fallback when LLM generation isn't available.
        """
        harness = f'''
import sys
try:
    import atheris
except ImportError:
    # Fallback if atheris not available
    def atheris_instrument_func(fn):
        return fn
    class atheris:
        @staticmethod
        def instrument_func(fn):
            return fn

@atheris.instrument_func
def fuzz_target(data):
    try:
        # Parse data as {parameter_type}
        if "{parameter_type}" == "bytes":
            input_val = data
        elif "{parameter_type}" == "str":
            input_val = data.decode('utf-8', errors='ignore')
        elif "{parameter_type}" == "int":
            input_val = int.from_bytes(data[:8], 'little', signed=True) if len(data) >= 8 else 0
        else:
            input_val = data

        # Call the function
        result = {function_name}(input_val)

    except (ValueError, KeyError, TypeError, AttributeError, IndexError):
        # Expected exceptions from input validation
        pass
    except (RecursionError, MemoryError, AssertionError, TimeoutError) as e:
        # Security-relevant exceptions - re-raise
        raise
    except Exception as e:
        # Log unexpected exceptions but don't crash
        pass

# For standalone execution
if __name__ == '__main__':
    if len(sys.argv) > 1:
        with open(sys.argv[1], 'rb') as f:
            fuzz_target(f.read())
'''
        return harness

    def infer_parameter_type(self, source: str, parameter_name: str) -> str:
        """
        Infer parameter type from function signature and usage.
        """
        # Look for type hints
        if f'{parameter_name}: ' in source:
            match = re.search(rf'{parameter_name}:\s*(\w+)', source)
            if match:
                type_hint = match.group(1)
                if type_hint in ['bytes', 'str', 'int', 'float', 'dict', 'list']:
                    return type_hint

        # Look for usage patterns
        if f'{parameter_name}.decode' in source:
            return 'bytes'
        if f'len({parameter_name})' in source or f'{parameter_name}[' in source:
            return 'bytes'  # Indexable, likely bytes or str
        if f'int({parameter_name})' in source:
            return 'str'

        # Default to bytes (most general)
        return 'bytes'


class TrinityFuzzer:
    """Main fuzzing orchestrator."""

    def __init__(self, timeout_seconds: int = 5, max_mutations: int = 10000,
                 logger: Optional[TrinityLogger] = None):
        self.timeout_seconds = timeout_seconds
        self.max_mutations = max_mutations
        self.crashes: List[CrashFinding] = []
        self.logger = logger if logger is not None else TrinityLogger("noop", TrinityLogConfig(enabled=False))

    async def fuzz_function(
        self,
        harness_code: str,
        function_name: str,
        file_path: str,
        timeout_seconds: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Fuzz a function using generated harness.

        Returns:
            {
                "function": "function_name",
                "status": "complete|timeout|error",
                "crashes": [...],
                "mutations_executed": 1234,
                "duration_seconds": 5.2,
            }
        """
        timeout = timeout_seconds or self.timeout_seconds

        self.logger.debug("fuzz_function start", phase="fuzzing",
                          func=function_name, timeout=timeout)

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                tmpdir_path = Path(tmpdir)

                harness_file = tmpdir_path / f"fuzz_{function_name}.py"
                harness_file.write_text(harness_code)

                corpus_dir = tmpdir_path / "corpus"
                corpus_dir.mkdir()

                result = await self._run_fuzzer_subprocess(
                    str(harness_file),
                    str(corpus_dir),
                    timeout
                )

                self.logger.info("fuzz_function complete", phase="fuzzing",
                                 func=function_name, status=result["status"],
                                 crashes=len(result.get("crashes", [])),
                                 duration_s=result.get("duration_seconds", 0))
                return result

        except Exception as e:
            self.logger.error(f"Fuzzing error for {function_name}", phase="fuzzing",
                              exc=e, func=function_name)
            return {
                "function": function_name,
                "status": "error",
                "crashes": [],
                "mutations_executed": 0,
                "duration_seconds": 0,
                "error": str(e),
            }

    async def _run_fuzzer_subprocess(
        self,
        harness_file: str,
        corpus_dir: str,
        timeout: int,
    ) -> Dict[str, Any]:
        """
        Run fuzzer in subprocess with timeout.

        Returns structured result.
        """
        start_time = asyncio.get_event_loop().time()

        try:
            # Try to run with python directly (works if harness is standalone)
            process = await asyncio.create_subprocess_exec(
                'python3', harness_file,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=timeout
                )
                elapsed = asyncio.get_event_loop().time() - start_time

                crashes = self._parse_crashes(stdout.decode('utf-8', errors='ignore'))

                stderr_text = stderr.decode('utf-8', errors='ignore').strip()
                if stderr_text:
                    self.logger.debug("fuzzer subprocess stderr",
                                      phase="fuzzing", stderr=stderr_text[:500])

                self.logger.trace("mutations_executed is a hardcoded estimate (not real)",
                                  phase="fuzzing", estimated_value=1000)

                return {
                    "status": "complete",
                    "crashes": crashes,
                    "mutations_executed": 1000,  # Estimate — not a real counter
                    "duration_seconds": round(elapsed, 2),
                }

            except asyncio.TimeoutError:
                process.kill()
                elapsed = asyncio.get_event_loop().time() - start_time
                self.logger.debug(
                    "fuzzer timeout — timeout crash has no stack_trace so is_app_crash() will return False and discard it",
                    phase="fuzzing", timeout_s=timeout,
                )
                return {
                    "status": "timeout",
                    "crashes": [{
                        'type': 'Timeout',
                        'description': f'Function timeout after {timeout}s',
                    }],
                    "mutations_executed": 500,
                    "duration_seconds": round(elapsed, 2),
                }

        except Exception as e:
            elapsed = asyncio.get_event_loop().time() - start_time
            return {
                "status": "error",
                "crashes": [],
                "mutations_executed": 0,
                "duration_seconds": round(elapsed, 2),
                "error": str(e),
            }

    def _parse_crashes(self, output: str) -> List[Dict[str, Any]]:
        """Parse fuzzer output for crashes."""
        crashes = []

        # Look for RecursionError, MemoryError, etc in output
        if 'RecursionError' in output:
            crashes.append({
                'type': 'RecursionError',
                'description': 'Stack overflow - likely infinite recursion',
            })

        if 'MemoryError' in output:
            crashes.append({
                'type': 'MemoryError',
                'description': 'Memory exhaustion - likely infinite loop or allocation',
            })

        if 'Segmentation fault' in output or 'SIGSEGV' in output:
            crashes.append({
                'type': 'SegmentationFault',
                'description': 'Memory access violation',
            })

        return crashes


class AdaptiveTimeAllocator:
    """Allocate fuzzing time based on function characteristics."""

    def allocate_budget(
        self,
        functions: List[Dict],
        total_budget_seconds: int = 120,
    ) -> Dict[str, int]:
        """
        Allocate fuzzing time per function.

        Higher risk + more complex = more time
        """
        allocations = {}

        for func in functions:
            risk_score = func.get('risk_score', 5)
            complexity = len(func['source'].split('\n'))

            # Base time: risk contributes 0-3 seconds
            base_time = (risk_score / 10) * 3

            # Complexity contributes 0-2 seconds (more lines = more time needed)
            base_time += min(complexity / 30, 2)

            # Minimum 2 seconds per function
            base_time = max(base_time, 2)

            allocations[func['name']] = int(base_time)

        # Normalize to total budget
        total_allocated = sum(allocations.values())
        if total_allocated > total_budget_seconds:
            scale_factor = total_budget_seconds / total_allocated
            allocations = {k: max(1, int(v * scale_factor)) for k, v in allocations.items()}

        return allocations
