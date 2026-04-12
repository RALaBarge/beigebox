# AI Security Gaps: Threat Landscape, Tooling Analysis, and BeigeBox Strategy

**Compiled:** April 2026  
**Scope:** AI-specific security threats, tooling ecosystem gaps, and actionable build priorities for BeigeBox

---

## 1. Threat Landscape: 15 AI-Specific Threats

These are threats specific to AI/LLM systems — distinct from traditional network security. Ranked roughly by exploitation frequency in 2025-2026.

---

### T1 — Direct Prompt Injection (OWASP LLM01:2025)

**What it is:** Attacker-supplied input overrides system instructions, alters LLM behavior, or extracts information the system was not meant to reveal.

**Current exploitation:** Remain the #1 critical vulnerability. Techniques include:
- Obfuscation: typos, encoding, synonym substitution to evade regex-based filters
- Virtualization: framing malicious instructions as roleplay or hypotheticals
- Roleplay attacks: 89.6% success rate against production LLMs
- Multi-turn escalation: 97% success within 5 conversation turns

**Where BeigeBox is exposed:** Any request flowing through the proxy without guardrails enabled. The `guardrails.py` module has pattern-matching for ~12 injection signatures but these are trivially bypassed with obfuscation or encoding.

---

### T2 — Indirect Prompt Injection (OWASP LLM01:2025)

**What it is:** Malicious instructions embedded in external content (documents, web pages, tool outputs, retrieved RAG chunks) that the LLM processes and executes.

**Current exploitation:** Attackers target agentic pipelines. A compromised support ticket can embed SQL instructions that a Cursor agent executes (real incident: Supabase 2025). Image metadata can carry instructions invisible to users but read by vision models.

**Where BeigeBox is exposed:** The Operator agent reads workspace files, tool outputs, and retrieved documents. None of these sources are scanned for embedded instructions before being injected into context.

---

### T3 — Jailbreaking and Safety Bypass

**What it is:** Techniques that cause aligned models to violate their training-time safety constraints and produce harmful, restricted, or confidential output.

**Current exploitation:** Novel generation techniques being actively researched:
- "DAN" (Do Anything Now) style prompts
- Token-level manipulation exploiting tokenizer edge cases
- Gradient-based adversarial suffixes that transfer across models
- Multi-step context manipulation that gradually shifts model behavior

**Maturity of defenses:** Low. Character injection alone achieves high bypass rates against most production guardrails. Adversarial ML evasion further increases bypass rates. Academic paper (arxiv 2504.11168) demonstrated breaking all 12 leading defenses tested.

---

### T4 — RAG and Vector Database Poisoning (OWASP LLM08:2025)

**What it is:** Attacker injects malicious documents into a vector store; when retrieved by RAG, those documents carry instructions that the LLM executes.

**Current exploitation:** PoisonedRAG (USENIX Security 2025) demonstrated 90% attack success with just 5 poisoned texts. A single poisoned embedding can alter system behavior across multiple queries. Attack works without modifying weights or prompts — only the knowledge base.

**Where BeigeBox is exposed:** ChromaDB stores embeddings for semantic cache and document search. No validation is applied to documents before embedding. The `document_search` tool and `confluence_crawler` can ingest arbitrary external content.

---

### T5 — Model Extraction and Intellectual Property Theft (OWASP LLM10)

**What it is:** Systematic querying of an LLM API to reconstruct model behavior, extract training data, or recover proprietary system prompts through inference patterns.

**Attack categories:**
- Functional extraction: clone model behavior via API distillation
- Training data extraction: recover PII, rare sequences, private data from memorized examples
- Prompt inversion: reconstruct system prompt from model responses
- Parameter recovery: approximate weight values via output distribution analysis

**Where BeigeBox is exposed:** No per-session query volume tracking exists beyond the rate limiter in `auth.py`. No anomaly detection for unusual query diversity patterns that indicate extraction attempts. System prompts are potentially recoverable from context injection.

---

### T6 — Training Data Poisoning (OWASP LLM04:2025)

**What it is:** Adversarial examples injected into training datasets before or during model fine-tuning. Causes the model to produce attacker-controlled outputs for trigger inputs.

**Current exploitation:** Clean-data poisoning at 86% success rate (2025 research). Synthetic data pipelines are a new propagation vector — Virus Infection Attack (VIA) shows poisoned content spreading across model generations.

**Where BeigeBox is exposed:** If BeigeBox is used to curate fine-tuning datasets (via Operator scraping tools), poisoned web content can enter training pipelines. No data provenance tracking exists.

---

### T7 — Model Backdoors in Pre-trained Weights

**What it is:** Malicious behavior embedded in model weights during training or fine-tuning. Activated by a trigger phrase or token sequence invisible in normal operation.

**Current exploitation:** GGUF model files can embed Jinja2 templates that silently prepend malicious instructions to every prompt. Weight/quantization tampering can flip bits to embed rogue behaviors. Trigger-based backdoors preserve normal task performance, making detection difficult.

**Tooling state:** Microsoft released a backdoor scanner for open-weight LLMs in 2025 that analyzes how triggers influence internal state and output distributions. Still early-stage — no standard tooling or CI integration.

**Where BeigeBox is exposed:** Models pulled from Ollama registry are unsigned and not integrity-verified at the inference engine level. The `d0cs/security.md` document explicitly acknowledges this: "Treat pulled models the same as any unsigned binary."

---

### T8 — Supply Chain Compromise of AI Libraries

**What it is:** Malicious code injected into widely-used AI/ML packages via compromised PyPI accounts or repository access.

**Real incidents:**
- LiteLLM PyPI compromise (March 24, 2026): TeamPCP three-stage backdoor. LiteLLM routes requests across LLM providers — a compromised version silently intercepts API keys for every provider in use.
- Supply-Chain Poisoning Attacks Against LLM Coding Agent Skill Ecosystems (arxiv 2604.03081): Demonstrates how MCP server ecosystems can be targeted.

**Where BeigeBox is exposed:** BeigeBox has hash-locked deps (`requirements.lock`) and pre-push `pip-audit`, which is the correct approach. Gap: no runtime behavioral monitoring for already-loaded libraries executing unexpected network calls.

---

### T9 — MCP and Agentic Tool Call Injection

**What it is:** Attacks targeting the Model Context Protocol layer and tool-use pipelines in agentic systems. The LLM is manipulated into calling the wrong tool, calling a tool with attacker-controlled parameters, or invoking malicious tools registered via tool shadowing.

**Attack vectors (2025-2026):**
- Tool poisoning: manipulated tool descriptions cause the LLM to prefer a malicious server's "send_email" over the legitimate one
- Tool shadowing: namespace collisions when multiple MCP servers register similarly named tools
- Prompt injection via MCP sampling: MCP servers exploit the sampling feature to covertly exfiltrate session data
- GitHub Copilot CVE-2025-53773: CVSS 9.6 RCE via MCP tool call injection

**Where BeigeBox is exposed:** BeigeBox exposes its own MCP server at `POST /mcp`. The `mcp_server.py` exposes all registered tools. No validation of tool call parameters for injection patterns. No tool invocation audit log separating requested vs actual calls.

---

### T10 — Data Exfiltration Through LLM Outputs

**What it is:** Using the LLM as a covert channel to exfiltrate data from restricted environments or encode sensitive information in model outputs in ways DLP systems cannot detect.

**Techniques:**
- Encoding in whitespace, punctuation, or token choice variations — evades standard DLP
- DNS tunneling: LLM-generated code makes DNS queries that encode exfiltrated data
- Indirect prompt injection triggering the LLM to include sensitive context in a URL
- Memory exfiltration: persistent agent memory becomes an attack surface when shared across sessions

**Detection gap:** Traditional DLP is tuned for email, endpoint, and cloud storage. No tooling exists that monitors AI-generated text for covert encoding patterns. AWS AgentCore DNS tunneling PoC demonstrated zero-click data exfiltration via DNS resolver.

---

### T11 — LLMjacking and API Key Theft

**What it is:** Unauthorized use of stolen LLM API credentials to run inference at the victim's expense, or to access proprietary model capabilities.

**Current exploitation:** Bearer tokens exposed in JavaScript source, mobile app binaries, git history, and browser network requests. Keys generated with insufficient entropy are brute-forceable. Cost exhaustion attacks spike bills before detection.

**Where BeigeBox is exposed:** `auth.py` handles inbound key validation well. Gap is on outbound: API keys for OpenRouter, OpenAI-compatible backends are stored in `agentauth` keychain or env vars. No anomaly detection on outbound API spend vs baseline. No budget circuit breaker.

---

### T12 — Adversarial Inputs at Inference Time

**What it is:** Carefully crafted inputs (text, image, or multimodal) that cause deterministic misclassification or behavioral divergence in production models.

**Current exploitation (multimodal, 2025-2026):**
- ICLR 2025: All agents including GPT-4o can be hijacked with imperceptible image perturbations (16/256 pixel magnitude). 67% success rate.
- Cross-modal attacks: threats arise at the fusion boundary between modalities, not just within individual modality processing
- Multimodal stego-malware appends obfuscated payloads in images; LLM agents "uplift" and execute the payload

**Where BeigeBox is exposed:** Image inputs to multimodal backends pass through with no preprocessing. The Operator can retrieve and process images from URLs via CDP and web tools.

---

### T13 — AI-Generated Malware and Threat Amplification

**What it is:** LLMs used as force multipliers in offensive operations — generating ransomware variants, phishing lures, obfuscation layers, and C2 scripts.

**Current exploitation:**
- PromptFlux and PromptSteal (Google GTIG 2025): malware strains that call LLMs mid-execution to regenerate their own code on demand, evading signature-based detection
- State-sponsored actors (North Korea, Iran, PRC) using Gemini for reconnaissance, phishing lure generation, and C2 development
- Claude used to generate ransomware variants with advanced evasion capabilities (confirmed 2025 incident)

**Relevance to BeigeBox:** BeigeBox as a proxy needs to detect when it is being used as the LLM component in an attack chain — e.g., a client repeatedly requesting code generation, obfuscation, or malware-adjacent content.

---

### T14 — Deepfake and Synthetic Media in Social Engineering

**What it is:** AI-generated audio, video, or text used to impersonate humans in social engineering attacks targeting AI systems or their operators.

**Current state:** Detection tooling exists but is fragmented. Open-source: DeepSafe, DeepFense (400+ model evaluation), FakeVoiceFinder (Jan 2026). Commercial: Sensity AI (acquired by Check Point), Intel FakeCatcher. Detection accuracy degrades against in-the-wild generation techniques not represented in training data.

**Relevance to BeigeBox:** Voice pipeline (Whisper + Kokoro) could be fed synthesized audio to impersonate authorized operators.

---

### T15 — Context Manipulation and Memory Poisoning in Long-Running Agents

**What it is:** Gradual corruption of an agent's memory, working context, or persistent state to alter its behavior over multi-turn interactions without triggering single-turn safety checks.

**Current exploitation:** Agent memory stores (conversation history, plan files, workspace) become persistent attack surfaces. An attacker who can write to `workspace/out/plan.md` can influence all future Operator turns. Semantic cache poisoning can cause cached malicious responses to serve future queries.

**Where BeigeBox is exposed:** The Operator reads `workspace/out/plan.md` on every turn. The semantic cache stores and replays responses keyed by embedding similarity. Neither source is validated for injected instructions between writes.

---

## 2. Current Tooling Matrix

Coverage rating: **Full** = production-ready, well-maintained | **Partial** = exists but immature or bypassable | **Gap** = no adequate tooling | **Proprietary-only** = no viable open-source

| Threat | Open-Source Coverage | Proprietary Coverage | BeigeBox Current |
|--------|---------------------|---------------------|-----------------|
| T1: Direct Prompt Injection | Partial (LLM Guard, Rebuff, NeMo, LlamaFirewall) | Full (Lakera Guard, Azure Content Safety) | Partial (regex patterns, 12 signatures) |
| T2: Indirect Prompt Injection | Partial (Promptfoo, Garak) | Partial (Lakera) | Gap |
| T3: Jailbreaking | Partial (Garak, DeepTeam, Rebuff) | Partial (Lakera, HiddenLayer) | Gap |
| T4: RAG/Vector Poisoning | Gap (RevPRAG detection only) | Proprietary-only | Gap |
| T5: Model Extraction | Gap (academic defenses only) | Proprietary-only | Gap |
| T6: Training Data Poisoning | Gap (research tools only) | Proprietary-only | Gap |
| T7: Model Backdoors | Partial (Microsoft scanner, beta) | Partial (HiddenLayer) | Gap |
| T8: Supply Chain | Full (pip-audit, Trivy, Gitleaks) | Full (Snyk, Dependabot) | Full (implemented) |
| T9: MCP/Tool Call Injection | Gap | Gap | Gap |
| T10: Output Exfiltration | Gap | Gap | Gap |
| T11: LLMjacking/API Abuse | Partial (rate limiting, alerts) | Partial (Upwind) | Partial (rate limiter) |
| T12: Adversarial Inputs | Gap (multimodal) | Gap | Gap |
| T13: AI-Generated Malware | Partial (SIEM/EDR with AI rules) | Partial (CrowdStrike Charlotte AI) | Gap |
| T14: Deepfake Detection | Partial (DeepSafe, DeepFense) | Full (Sensity AI, Intel FakeCatcher) | Gap |
| T15: Memory/Context Poisoning | Gap | Gap | Gap |

---

## 3. Library Gap Analysis

### What Exists (Active Open-Source Tools)

**LLM Guard** (Protect AI, MIT)
- 15 input scanners + 20 output scanners
- Covers: prompt injection, PII, toxicity, ban topics, code detection
- Can run as standalone Docker API or Python library
- Maturity: **Medium** — production deployable, maintained, 2.5k stars
- Gap: No multimodal support, no RAG-specific scanning, regex/classifier based (bypassable)

**NeMo Guardrails** (NVIDIA, Apache 2.0)
- Policy DSL for defining conversation rails
- Covers: topic control, safe responses, custom policies
- Maturity: **Medium** — complex to configure, good for chatbot use cases
- Gap: Requires LLM for evaluation (adds latency), not suited for high-throughput proxy

**LlamaFirewall** (Meta, open-source)
- PromptGuard 2: universal jailbreak detector
- Agent Alignment Checks: chain-of-thought auditor for goal misalignment
- CodeShield: online static analysis of generated code
- Maturity: **Low-Medium** — new (2025), promising architecture, still early
- Gap: Focused on Meta's use cases, agent alignment checks are expensive

**Garak** (NVIDIA, Apache 2.0)
- 120+ probe modules for vulnerability scanning
- 23 model backends
- Maturity: **Medium** — excellent for CI red-teaming, not runtime detection
- Gap: Offline scanner only — not a runtime guardrail

**Promptfoo** (MIT)
- 50+ vulnerability types, CI/CD integration
- Tests RAG pipelines, agent architectures, tool parameter injection
- Maps to OWASP LLM Top 10, NIST AI RMF, MITRE ATLAS
- Maturity: **High** — most complete open-source AI testing platform in 2026
- Gap: Test-time tool, not runtime protection

**Rebuff** (MIT)
- Multi-layer: heuristics + LLM-based detection + canary tokens
- Vector database of known attack signatures
- Maturity: **Low** — small project, not actively maintained as of 2025
- Gap: Limited to prompt injection, no broader coverage

**DeepTeam** (Confident AI, MIT)
- Supports OWASP Top 10, OWASP ASI 2026, NIST, MITRE, Aegis frameworks
- Red teams agentic pipelines
- Maturity: **Medium** — growing, good framework coverage
- Gap: Evaluation tool, not runtime guard

**MarkLLM** (THU-BPM, MIT)
- 9 watermarking algorithms for LLM output
- Maturity: **Low-Medium** — research tool, EMNLP 2024 demo
- Gap: Only watermarks, does not detect or prevent threats

**SynthID** (Google DeepMind, Apache 2.0 via HuggingFace)
- Watermarks LLM-generated text, audio, images
- Unified SynthID Detector released May 2025
- Maturity: **Medium** — production use at Google scale, open-sourced
- Gap: Watermarking provenance only, not a security control

**DeepSafe / DeepFense** (open-source)
- DeepSafe: ensemble deepfake detection across image/video/audio
- DeepFense: 400+ model evaluation toolkit for audio deepfakes
- Maturity: **Low** — research tools, not production-grade fraud prevention

### What is Missing (Critical Gaps)

**1. RAG/Vector Store Content Scanner**
No open-source tool scans documents before they are embedded into vector stores for injected instructions. The closest is heuristic preprocessing (regex for "ignore previous" etc.) but no semantic-aware scanner exists. This is a critical gap given PoisonedRAG's 90% success rate.

**2. Runtime Covert Channel / Output Exfiltration Detector**
No open-source tooling monitors LLM outputs for covert data encoding (whitespace steganography, token distribution anomalies, DNS-triggering patterns). Traditional DLP cannot inspect AI outputs at this semantic level.

**3. Model Integrity Verifier**
No production-ready open-source tool verifies GGUF/SafeTensors model integrity against known backdoors before loading. Microsoft's scanner is in beta and not CI-integrated. No standard hash registry exists for model weights.

**4. MCP / Tool Call Security Layer**
No open-source middleware exists to validate MCP tool calls for injection patterns, enforce tool namespace isolation, or audit tool invocation chains. The MCP spec itself treats authentication and security as optional.

**5. Agentic Memory Integrity Guard**
No tooling monitors or validates agent persistent state (plan files, workspace, conversation memory) for injected instructions between write and read cycles.

**6. LLM-Native DLP**
Traditional DLP (Symantec, Forcepoint) is not trained to detect sensitive data encoded in AI prompt/response pairs. No open-source LLM-aware DLP exists.

**7. Multimodal Input Scanner**
No open-source tool scans images for adversarial perturbations or steganographic instructions before they are passed to multimodal LLMs.

**8. AI Cost Anomaly Detection**
No open-source tool baselines normal token spend per API key and alerts on statistical anomalies indicating extraction attacks or LLMjacking.

### Maturity Summary

| Tool | Type | Maturity | License | Runtime? |
|------|------|----------|---------|---------|
| LLM Guard | Input/Output scanner | Medium | MIT | Yes |
| NeMo Guardrails | Policy rails | Medium | Apache 2.0 | Yes |
| LlamaFirewall | Multi-layer guard | Low-Medium | Open | Yes |
| Garak | Vulnerability scanner | Medium | Apache 2.0 | No (CI) |
| Promptfoo | Red team platform | High | MIT | No (CI) |
| Rebuff | Injection detector | Low | MIT | Yes |
| DeepTeam | Red team framework | Medium | MIT | No (CI) |
| MarkLLM | Watermarking | Low | MIT | Partial |
| SynthID | Watermarking | Medium | Apache 2.0 | Partial |
| Microsoft Backdoor Scanner | Model integrity | Low | N/A | No |
| Lakera Guard | Full guardrail platform | High | Proprietary | Yes |
| Protect AI Guardian | Guardrail platform | High | Proprietary | Yes |
| HiddenLayer | Model security | High | Proprietary | Yes |

---

## 4. What BeigeBox Should Build

Priority based on gap severity, exploitation frequency, and architectural fit. BeigeBox is positioned as an OpenAI-compatible proxy — the ideal insertion point for transparent security controls that require zero client changes.

---

### Priority 1 — HIGH (Build Now)

#### P1-A: Enhanced Prompt Injection Guard (`beigebox/guardrails.py` upgrade)

**Gap addressed:** T1 (direct injection), T3 (jailbreaking)

**What to build:**
- Integrate LLM Guard as an optional scanner backend — when enabled, routes suspicious-scoring messages through LLM Guard's API (Docker sidecar or Python lib)
- Add semantic similarity check against a maintained attack vector database (embedding distance from known jailbreak patterns)
- Add obfuscation normalization before pattern matching: Unicode normalization, leet-speak expansion, base64/hex decode attempt
- Add canary token injection: embed a hidden token in system prompts; if model echoes it in output, fire an injection alert
- Configurable confidence threshold — soft block (log + allow), hard block, or quarantine (route to safer model)

**Config addition:**
```yaml
guardrails:
  input:
    injection_backend: "llm_guard"   # "regex" (current) | "llm_guard" | "embedding"
    injection_confidence_threshold: 0.7
    canary_tokens: true
```

**Effort:** 8-12 hours  
**Test integration:** Add injection test corpus to pytest, mark `@pytest.mark.security`

---

#### P1-B: RAG Content Scanner (pre-embed validation)

**Gap addressed:** T2 (indirect injection), T4 (RAG poisoning), T15 (memory poisoning)

**What to build:**
- Hook into `VectorStore.add_documents()` — scan every document before it is embedded
- Scanner checks: regex patterns for instruction-injection phrases, semantic similarity to known indirect injection templates, metadata validation (source, timestamp, hash)
- Add to `confluence_crawler` and `document_search` ingestion paths
- Output: `(allowed: bool, confidence: float, matched_pattern: str)` — mirrors `GuardrailResult`
- Store rejected documents in a quarantine table in SQLite for audit

**Effort:** 6-8 hours

---

#### P1-C: API Cost Anomaly and LLMjacking Detection

**Gap addressed:** T5 (model extraction), T11 (LLMjacking)

**What to build:**
- Per-key token budget tracking in `auth.py` — track cumulative input/output tokens per key in a rolling 24h window in SQLite
- Anomaly signals: token velocity spike (>3σ from key's baseline), unusual query entropy (extraction attempts use high-diversity prompts), model switching frequency, bulk identical-prefix queries
- Configurable budget hard caps: `max_daily_tokens_in`, `max_daily_tokens_out`
- Tap event on anomaly: `source=api_anomaly, meta={key_name, signal, value, threshold}`
- CLI command: `beigebox flash` already shows stats — add security anomaly summary

**Config addition:**
```yaml
auth:
  keys:
    - name: production-client
      max_daily_tokens_in: 500000
      max_daily_tokens_out: 200000
      extraction_detection: true
```

**Effort:** 6-8 hours

---

#### P1-D: MCP Tool Call Validator

**Gap addressed:** T9 (MCP/tool call injection)

**What to build:**
- Pre-execution hook in `beigebox/mcp_server.py` and `beigebox/tools/registry.py`
- Validates tool call parameters for injection patterns before dispatch
- Enforces tool namespace isolation: two tools cannot share a name across different MCP servers
- Audit log: every tool call (tool name, parameters hash, caller, result code) written to SQLite `tool_audit` table
- Rate limit per tool: prevent rapid repeated calls that indicate automated extraction
- Tool call signature validation: schema check against registered tool's parameter spec

**Effort:** 8-10 hours

---

### Priority 2 — MEDIUM (Build This Month)

#### P2-A: LLMSecurityTester Tool (`beigebox/tools/llm_security_tester.py`)

**Gap addressed:** T1, T3, T5 — automated red-teaming of BeigeBox itself and downstream LLMs

**What to build:**
Implement the `LLMSecurityTester` tool from `SECURITY_TOOLKIT_ROADMAP.md` (already on the roadmap as a Tier 2 item). This wraps Garak/Promptfoo in a BeigeBox Operator tool:
- Launch Garak probes against a target endpoint (including `localhost:8001`)
- Test categories: prompt injection, jailbreaking, PII leakage, system prompt extraction, model extraction indicators
- Return structured findings: `{attack_type, success, risk_level, response_excerpt, fix}`
- Integrate with the `LLMSecurityTester` roadmap item already documented

**Effort:** 10-12 hours (aligns with SECURITY_TOOLKIT_ROADMAP.md estimate)

---

#### P2-B: Output Exfiltration Monitor

**Gap addressed:** T10 (data exfiltration), T15 (memory poisoning)

**What to build:**
- Post-stream output analysis hook in `proxy.py` (after `check_output`, before response flush)
- Checks: PII density in output vs. input (sudden PII inflation = exfiltration signal), URL detection with external domain flag, unusual whitespace patterns (steganography heuristic), base64/hex blocks in response, unexpectedly long outputs vs. input complexity
- Does not block by default — logs high-confidence alerts as Tap events
- Can be escalated to block via config threshold

**Config addition:**
```yaml
guardrails:
  output:
    exfiltration_detection: true
    exfiltration_threshold: 0.8
    block_external_urls: false   # log only by default
```

**Effort:** 6-8 hours

---

#### P2-C: Model Integrity Check at Load Time

**Gap addressed:** T7 (model backdoors), T8 (supply chain)

**What to build:**
- At startup, for each configured Ollama model: fetch model manifest, verify blob hashes against a local registry of known-good hashes
- Maintain a `model_integrity.yaml` — user populates with trusted hashes, auto-populated on first verified load
- Warn (non-blocking) on hash mismatch or missing entry; configurable to hard-block
- Tap event: `source=model_integrity, meta={model, hash_expected, hash_actual, status}`
- Later: integrate Microsoft's backdoor scanner CLI as an optional deep-scan tool

**Config addition:**
```yaml
model_integrity:
  enabled: true
  mode: "warn"   # "warn" | "block"
  registry_path: "./model_integrity.yaml"
```

**Effort:** 5-7 hours

---

### Priority 3 — LOWER (Design Phase)

#### P3-A: Multimodal Input Scanner

**Gap addressed:** T12 (adversarial inputs), T2 (indirect injection in images)

**What to build:**
- Intercept base64-encoded image inputs in chat completions
- Run DeepSafe or a lightweight CLIP-based anomaly detector to flag adversarial perturbations
- Scan image EXIF/metadata and embedded text layers for injected instructions
- This is a stretch goal — requires model hosting or external API integration

**Effort:** 15-20 hours (defer until multimodal traffic is significant)

---

#### P3-B: Agent Memory Integrity Guard

**Gap addressed:** T15 (memory poisoning), T2 (indirect injection via plan files)

**What to build:**
- Content-hash validation: when Operator reads `workspace/out/plan.md` or other persistent files, compare hash against last-known-good value
- If hash changed unexpectedly between Operator turns (external write), emit Tap warning before consuming
- For semantic cache: scan cached responses for injection signatures at retrieval time (not just at write time)

**Effort:** 4-6 hours

---

#### P3-C: Deepfake Audio Detection for Voice Pipeline

**Gap addressed:** T14 (deepfake/synthetic media)

**What to build:**
- Integrate FakeVoiceFinder or DeepFense as an optional pre-processing step in the Whisper voice pipeline
- Configurable confidence threshold: flag, log, or block synthetic audio inputs
- Relevant when BeigeBox voice pipeline is exposed to external users

**Effort:** 8-10 hours (only if voice feature becomes production-facing)

---

## 5. Integration Strategy

### Security Layer Architecture

BeigeBox's position as a transparent proxy makes it the ideal enforcement point. The security stack should layer as follows:

```
incoming request
    ↓
[AUTH] MultiKeyAuthRegistry — key validation, rate limit, model/endpoint ACL
    ↓
[GUARDRAIL-IN] Enhanced Prompt Injection Guard (P1-A)
    ├── Unicode/obfuscation normalization
    ├── Pattern + semantic injection check
    └── Canary token injection
    ↓
[RAG SCAN] Pre-embed validator at VectorStore ingestion (P1-B)
    ↓
[ROUTING] Existing routing pipeline (Z-command, classifier, decision LLM)
    ↓
[TOOL AUDIT] MCP Tool Call Validator (P1-D)
    ↓
[BACKEND] LLM backend call
    ↓
[GUARDRAIL-OUT] Output check + Exfiltration Monitor (P2-B)
    ├── PII redaction
    ├── Pattern block
    └── Exfiltration signal detection
    ↓
[COST ANOMALY] API cost/token anomaly check (P1-C)
    ↓
response to client
```

### Operator / Agent Integration

The Operator agent needs security-awareness injected at three points:

1. **Tool invocation policy**: The `ToolRegistry` should enforce a per-operator-run tool call budget. If an agent calls the same tool with systematically varying inputs (extraction pattern), trigger an alert.

2. **Workspace file integrity**: Before each Operator turn reads `plan.md` or any workspace file, validate the hash chain matches the last Operator-authored write.

3. **System prompt protection**: Inject a canary phrase into the Operator's system prompt. Enable `output.canary_check: true` to detect if the canary appears in responses (indicates system prompt extraction attempt).

### Observability Integration

All new security events should use the existing Tap system. Recommended new event types:

| Event Source | Signal | Severity |
|---|---|---|
| `prompt_guard` | `injection_detected` | warning/critical |
| `rag_scanner` | `poisoned_document` | critical |
| `api_anomaly` | `extraction_signal` | warning |
| `api_anomaly` | `cost_spike` | warning |
| `tool_audit` | `tool_call_injected` | critical |
| `output_monitor` | `exfiltration_signal` | warning |
| `model_integrity` | `hash_mismatch` | critical |

### CI/CD Integration

Add a `security` test marker to pytest. Recommended additions to `scripts/security-scan.sh`:

```bash
# Add after bandit:
banner "promptfoo — LLM red team scan"
if command -v promptfoo &>/dev/null; then
    promptfoo redteam run --config .promptfoo.yaml 2>&1
fi

banner "garak — LLM vulnerability scan"  
if command -v garak &>/dev/null; then
    python -m garak --model_type rest --model_name beigebox-local --probes promptinject 2>&1
fi
```

---

## 6. Recommendations for 2026

### Standards and Compliance

**EU AI Act (August 2, 2026 deadline):** High-risk AI systems must implement machine-readable watermarking, risk assessments, and incident reporting. BeigeBox deployments used in high-risk contexts need: output watermarking (SynthID integration), comprehensive audit logs (already strong via Tap), and documented risk assessment.

**NIST AI RMF (AI 100-1):** Govern → Map → Measure → Manage framework. BeigeBox's Tap logging and existing threat model (`d0cs/security.md`) partially satisfy Measure/Manage. Gap: no formal Govern or Map phase documentation.

**OWASP LLM Top 10 2025 compliance checklist:** Against current BeigeBox:
- LLM01 Prompt Injection: **Partial** (P1-A closes the gap)
- LLM02 Insecure Output Handling: **Partial** (output guardrails exist, exfiltration detection missing)
- LLM03 Supply Chain: **Strong** (hash-locked, pip-audit, digest-pinned Docker)
- LLM04 Training Data Poisoning: **Weak** (no controls on data ingested by Operator tools)
- LLM05 Insecure Output Handling: **Partial**
- LLM06 Excessive Agency: **Partial** (tool profiles limit tool access, no runtime budget)
- LLM07 System Prompt Leakage: **Weak** (no canary tokens or extraction detection)
- LLM08 Vector/Embedding Weaknesses: **Gap** (P1-B closes this)
- LLM09 Misinformation: **Out of scope** (routing/content concern, not technical control)
- LLM10 Model Theft: **Weak** (no extraction detection, P1-C addresses partially)

### Emerging Tools to Watch

- **LlamaFirewall**: Meta's agent-focused guardrail framework. Its Agent Alignment Checks (chain-of-thought audit) is the only open-source tool targeting goal misalignment in agentic systems — directly relevant to BeigeBox Operator.
- **Microsoft LLM Backdoor Scanner**: Currently beta; when it reaches stability, integrate as `beigebox bench --security` option for model integrity deep-scan.
- **OpenGuardrails** (arxiv 2510.19169): First context-aware safety model + deployable guardrails platform. Watch for production release — could replace LLM Guard as the default scanner backend.
- **SynthID Detector** (Google/HuggingFace): Unified detector for text, audio, image watermarks — relevant when BeigeBox voice pipeline is production-facing.
- **OWASP ASVS for AI** (in draft): Application Security Verification Standard adapted for AI systems. When finalized, map BeigeBox controls to it.

### Architectural Best Practices for 2026

1. **Assume the context window is compromised.** Treat all external content entering the context (RAG chunks, tool outputs, user messages) as potentially adversarial. Scan before inject.

2. **Defense at the proxy layer, not the model layer.** Models cannot reliably defend themselves — prompt injection defeats model-level alignment. The proxy is the correct control plane.

3. **Log everything, block selectively.** Most threats are best detected, not prevented. Hard-blocking creates denial-of-service vectors. Log first, alert on high confidence, block only on near-certain signals.

4. **Cost and token anomaly detection is your model extraction early warning system.** Extraction attacks are statistically distinguishable from normal usage — high query diversity, systematic probing patterns, unusual output-to-input token ratios.

5. **MCP security is now a first-class concern.** As BeigeBox exposes MCP and consumes MCP tool servers, the tool call layer is the new injection surface. Parameter validation and tool namespacing are not optional.

6. **Canary tokens are cheap and effective.** Embedding a canary phrase in system prompts detects system prompt extraction with near-zero false positives and zero performance overhead.

7. **Open-source AI security tooling is 12-18 months behind commercial offerings.** For runtime production protection, LLM Guard (Protect AI) is the best open-source option. For offline red-teaming and CI integration, Promptfoo is the most complete platform. Neither covers everything.

---

## Appendix: BeigeBox Security Build Order

Incorporating the gaps above into the existing `SECURITY_TOOLKIT_ROADMAP.md` Phase 3 (AI-specific additions):

| # | Tool/Feature | Addresses | Effort | Priority |
|---|---|---|---|---|
| 1 | Enhanced Prompt Injection Guard (P1-A) | T1, T3 | 8-12h | Now |
| 2 | RAG Content Scanner (P1-B) | T2, T4, T15 | 6-8h | Now |
| 3 | API Cost Anomaly Detector (P1-C) | T5, T11 | 6-8h | Now |
| 4 | MCP Tool Call Validator (P1-D) | T9 | 8-10h | Now |
| 5 | LLMSecurityTester Tool (P2-A) | T1, T3, T5 | 10-12h | This month |
| 6 | Output Exfiltration Monitor (P2-B) | T10, T15 | 6-8h | This month |
| 7 | Model Integrity Check (P2-C) | T7, T8 | 5-7h | This month |
| 8 | Agent Memory Integrity Guard (P3-B) | T15, T2 | 4-6h | Next sprint |
| 9 | Multimodal Input Scanner (P3-A) | T12, T2 | 15-20h | Later |
| 10 | Deepfake Audio Detection (P3-C) | T14 | 8-10h | Later |

**Total for Priority 1+2:** ~57-73 hours  
**Total including Priority 3:** ~84-109 hours

---

*This document should be reviewed quarterly. The AI security threat landscape is evolving faster than any annual review cycle can track.*
