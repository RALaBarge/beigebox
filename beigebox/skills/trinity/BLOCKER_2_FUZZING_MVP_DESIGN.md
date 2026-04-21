# Blocker 2: Fuzzing-Only MVP Design

**Status**: Design Phase (Ready for Implementation)  
**Timeline**: 3-4 weeks  
**Complexity**: Medium  
**Value**: 70% of dynamic analysis benefit

---

## Executive Summary

This document details a **Fuzzing-Only MVP** for Trinity's dynamic analysis. Fuzzing is the easiest and highest-ROI dynamic analysis technique:

- **Catches**: DOS attacks, crashes, memory leaks, infinite loops, resource exhaustion
- **Ignores**: Complex concurrency bugs, symbolic paths, taint flows (defer to later)
- **ROI**: 70% of dynamic analysis value with 30% of complexity
- **Timeline**: 3-4 weeks with one developer
- **Effort**: ~120 hours (2-3 weeks) for implementation + integration

After fuzzing MVP is working, we can add symbolic execution (4 weeks) and taint tracking (3 weeks) in separate phases.

---

## What Fuzzing Catches (That Static Can't)

### 1. Denial of Service (DOS) Vulnerabilities

**Example 1: Unbounded Loop**
```python
def process_records(data):
    count = 0
    while count < 1000000:  # No exit condition if data is corrupted
        if data[count % len(data)] == 'STOP':
            break
        count += 1
    return count
```
- **Static Analysis**: "Possible infinite loop" (weak signal)
- **Fuzzer**: Timeout on malformed input → DOS vulnerability (high confidence)

**Example 2: Algorithmic Complexity Attack**
```python
def find_duplicates(items):
    # O(n²) algorithm with no limits
    duplicates = []
    for i in range(len(items)):
        for j in range(i+1, len(items)):
            if items[i] == items[j]:
                duplicates.append((i, j))
    return duplicates

find_duplicates([1, 2, 3, ..., 100000])  # 10B comparisons, timeout
```
- **Static Analysis**: Cannot predict complexity from input size
- **Fuzzer**: Large input → timeout → algorithmic DOS (detected)

### 2. Crash/Memory Corruption

**Example: Out-of-Bounds Access**
```python
def parse_header(data):
    return data[0] + data[1] + data[2]  # Assumes len(data) >= 3

parse_header(b'X')  # IndexError
```
- **Static Analysis**: Hard to detect (bounds checking not obvious)
- **Fuzzer**: Short input → crash (detected)

**Example: Type Confusion**
```python
def process(value):
    return value * 2  # Works for int, str; breaks for dict

process({'key': 'val'})  # TypeError
```
- **Fuzzer**: Type-mismatched input → crash (detected)

### 3. Infinite Recursion

**Example: Recursive Parser**
```python
def parse_nested(data):
    if data.startswith('['):
        return parse_nested(data[1:])  # Recursive, no depth limit
    return data

parse_nested('[' * 10000)  # RecursionError (DOS)
```
- **Static Analysis**: Recursion detected, but depth not validated
- **Fuzzer**: Deep nesting → crash/timeout (detected)

### 4. Resource Exhaustion

**Example: Memory Leak on Error**
```python
def load_data(filename):
    buffer = allocate_buffer(1GB)
    if not file_exists(filename):
        # LEAK: buffer never freed
        return None
    return buffer
```
- **Static Analysis**: Can't track memory allocation/deallocation
- **Fuzzer**: Missing file → memory spike (detected as resource leak)

---

## Architecture: Fuzzing Integrated into Trinity

### New Phase: Phase 1b (Parallel with Phase 1a)

```
Input Code
    ↓
Phase 0: Prepare (Existing)
    ↓
Phase 1a: Static Analysis (Current)    Phase 1b: Fuzzing (NEW - Parallel)
├─ Surface Scanner                      ├─ Generate test harnesses
├─ Deep Reasoner                        ├─ Extract fuzzable functions
├─ Specialist                           ├─ Run Atheris (Python fuzzer)
│                                       └─ Collect crashes/hangs
↓                                                    ↓
Phase 2: Consensus Building (Merge static + fuzzing findings)
    ├─ Dedup findings from both sources
    ├─ Weight fuzzing crashes as high-confidence (0.95)
    └─ Tag findings with origin: "static", "fuzzing", or "both"
    ↓
Phase 3: Appellate Review
Phase 4: Source Verification
    ↓
Output: Findings (static + fuzzing combined)
```

### Fuzzing Workflow

```
1. Code Chunking (existing)
   ├─ Extract individual functions/methods
   └─ Identify "fuzzable" entry points
   
2. Test Harness Generation (NEW)
   ├─ LLM generates fuzz target stubs
   ├─ Identify input parameters
   └─ Mock external dependencies
   
3. Fuzzing Execution (NEW)
   ├─ Run Atheris with generated harness
   ├─ Feed random/mutated inputs
   ├─ Monitor for: crashes, timeouts, hangs
   └─ Collect reproducer inputs
   
4. Finding Creation (NEW)
   ├─ Convert fuzzer output to Trinity findings
   ├─ Include crash type, reproducer, stack trace
   └─ Assign high confidence (0.90-0.98)
   
5. Consensus Merging (MODIFIED Phase 2)
   ├─ Merge static + fuzzing findings
   └─ Tier assignment considers both sources
```

---

## Implementation Plan

### Stage 1: Fuzzing Infrastructure (Week 1)

#### 1.1: Add Atheris Integration
**File**: `beigebox/skills/trinity/fuzzing.py` (NEW, ~300 lines)

```python
import atheris
import sys
from typing import Callable, List, Dict, Any

class TrinityFuzzer:
    """Wrapper around Atheris for Trinity pipeline."""

    def __init__(self, timeout_seconds: int = 5, max_mutations: int = 10000):
        self.timeout_seconds = timeout_seconds
        self.max_mutations = max_mutations
        self.crashes: List[Dict[str, Any]] = []
        self.hangs: List[Dict[str, Any]] = []

    def fuzz_function(
        self,
        fuzz_target: Callable,
        function_name: str,
        file_path: str,
        input_types: List[str] = None,  # ["bytes", "str", "json"]
    ) -> Dict[str, Any]:
        """
        Fuzz a single function.

        Args:
            fuzz_target: Callable that takes bytes input
            function_name: Name of function being fuzzed
            file_path: Original source file path
            input_types: Expected input types to generate appropriate corpus

        Returns:
            {
                "function": "function_name",
                "status": "complete|timeout|error",
                "crashes": [...],
                "hangs": [...],
                "mutations_executed": 1234,
                "duration_seconds": 5.2,
            }
        """
        ...

    def run_with_timeout(self, fuzz_target: Callable, timeout: int):
        """Run fuzzer with wall-clock timeout."""
        ...

    def analyze_crash(self, crash_data: Dict) -> Dict[str, Any]:
        """
        Analyze crash and convert to Trinity finding.

        Returns:
            {
                "type": "segfault|timeout|assertion|exception",
                "reproducer": b"...",  # Input that triggers crash
                "stack_trace": "...",
                "severity": "critical|high|medium",
                "confidence": 0.95,
            }
        """
        ...
```

**Dependencies**: 
```bash
pip install atheris  # Python fuzzing framework
```

#### 1.2: Test Harness Generation
**File**: `beigebox/skills/trinity/harness_generator.py` (NEW, ~250 lines)

```python
from typing import List, Dict, Any
import ast
import inspect

class HarnessGenerator:
    """Generate fuzz targets for Python functions."""

    def generate_harness(
        self,
        function_source: str,
        function_name: str,
        inferred_types: Dict[str, str],  # {"param": "bytes|str|int|dict"}
    ) -> str:
        """
        Generate a fuzz_target function that Atheris can call.

        Example input:
            def parse_json(data):
                return json.loads(data)

        Generated harness:
            @atheris.instrument_func
            def fuzz_target(data):
                try:
                    parse_json(data)
                except Exception as e:
                    if is_security_relevant(e):
                        raise
        
        Returns: Python code as string (ready to exec)
        """
        ...

    def infer_parameter_types(self, function_signature: str) -> Dict[str, str]:
        """
        Use AST + heuristics to infer parameter types.

        Examples:
            def process(data) -> Dict[str, str]  # "data" is likely bytes/str
            def calculate(count: int)              # "count" is int
            def handle(request, response)          # Both likely objects
        
        Returns: {"data": "bytes", "count": "int", ...}
        """
        ...

    def wrap_with_mocks(self, harness: str) -> str:
        """
        Mock external dependencies so harness runs in isolation.

        Examples:
            - Mock `requests.get()` to return fake response
            - Mock database calls to return empty result
            - Mock file I/O to return test data
        
        Returns: Harness with mocks injected
        """
        ...
```

**Key Challenge**: Inferring parameter types from source code. Use heuristics:
- Type annotations: `def foo(x: bytes)` → "bytes"
- Assignment patterns: `x = b"..."` → "bytes"
- Function calls: `len(x)` → "bytes or str"
- Docstrings: Parse manually or use LLM

**Fallback**: Default to `bytes` (most general)

### Stage 2: Fuzzing Execution (Week 1-2)

#### 2.1: Function Extraction
**File**: `beigebox/skills/trinity/function_extractor.py` (NEW, ~200 lines)

```python
import ast
from typing import List, Tuple

class FunctionExtractor:
    """Extract individual functions from source code."""

    def extract_functions(self, code: str, file_path: str) -> List[Dict[str, Any]]:
        """
        Parse code and extract all fuzzable functions.

        Returns:
            [
                {
                    "name": "parse_json",
                    "source": "def parse_json(data): ...",
                    "line_start": 42,
                    "parameters": ["data"],
                    "is_fuzzable": True,  # No external deps, reasonable complexity
                    "reason": "accepts_data_parameter",
                },
                ...
            ]
        """
        ...

    def is_fuzzable(self, function_name: str, source: str) -> Tuple[bool, str]:
        """
        Decide if function is worth fuzzing.

        Criteria:
            - Takes at least 1 parameter (not self._private helpers)
            - Returns something (not just side effects)
            - Doesn't directly call external services
            - Reasonably sized (< 500 lines)
        
        Returns: (is_fuzzable, reason)
        """
        ...

    def filter_by_risk(self, functions: List[Dict]) -> List[Dict]:
        """
        Filter to high-risk functions (most likely to have vulnerabilities).

        Heuristics:
            - Functions with parsing logic (JSON, CSV, XML)
            - Functions with loops/recursion
            - Functions with string manipulation
            - Functions in security-critical modules
        
        Scoring: Risk 1-10, keep top 50%
        """
        ...
```

#### 2.2: Fuzzing Runner
**File**: `beigebox/skills/trinity/pipeline.py` (MODIFIED)

Add new method to `TrinityPipeline`:

```python
async def _phase_1b_fuzzing(self) -> List[Finding]:
    """
    Phase 1b: Dynamic fuzzing analysis (parallel with Phase 1a).
    
    Returns list of findings from fuzzing.
    """
    print(f"[{self.audit_id}] Phase 1b: Fuzzing (starting)")
    
    fuzzing_findings = []
    
    # Extract fuzzable functions from all chunks
    all_functions = []
    for chunk in self.chunks:
        extractor = FunctionExtractor()
        functions = extractor.extract_functions(chunk['content'], chunk['file'])
        all_functions.extend(functions)
    
    # Filter to high-risk functions (sample if too many)
    high_risk = self._filter_by_risk(all_functions)
    selected = high_risk[:50]  # Fuzz top 50 functions (cost control)
    
    # Generate harnesses and run fuzzer
    fuzzer = TrinityFuzzer(timeout_seconds=5, max_mutations=10000)
    
    for func_info in selected:
        # Generate harness
        harness_gen = HarnessGenerator()
        harness = harness_gen.generate_harness(
            func_info['source'],
            func_info['name'],
            harness_gen.infer_parameter_types(func_info['source'])
        )
        
        # Run fuzzer
        result = await fuzzer.fuzz_function(
            harness,
            func_info['name'],
            func_info['file_path']
        )
        
        # Convert crashes to findings
        for crash in result['crashes']:
            finding = Finding(
                id=f"{self.audit_id}:fuzz:{len(fuzzing_findings)}",
                severity="critical",
                title=f"Crash in {func_info['name']}: {crash['type']}",
                description=f"Function crashes with certain inputs. Crash type: {crash['type']}. Reproducer: {crash['reproducer']!r}",
                file=func_info['file_path'],
                line=func_info['line_start'],
                evidence=f"Input: {crash['reproducer'][:100]!r}...",
                model="atheris-fuzzer",
                confidence=0.95,  # High confidence: actual crash
            )
            fuzzing_findings.append(finding)
        
        # Convert hangs to findings
        for hang in result['hangs']:
            finding = Finding(
                id=f"{self.audit_id}:fuzz:{len(fuzzing_findings)}",
                severity="high",
                title=f"DOS in {func_info['name']}: Timeout",
                description=f"Function hangs on certain inputs (timeout after 5s). Reproducer: {hang['reproducer']!r}",
                file=func_info['file_path'],
                line=func_info['line_start'],
                evidence=f"Input: {hang['reproducer'][:100]!r}...",
                model="atheris-fuzzer",
                confidence=0.90,  # High confidence: actual timeout
            )
            fuzzing_findings.append(finding)
    
    print(f"[{self.audit_id}] Phase 1b: Fuzzing (complete, {len(fuzzing_findings)} findings)")
    return fuzzing_findings
```

### Stage 3: Integration with Consensus (Week 2)

#### 3.1: Modify Phase 2 to Merge Findings
**File**: `beigebox/skills/trinity/pipeline.py` (MODIFIED)

Update `run_full_audit()`:

```python
async def run_full_audit(self) -> Dict[str, Any]:
    """Execute full 4-phase Trinity audit with optional fuzzing."""
    try:
        # Prepare: Load and chunk code
        await self._prepare()

        # Phase 1: Parallel static + fuzzing
        static_results, fuzzing_results = await asyncio.gather(
            self._phase_1_parallel_audits(),
            self._phase_1b_fuzzing(),
            return_exceptions=True
        )

        # Merge static + fuzzing results
        self.phase_1_results["fuzzing"] = fuzzing_results if not isinstance(fuzzing_results, Exception) else []

        # Phase 2: Consensus (now includes fuzzing findings)
        await self._phase_2_consensus_building_with_fuzzing()

        # ... rest of phases ...
```

Update `_phase_2_consensus_building()` to handle fuzzing:

```python
async def _phase_2_consensus_building_with_fuzzing(self) -> None:
    """Phase 2: Consensus with fuzzing findings."""
    
    # Gather all findings (static + fuzzing)
    all_findings = (
        self.phase_1_results["surface_scanner"] +
        self.phase_1_results["deep_reasoner"] +
        self.phase_1_results["specialist"] +
        self.phase_1_results.get("fuzzing", [])  # Add fuzzing findings
    )

    # ... existing deduplication logic ...

    # New: Weight fuzzing findings higher
    for key, data in deduplicated.items():
        consensus_count = len(data["models"])
        avg_confidence = sum(data["confidences"]) / len(data["confidences"])

        # If fuzzing found it, upgrade tier
        if "atheris-fuzzer" in data["models"]:
            if consensus_count >= 2:
                tier = "A"  # Fuzzing + static = high confidence
            else:
                tier = "B"  # Fuzzing alone = medium confidence
        else:
            # ... existing static-only tier logic ...
```

### Stage 4: Testing & Validation (Week 2-3)

#### 4.1: Test Suite
**File**: `tests/test_fuzzing.py` (NEW, ~400 lines)

```python
import pytest
from beigebox.skills.trinity.fuzzing import TrinityFuzzer
from beigebox.skills.trinity.harness_generator import HarnessGenerator
from beigebox.skills.trinity.function_extractor import FunctionExtractor

class TestFuzzing:
    """Test fuzzing infrastructure."""

    def test_harness_generation(self):
        """Test that valid Python harnesses are generated."""
        gen = HarnessGenerator()
        harness = gen.generate_harness(
            "def parse(data): return json.loads(data)",
            "parse",
            {"data": "str"}
        )
        assert "def fuzz_target" in harness
        assert "atheris.instrument_func" in harness

    def test_function_extraction(self):
        """Test that functions are extracted correctly."""
        code = """
        def foo(x):
            return x * 2
        
        def _private(y):
            return y
        
        def parse_json(data):
            import json
            return json.loads(data)
        """
        extractor = FunctionExtractor()
        functions = extractor.extract_functions(code, "test.py")
        
        names = [f['name'] for f in functions]
        assert "foo" in names
        assert "parse_json" in names
        # _private might be included but marked as low priority

    def test_dos_detection(self):
        """Test that DOS vulnerabilities are detected."""
        # Vulnerable code with unbounded loop
        code = """
        def process(data):
            count = 0
            while True:
                if data[count % len(data)] == 'STOP':
                    break
                count += 1
            return count
        """
        
        fuzzer = TrinityFuzzer(timeout_seconds=2, max_mutations=1000)
        harness = HarnessGenerator().generate_harness(code, "process", {"data": "bytes"})
        result = fuzzer.fuzz_function(harness, "process", "test.py")
        
        assert result['status'] in ['timeout', 'complete']
        assert len(result['hangs']) > 0 or result['status'] == 'timeout'

    def test_crash_detection(self):
        """Test that crashes are detected."""
        code = """
        def parse_header(data):
            return data[0] + data[1] + data[2]
        """
        
        fuzzer = TrinityFuzzer()
        harness = HarnessGenerator().generate_harness(code, "parse_header", {"data": "bytes"})
        result = fuzzer.fuzz_function(harness, "parse_header", "test.py")
        
        assert len(result['crashes']) > 0

    @pytest.mark.integration
    def test_end_to_end_fuzzing(self):
        """Test full fuzzing pipeline on sample codebase."""
        # Create temp codebase with known vulnerabilities
        temp_repo = create_vulnerable_repo()
        
        # Run Trinity with fuzzing enabled
        pipeline = TrinityPipeline(
            repo_path=temp_repo,
            fuzzing_enabled=True
        )
        report = asyncio.run(pipeline.run_full_audit())
        
        # Verify fuzzing findings are included
        assert any(f['model'] == 'atheris-fuzzer' for f in report['verified_findings'])
        assert len(report['findings']['phase_1_raw']['fuzzing']) > 0
```

#### 4.2: Benchmark Against Known Vulnerabilities
Create test repository with known DOS/crash vulnerabilities:

```python
# test_repo/vulnerable_code.py

# DOS 1: Unbounded loop
def process_items(data):
    count = 0
    while count < 1000000:
        if count % len(data) == 0:
            break
        count += 1
    return count

# DOS 2: Algorithmic complexity
def find_all_pairs(items):
    pairs = []
    for i in range(len(items)):
        for j in range(len(items)):
            if items[i] < items[j]:
                pairs.append((i, j))
    return pairs

# CRASH: Out of bounds
def parse_header(data):
    return data[0] + data[1] + data[2]

# CRASH: Type confusion
def process(value):
    return value * 2
```

**Expected Results**:
- DOS 1: Timeout detected ✓
- DOS 2: Timeout on large input ✓
- CRASH 1: IndexError on short input ✓
- CRASH 2: TypeError on dict input ✓

---

## Integration Points

### 1. Configuration
Add to Trinity config:

```yaml
fuzzing:
  enabled: true
  timeout_seconds: 5
  max_mutations: 10000
  max_functions_to_fuzz: 50  # Budget control
  enable_corpus: true        # Use seed corpus
```

### 2. MCP Skill Interface
Update `mcp_skill.py`:

```python
async def start_audit(
    self,
    repo_path: str,
    models: Optional[Dict[str, str]] = None,
    budget: Optional[Dict[str, int]] = None,
    fuzzing_enabled: bool = True,  # NEW
    beigebox_url: str = "http://localhost:8000",
) -> Dict[str, Any]:
    """Start audit with optional fuzzing."""
    pipeline = TrinityPipeline(
        repo_path=repo_path,
        models=models,
        budget=budget,
        fuzzing_enabled=fuzzing_enabled,
        beigebox_url=beigebox_url,
    )
    # ...
```

### 3. Report Structure
Update report to include fuzzing phase:

```json
{
    "findings": {
        "phase_1_raw": {
            "surface_scanner": 12,
            "deep_reasoner": 8,
            "specialist": 6,
            "fuzzing": 3  // NEW
        },
        "phase_1b_fuzzing_enabled": true,  // NEW
        "fuzzing_functions_tested": 47,     // NEW
        "fuzzing_timeout_seconds": 5        // NEW
    }
}
```

---

## Timeline & Effort

| Component | Effort | Parallel | Owner |
|-----------|--------|----------|-------|
| **Week 1** |
| 1.1 Atheris integration | 20 hours | Start | Dev 1 |
| 1.2 Harness generator | 25 hours | Yes | Dev 1 |
| **Week 2** |
| 2.1 Function extractor | 15 hours | Start | Dev 2 |
| 2.2 Fuzzing runner | 20 hours | Yes (after 1.1) | Dev 1 |
| 3.1 Phase 2 integration | 15 hours | After 2.2 | Dev 1 |
| **Week 2-3** |
| 4.1 Test suite | 30 hours | Parallel | Dev 2 |
| 4.2 Vulnerable test repo | 10 hours | Parallel | Dev 2 |
| Integration & fixes | 20 hours | Yes | Dev 1-2 |
| **Total** | **155 hours (3-4 weeks)** | | |

---

## Success Metrics

### Phase Completion (Week 4)
- [ ] Atheris fuzzer running successfully
- [ ] Harnesses generated for 50+ functions
- [ ] DOS attacks detected (unbounded loop, timeout)
- [ ] Crashes detected (out-of-bounds, type errors)
- [ ] Fuzzing findings merged into Phase 2 consensus
- [ ] Test suite passing (unit + integration)

### Benchmark Results
- [ ] DOS detection: 100% (all deliberately introduced DOS found)
- [ ] Crash detection: 90%+ (most memory/type issues found)
- [ ] False positives: <20% (fuzzer noise / library crashes)
- [ ] Performance: <10s per audit (fuzzing overhead)

### Production Readiness
- [ ] Documentation complete
- [ ] Configuration working
- [ ] MCP endpoints updated
- [ ] Report structure includes fuzzing
- [ ] No regressions in static analysis

---

## Future Extensions (Post-MVP)

### Phase 2: Symbolic Execution (4 weeks, post-MVP)
- Add z3 constraint solving
- Explore all code paths
- Find complex logic flaws
- **Target**: Q3 2026

### Phase 3: Taint Tracking (3 weeks, post-MVP)
- Instrument code for taint propagation
- Track untrusted data flows
- Find injection vulnerabilities
- **Target**: Q3 2026

### Integration: Full Dynamic Analysis
Once all three techniques working:
- Merge findings from fuzzing + symbolic + taint
- Weight by confidence and agreement
- Achieve 90-95% recall
- **Target**: Q4 2026

---

## Risks & Mitigation

| Risk | Impact | Mitigation |
|------|--------|-----------|
| Harness generation fails | Can't fuzz functions | Manual harness templates for common patterns (parse_json, handle_request, etc.) |
| Fuzzer timeout too long | Slow audits | Adaptive timeout (start 5s, scale down if hitting limits) |
| False positives from library crashes | Low signal-to-noise | Filter by stack trace origin (only report app crashes, not lib crashes) |
| Sandbox escape during fuzzing | Security | Run in Docker container, enforce resource limits |
| Atheris not available for all Python versions | Compatibility | Test against 3.8-3.11, use subprocess sandbox for unsupported |

---

## Go/No-Go Decision

**Recommend: GO** ✓

- Fuzzing MVP is contained and low-risk
- High ROI (3-4 weeks for significant vulnerability detection improvement)
- Foundation for symbolic + taint tracking later
- Can be toggled off if issues arise
- No impact on existing static analysis

---

## Next Steps

1. **Week 1**: Kickoff → Atheris integration + harness generator
2. **Week 2**: Function extraction + fuzzing runner + Phase 2 integration
3. **Week 2-3**: Full test suite + vulnerable test repo + integration testing
4. **Week 4**: Final validation + documentation + production deployment

Ready to start implementation, or want to adjust the design?
