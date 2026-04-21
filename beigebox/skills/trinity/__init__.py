"""
Trinity Audit Framework - Multi-Model Adversarial Code Analysis.

A production-grade security audit methodology that combines 3+ independent LLM models
to find vulnerabilities with high confidence through consensus-based validation.

## Quick Start

```python
from beigebox.skills.trinity.mcp_skill import get_trinity_skill

skill = get_trinity_skill()

# Start an audit (returns immediately with audit_id)
result = await skill.start_audit(
    repo_path="/path/to/code",
    budget={"surface": 15000, "deep": 40000, "specialist": 20000, "appellate": 25000}
)
audit_id = result["audit_id"]

# Poll for status
status = await skill.get_audit_status(audit_id)
print(status)  # {"status": "running", "phase_1_findings": 12, ...}

# Get results when complete
results = await skill.get_audit_result(audit_id)
print(results)  # Full audit report with findings
```

## Architecture

### Phase 1: Parallel Independent Audits
Three independent stacks run in parallel:
- **Surface Scanner (Haiku)**: Fast pattern matching (~45s per chunk)
  - SQL injection, XSS, hardcoded secrets, input validation gaps
- **Deep Reasoner (Grok 4.1 Fast)**: Complex reasoning (~120s per chunk)
  - Logic flaws, race conditions, state machines, authorization bugs
- **Specialist (Arcee Trinity Large)**: Domain-specific patterns (~60s per chunk)
  - Language/domain expertise for harder-to-spot issues

### Phase 2: Consensus Building
- Deduplication: merge identical findings
- Cross-stack grading: how many stacks independently flagged each finding?
- Confidence tiers:
  - **Tier A**: All 3 stacks agree (high confidence)
  - **Tier B**: 2 of 3 stacks agree (medium confidence)
  - **Tier C**: 1 stack flagged (low confidence, needs review)

### Phase 3: Appellate Review
- Independent model (Qwen/Deepseek) reviews Phase 2 findings
- Challenges: is each finding internally coherent? Does evidence support claim?
- Adjusts confidence scores based on appellate reasoning

### Phase 4: Source Verification
- Ground every finding in actual source code
- Extract file:line citations
- Verify code paths exist
- Build reproducibility instructions

## Configuration

### Default Models
```python
models = {
    "surface": "haiku",                  # Claude Haiku (direct Anthropic)
    "deep": "grok-4.1-fast",             # via OpenRouter/BeigeBbox
    "specialist": "arcee-trinity-large", # via OpenRouter/BeigeBbox
    "appellate": "qwen-max",             # via OpenRouter/BeigeBbox (different from Phase 1)
}
```

All models can be overridden at runtime:
```python
custom_models = {
    "surface": "haiku",
    "deep": "claude-opus-4-7",
    "specialist": "deepseek-coder",
    "appellate": "claude-sonnet-4-6",
}
result = await skill.start_audit("/path/to/code", models=custom_models)
```

### Token Budgets
```python
budget = {
    "surface": 15000,      # Surface Scanner (fast, cheap)
    "deep": 40000,         # Deep Reasoner (expensive, thorough)
    "specialist": 20000,   # Specialist (medium)
    "appellate": 25000,    # Appellate review (medium-expensive)
}
```

### Code Handling
- **Chunking**: Sliding window (4000 tokens, 500 token overlap)
- **Privacy**: Respects .gitignore strictly — no excluded files sent to LLMs
- **Context**: Full repository context available (all chunks cover all code)

## Output Format

```json
{
  "audit_id": "trinity-abc123",
  "status": "complete",
  "timing": {
    "started_at": "2026-04-20T18:30:00",
    "elapsed_seconds": 487.2
  },
  "code_metrics": {
    "total_files": 47,
    "total_chunks": 230,
    "total_tokens": 98765
  },
  "findings": {
    "phase_1_raw": {
      "surface_scanner": 45,
      "deep_reasoner": 38,
      "specialist": 31
    },
    "phase_2_consensus": 24,
    "phase_3_reviewed": 20,
    "phase_4_verified": 18
  },
  "verified_findings": [
    {
      "id": "F001",
      "severity": "critical",
      "title": "Arbitrary SQL Execution",
      "description": "User input concatenated into SQL query",
      "file": "src/app.py",
      "line": 142,
      "evidence": "query = f'SELECT * FROM users WHERE id = {user_id}'",
      "model": "grok-4.1-fast",
      "confidence": 0.92
    },
    ...
  ],
  "consensus_findings": [
    {
      "finding": {...},
      "consensus_tier": "A",
      "consensus_confidence": 0.92,
      "agreement_count": 3,
      "agreeing_models": ["haiku", "grok-4.1-fast", "arcee-trinity-large"]
    },
    ...
  ],
  "audit_log": [...]
}
```

## Integration with MCP Server

Add to `beigebox/mcp_server.py`:

```python
from beigebox.skills.trinity.mcp_skill import get_trinity_skill

# In tool registration:
async def handle_trinity_audit(params: Dict) -> Dict:
    skill = get_trinity_skill()
    return await skill.start_audit(
        repo_path=params["repo_path"],
        budget=params.get("budget"),
        models=params.get("models"),
    )

async def handle_trinity_status(params: Dict) -> Dict:
    skill = get_trinity_skill()
    return await skill.get_audit_status(params["audit_id"])

async def handle_trinity_result(params: Dict) -> Dict:
    skill = get_trinity_skill()
    return await skill.get_audit_result(params["audit_id"])
```

Then expose via MCP:
```json
{
  "tools": [
    {
      "name": "trinity_audit",
      "description": "Start a Trinity security audit of a repository",
      "input_schema": {
        "repo_path": {"type": "string"},
        "models": {"type": "object", "optional": true},
        "budget": {"type": "object", "optional": true}
      }
    },
    {
      "name": "trinity_status",
      "description": "Check audit status",
      "input_schema": {
        "audit_id": {"type": "string"}
      }
    },
    {
      "name": "trinity_result",
      "description": "Get completed audit results",
      "input_schema": {
        "audit_id": {"type": "string"}
      }
    }
  ]
}
```

## Performance

For a typical codebase (~500-2000 LOC):
- **Phase 1**: 3-5 min (surface, deep, specialist run in parallel)
- **Phase 2**: 1-2 min (consensus building)
- **Phase 3**: 2-4 min (appellate review)
- **Phase 4**: 1-2 min (source verification)
- **Total**: ~8-15 minutes

Token usage:
- Surface: ~3-5K tokens per run
- Deep: ~8-15K tokens per run
- Specialist: ~4-8K tokens per run
- Appellate: ~5-10K tokens per run
- **Total**: ~25-50K tokens per audit

## Extensibility

Add custom models at runtime:
```python
from beigebox.skills.trinity.model_router import ModelConfig

skill = get_trinity_skill()
skill.router.register_model(
    "claude-sonnet-4-6",
    ModelConfig(
        name="Claude Sonnet",
        provider="openrouter",
        model_id="claude/claude-sonnet-4-6",
        route_via_beigebox=True,
    )
)
```

## References

- **Design**: See `DESIGN.md` for full methodology specification
- **Paper**: "Adversarial Security Audit" v1.43 (Trinity Pipeline section)
- **Implementation**: `pipeline.py` contains all 4 phases

---

**Status**: Production-ready
**Last Updated**: April 20, 2026
**Owner**: BeigeBox Security Team
"""

from .mcp_skill import get_trinity_skill, TrinityMCPSkill
from .pipeline import TrinityPipeline
from .chunker import TrinityChunker
from .model_router import TrinityModelRouter
from .logger import TrinityLogger, TrinityLogConfig, TrinityLogLevel

__all__ = [
    "get_trinity_skill",
    "TrinityMCPSkill",
    "TrinityPipeline",
    "TrinityChunker",
    "TrinityModelRouter",
    "TrinityLogger",
    "TrinityLogConfig",
    "TrinityLogLevel",
]
