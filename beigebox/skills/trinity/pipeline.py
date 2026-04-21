"""
Trinity Pipeline - Complete 4-phase security audit orchestration.

Phase 1: Parallel Independent Audits (Surface Scanner, Deep Reasoner, Specialist)
Phase 2: Consensus Building
Phase 3: Appellate Review
Phase 4: Source Verification
"""

import asyncio
import json
import time
import uuid
import re
from datetime import datetime
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, asdict

from .chunker import TrinityChunker
from .model_router import TrinityModelRouter
from .fuzzing import TrinityFuzzer, RiskScorer, SeedCorpusExtractor, CrashClassifier, SmartHarnessGenerator, AdaptiveTimeAllocator
from .function_extractor import FunctionExtractor
from .logger import TrinityLogger, TrinityLogConfig


@dataclass
class Finding:
    """A security finding."""
    id: str
    severity: str  # critical, high, medium, low
    title: str
    description: str
    file: str
    line: int
    evidence: str
    model: str  # which model found it
    confidence: float  # 0.0-1.0


class TrinityPipeline:
    """Complete Trinity audit pipeline."""

    def __init__(
        self,
        repo_path: str,
        audit_id: Optional[str] = None,
        models: Optional[Dict[str, str]] = None,
        budget: Optional[Dict[str, int]] = None,
        beigebox_url: str = "http://localhost:8000",
        route_via_beigebox: bool = True,
        log_config: Optional[TrinityLogConfig] = None,
    ):
        self.repo_path = repo_path
        self.audit_id = audit_id or f"trinity-{uuid.uuid4().hex[:12]}"
        self.models = models or {
            "surface": "haiku",
            "deep": "grok-4.1-fast",
            "specialist": "arcee-trinity-large",
            "appellate": "qwen-max",
        }
        self.budget = budget or {
            "surface": 15000,
            "deep": 40000,
            "specialist": 20000,
            "appellate": 25000,
        }

        _log_cfg = log_config or TrinityLogConfig(enabled=False)
        self.log = TrinityLogger(self.audit_id, _log_cfg)

        self.chunker = TrinityChunker(repo_path, logger=self.log)
        self.router = TrinityModelRouter(
            beigebox_url=beigebox_url,
            route_via_beigebox=route_via_beigebox,
            logger=self.log
        )

        self.chunks: List[Dict] = []
        self.chunk_metadata: Dict = {}
        self.findings: List[Finding] = []
        self.phase_1_results: Dict[str, List[Finding]] = {}
        self.phase_2_consensus: List[Dict] = []
        self.phase_3_appellate: List[Dict] = []
        self.phase_4_verified: List[Finding] = []

        self.audit_log: List[Dict] = []
        self.start_time = time.time()

        # Data handling configuration
        self.data_handling_config = {
            "encryption_enabled": False,  # TODO: implement AES-256 encryption
            "audit_trail_enabled": True,
            "sanitize_evidence": True,
            "retention_days": 90,
        }

    async def run_full_audit(self) -> Dict[str, Any]:
        """Execute full 4-phase Trinity audit."""
        try:
            # Prepare: Load and chunk code
            await self._prepare()

            # Phase 1: Parallel audits
            await self._phase_1_parallel_audits()

            # Phase 2: Consensus
            await self._phase_2_consensus_building()

            # Phase 3: Appellate review
            await self._phase_3_appellate_review()

            # Phase 4: Source verification
            await self._phase_4_source_verification()

            # Return full results
            return self._build_report()

        except Exception as e:
            self._log({"error": str(e), "phase": "unknown"})
            raise

    async def _prepare(self) -> None:
        """Load and chunk code repository."""
        self.log.phase_banner("prepare: chunking repository")
        print(f"[{self.audit_id}] Preparing: chunking repository...")
        self.chunks, self.chunk_metadata = self.chunker.chunk_repository()
        self.log.info("repository chunked", phase="prepare",
                      total_chunks=self.chunk_metadata['total_chunks'],
                      total_tokens=self.chunk_metadata['total_tokens'])
        print(f"[{self.audit_id}] Prepared: {self.chunk_metadata['total_chunks']} chunks, {self.chunk_metadata['total_tokens']} tokens")

    async def _phase_1_parallel_audits(self) -> None:
        """Phase 1: Run 3 independent audits + fuzzing in parallel."""
        self.log.phase_banner("1: parallel independent audits + fuzzing")
        print(f"[{self.audit_id}] Phase 1: Parallel Independent Audits + Fuzzing (starting)")

        # Create tasks for all 3 stacks + fuzzing
        tasks = [
            self._audit_surface_scanner(),
            self._audit_deep_reasoner(),
            self._audit_specialist(),
            self._phase_1b_fuzzing(),  # Parallel fuzzing
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        names = ["surface_scanner", "deep_reasoner", "specialist", "fuzzing"]
        self.phase_1_results = {}
        for name, result in zip(names, results):
            if isinstance(result, Exception):
                self.log.error(f"Phase 1 task '{name}' raised an exception — results discarded",
                               phase="phase_1", task=name, exc=result)
                self.phase_1_results[name] = []
            else:
                self.phase_1_results[name] = result

        print(f"[{self.audit_id}] Phase 1: Complete ({sum(len(f) for f in self.phase_1_results.values())} findings)")

    async def _audit_surface_scanner(self) -> List[Finding]:
        """Surface Scanner: Fast pattern matching for obvious vulnerabilities."""
        print(f"[{self.audit_id}] Stack 1: Surface Scanner (starting)")

        findings = []
        model_key = self.models["surface"]
        tokens_used = 0

        system_prompt = """You are a security code auditor looking for obvious vulnerabilities.
        Scan for: SQL injection, XSS, hardcoded secrets, unsafe functions, input validation gaps.
        Be brief. Return findings as JSON array: [{"title": "...", "description": "...", "severity": "critical|high|medium|low", "evidence": "..."}]"""

        for chunk in self.chunks:
            if tokens_used >= self.budget["surface"]:
                print(f"[{self.audit_id}] Surface Scanner: Budget exceeded, stopping")
                break

            prompt = f"""File: {chunk['file']} (lines {chunk['line_start']}-{chunk['line_end']})

Code:
{chunk['content']}

Find security vulnerabilities."""

            try:
                result = await self.router.call_model(
                    model_key,
                    prompt,
                    max_tokens=3000,
                    temperature=0.0,
                    system=system_prompt,
                )

                tokens_used += result["tokens_used"]
                self._log({
                    "phase": 1,
                    "stack": "surface_scanner",
                    "chunk": chunk["chunk_id"],
                    "tokens": result["tokens_used"],
                })

                try:
                    findings_json = json.loads(result["content"])
                    if isinstance(findings_json, list):
                        for f in findings_json:
                            finding = Finding(
                                id=f"{self.audit_id}:surface:{len(findings)}",
                                severity=f.get("severity", "medium"),
                                title=f.get("title", "Unknown"),
                                description=f.get("description", ""),
                                file=chunk["file"],
                                line=chunk["line_start"],
                                evidence=f.get("evidence", ""),
                                model="haiku",
                                confidence=0.85,
                            )
                            self.log.finding_extracted(finding.id, finding.title,
                                                       finding.severity, finding.model, phase="phase_1a_surface")
                            findings.append(finding)
                    else:
                        self.log.warn("surface scanner: LLM returned JSON but not a list — findings discarded",
                                      phase="phase_1a_surface", chunk=chunk["chunk_id"],
                                      json_type=type(findings_json).__name__)
                except json.JSONDecodeError as je:
                    self.log.parse_fail("phase_1a_surface_scanner",
                                        result["content"], je, phase="phase_1a_surface")

            except Exception as e:
                self.log.error(f"Surface Scanner exception on chunk",
                               phase="phase_1a_surface", chunk=chunk["chunk_id"], exc=e)
                self._log({"phase": 1, "stack": "surface_scanner", "chunk": chunk["chunk_id"], "error": str(e)})
                continue

        print(f"[{self.audit_id}] Stack 1: Surface Scanner (complete, {len(findings)} findings)")
        return findings

    async def _audit_deep_reasoner(self) -> List[Finding]:
        """Deep Reasoner: Complex reasoning about logic flaws, state machines, etc."""
        print(f"[{self.audit_id}] Stack 2: Deep Reasoner (starting)")

        findings = []
        model_key = self.models["deep"]
        tokens_used = 0

        system_prompt = """You are a senior security architect analyzing code for complex vulnerabilities.
        Look for: logic flaws, race conditions, state machine issues, authorization bugs, business logic errors.
        Provide detailed reasoning. Return findings as JSON array."""

        # Sample fewer chunks (these are expensive)
        sampled_chunks = self.chunks[::2]  # Every other chunk

        for chunk in sampled_chunks:
            if tokens_used >= self.budget["deep"]:
                print(f"[{self.audit_id}] Deep Reasoner: Budget exceeded, stopping")
                break

            prompt = f"""File: {chunk['file']} (lines {chunk['line_start']}-{chunk['line_end']})

Code:
{chunk['content']}

Analyze for complex security vulnerabilities and architectural issues."""

            try:
                result = await self.router.call_model(
                    model_key,
                    prompt,
                    max_tokens=6000,
                    temperature=0.0,
                    system=system_prompt,
                )

                tokens_used += result["tokens_used"]
                self._log({
                    "phase": 1,
                    "stack": "deep_reasoner",
                    "chunk": chunk["chunk_id"],
                    "tokens": result["tokens_used"],
                })

                try:
                    findings_json = json.loads(result["content"])
                    if isinstance(findings_json, list):
                        for f in findings_json:
                            finding = Finding(
                                id=f"{self.audit_id}:deep:{len(findings)}",
                                severity=f.get("severity", "medium"),
                                title=f.get("title", "Unknown"),
                                description=f.get("description", ""),
                                file=chunk["file"],
                                line=chunk["line_start"],
                                evidence=f.get("evidence", ""),
                                model="grok-4.1-fast",
                                confidence=0.90,
                            )
                            self.log.finding_extracted(finding.id, finding.title,
                                                       finding.severity, finding.model, phase="phase_1a_deep")
                            findings.append(finding)
                    else:
                        self.log.warn("deep reasoner: LLM returned JSON but not a list — findings discarded",
                                      phase="phase_1a_deep", chunk=chunk["chunk_id"],
                                      json_type=type(findings_json).__name__)
                except json.JSONDecodeError as je:
                    self.log.parse_fail("phase_1a_deep_reasoner",
                                        result["content"], je, phase="phase_1a_deep")

            except Exception as e:
                self.log.error("Deep Reasoner exception on chunk",
                               phase="phase_1a_deep", chunk=chunk["chunk_id"], exc=e)
                self._log({"phase": 1, "stack": "deep_reasoner", "chunk": chunk["chunk_id"], "error": str(e)})
                continue

        print(f"[{self.audit_id}] Stack 2: Deep Reasoner (complete, {len(findings)} findings)")
        return findings

    async def _audit_specialist(self) -> List[Finding]:
        """Specialist: Language-specific patterns."""
        print(f"[{self.audit_id}] Stack 3: Specialist (starting)")

        findings = []
        model_key = self.models["specialist"]
        tokens_used = 0

        system_prompt = """You are a specialist in security vulnerabilities in production code.
        Identify domain-specific security issues. Return findings as JSON array."""

        sampled_chunks = self.chunks[1::3]  # Different sampling than deep reasoner

        for chunk in sampled_chunks:
            if tokens_used >= self.budget["specialist"]:
                print(f"[{self.audit_id}] Specialist: Budget exceeded, stopping")
                break

            prompt = f"""File: {chunk['file']} (lines {chunk['line_start']}-{chunk['line_end']})

Code:
{chunk['content']}

Identify security vulnerabilities."""

            try:
                result = await self.router.call_model(
                    model_key,
                    prompt,
                    max_tokens=4000,
                    temperature=0.0,
                    system=system_prompt,
                )

                tokens_used += result["tokens_used"]
                self._log({
                    "phase": 1,
                    "stack": "specialist",
                    "chunk": chunk["chunk_id"],
                    "tokens": result["tokens_used"],
                })

                try:
                    findings_json = json.loads(result["content"])
                    if isinstance(findings_json, list):
                        for f in findings_json:
                            finding = Finding(
                                id=f"{self.audit_id}:specialist:{len(findings)}",
                                severity=f.get("severity", "medium"),
                                title=f.get("title", "Unknown"),
                                description=f.get("description", ""),
                                file=chunk["file"],
                                line=chunk["line_start"],
                                evidence=f.get("evidence", ""),
                                model="arcee-trinity-large",
                                confidence=0.88,
                            )
                            self.log.finding_extracted(finding.id, finding.title,
                                                       finding.severity, finding.model, phase="phase_1a_specialist")
                            findings.append(finding)
                    else:
                        self.log.warn("specialist: LLM returned JSON but not a list — findings discarded",
                                      phase="phase_1a_specialist", chunk=chunk["chunk_id"],
                                      json_type=type(findings_json).__name__)
                except json.JSONDecodeError as je:
                    self.log.parse_fail("phase_1a_specialist",
                                        result["content"], je, phase="phase_1a_specialist")

            except Exception as e:
                self.log.error("Specialist exception on chunk",
                               phase="phase_1a_specialist", chunk=chunk["chunk_id"], exc=e)
                self._log({"phase": 1, "stack": "specialist", "chunk": chunk["chunk_id"], "error": str(e)})
                continue

        print(f"[{self.audit_id}] Stack 3: Specialist (complete, {len(findings)} findings)")
        return findings

    async def _phase_1b_fuzzing(self) -> List[Finding]:
        """Phase 1b: Smart fuzzing for dynamic vulnerability detection."""
        print(f"[{self.audit_id}] Phase 1b: Smart Fuzzing (starting)")

        findings = []

        try:
            # Step 1: Extract functions from repository
            extractor = FunctionExtractor(logger=self.log)
            all_functions = extractor.find_fuzzable_functions_in_repo(self.repo_path)

            if not all_functions:
                print(f"[{self.audit_id}] Fuzzing: No fuzzable functions found")
                return []

            # Step 2: Risk score all functions
            scorer = RiskScorer()
            all_functions = scorer.score_functions(all_functions)

            # Step 3: Select top 25 high-risk functions
            selected = all_functions[:25]

            # Step 4: Allocate time budget
            allocator = AdaptiveTimeAllocator()
            time_budget = allocator.allocate_budget(selected, total_budget_seconds=120)

            print(f"[{self.audit_id}] Fuzzing: Selected {len(selected)} functions")

            # Step 5: Generate harnesses and fuzz
            harness_gen = SmartHarnessGenerator()
            fuzzer = TrinityFuzzer(timeout_seconds=5, max_mutations=10000, logger=self.log)
            classifier = CrashClassifier()
            corpus_extractor = SeedCorpusExtractor()

            for func in selected:
                func_timeout = time_budget.get(func['name'], 5)

                # Generate harness
                param_type = harness_gen.infer_parameter_type(func['source'], func['parameters'][0] if func['parameters'] else 'data')
                harness = harness_gen.generate_basic_harness(
                    func['name'],
                    func['parameters'][0] if func['parameters'] else 'data',
                    param_type
                )

                # Extract seed corpus
                seeds = corpus_extractor.extract(func['source'], func['name'])

                # Run fuzzer
                result = await fuzzer.fuzz_function(
                    harness,
                    func['name'],
                    func['file_path'],
                    func_timeout
                )

                # Check for crashes
                crashes = result.get('crashes', [])
                for crash in crashes:
                    # Classify crash (app vs library)
                    if not classifier.is_app_crash(crash, self.repo_path):
                        continue  # Skip library crashes

                    # Create finding
                    severity = "critical" if crash.get('type') in ['RecursionError', 'MemoryError', 'Timeout'] else "high"

                    finding = Finding(
                        id=f"{self.audit_id}:fuzz:{len(findings)}",
                        severity=severity,
                        title=f"DOS/Crash in {func['name']}: {crash.get('type', 'Unknown')}",
                        description=f"Function crashes or hangs on certain inputs. Type: {crash.get('type', 'Unknown')}. Reproducer: {crash.get('description', '')}",
                        file=func['file_path'],
                        line=func['line_start'],
                        evidence=f"Crash type: {crash.get('type')}",
                        model="atheris-fuzzer",
                        confidence=0.95,  # High confidence: actual crash
                    )
                    findings.append(finding)

                self._log({
                    "phase": "1b",
                    "stack": "fuzzing",
                    "function": func['name'],
                    "crashes_found": len(crashes),
                    "timeout": func_timeout,
                })

            print(f"[{self.audit_id}] Phase 1b: Fuzzing (complete, {len(findings)} findings)")

        except Exception as e:
            print(f"[{self.audit_id}] Fuzzing error: {e}")
            self._log({"phase": "1b", "error": str(e)})

        return findings

    async def _phase_2_consensus_building(self) -> None:
        """Phase 2: Build consensus from Phase 1 findings with weighted voting."""
        self.log.phase_banner("2: consensus building")
        print(f"[{self.audit_id}] Phase 2: Consensus Building (starting)")

        all_findings = (
            self.phase_1_results["surface_scanner"] +
            self.phase_1_results["deep_reasoner"] +
            self.phase_1_results["specialist"]
        )

        if not all_findings:
            self.log.warn(
                "Phase 2 input is empty — all Phase 1 audit stacks returned 0 findings. "
                "This usually means JSON parse failures or empty LLM responses. "
                "Enable logging with log_responses=True to inspect raw model output.",
                phase="phase_2",
                surface_count=len(self.phase_1_results["surface_scanner"]),
                deep_count=len(self.phase_1_results["deep_reasoner"]),
                specialist_count=len(self.phase_1_results["specialist"]),
            )

        # Deduplication: group similar findings
        deduplicated = {}
        for finding in all_findings:
            key = (finding.file, finding.title)
            if key not in deduplicated:
                deduplicated[key] = {
                    "finding": finding,
                    "models": [],
                    "confidences": [],
                }
            deduplicated[key]["models"].append(finding.model)
            deduplicated[key]["confidences"].append(finding.confidence)

        # Build consensus with weighted voting
        for key, data in deduplicated.items():
            models = data["models"]
            confidences = data["confidences"]
            agreement_count = len(models)
            avg_confidence = sum(confidences) / len(confidences)
            min_confidence = min(confidences)
            max_confidence = max(confidences)

            # Weighted tier assignment based on Arcee's recommendations
            if agreement_count == 3:
                # All 3 agree: Tier A if avg confidence > 0.90
                if avg_confidence > 0.90:
                    tier = "A"
                    final_confidence = avg_confidence
                # All 3 flag but varying confidence
                elif min_confidence > 0.80 and max_confidence >= 0.88:
                    tier = "B"
                    final_confidence = avg_confidence
                else:
                    tier = "C"
                    final_confidence = avg_confidence

            elif agreement_count == 2:
                # 2 of 3 agree with confidence > 0.85: Tier B
                if min_confidence > 0.85:
                    tier = "B"
                    final_confidence = avg_confidence
                # 2 of 3 with lower confidence
                else:
                    tier = "C"
                    final_confidence = avg_confidence

            else:
                # Single model: Tier C if high confidence (>0.85), else Tier D
                if confidences[0] > 0.85:
                    tier = "C"
                else:
                    tier = "D"
                final_confidence = confidences[0]

            self.phase_2_consensus.append({
                "finding": asdict(data["finding"]),
                "consensus_tier": tier,
                "consensus_confidence": final_confidence,
                "agreement_count": agreement_count,
                "agreeing_models": models,
                "confidence_spread": {
                    "min": round(min_confidence, 3),
                    "max": round(max_confidence, 3),
                    "avg": round(avg_confidence, 3),
                },
                "disagreement_flag": agreement_count < 3,
            })

        # Sort by tier (A > B > C > D) and confidence descending
        tier_order = {"A": 0, "B": 1, "C": 2, "D": 3}
        self.phase_2_consensus.sort(
            key=lambda x: (tier_order[x["consensus_tier"]], -x["consensus_confidence"])
        )

        print(f"[{self.audit_id}] Phase 2: Complete ({len(self.phase_2_consensus)} consensus findings)")

    async def _phase_3_appellate_review(self) -> None:
        """Phase 3: Independent appellate review."""
        self.log.phase_banner("3: appellate review")
        print(f"[{self.audit_id}] Phase 3: Appellate Review (starting)")

        model_key = self.models["appellate"]
        tokens_used = 0

        system_prompt = """You are an independent security reviewer.
        Challenge the findings: are they internally coherent? Do they make sense?
        Return JSON: [{"finding_id": "...", "confidence_adjustment": -0.2, "reasoning": "..."}]"""

        for i, consensus in enumerate(self.phase_2_consensus[:10]):  # Review top 10 findings
            if tokens_used >= self.budget["appellate"]:
                self.log.info("appellate budget exhausted — stopping early",
                              phase="phase_3", findings_reviewed=i,
                              findings_total=len(self.phase_2_consensus[:10]),
                              tokens_used=tokens_used, budget=self.budget["appellate"])
                break

            finding = consensus["finding"]
            prompt = f"""Review this security finding:
            Title: {finding['title']}
            Description: {finding['description']}
            Severity: {finding['severity']}
            Evidence: {finding['evidence']}

            Is this finding credible and internally consistent?"""

            try:
                result = await self.router.call_model(
                    model_key,
                    prompt,
                    max_tokens=2000,
                    temperature=0.0,
                    system=system_prompt,
                )

                tokens_used += result["tokens_used"]

                # Parse appellate decision
                try:
                    review_json = json.loads(result["content"])
                    if isinstance(review_json, list) and len(review_json) > 0:
                        review = review_json[0]
                        self.phase_3_appellate.append({
                            "finding_id": finding["id"],
                            "confidence_adjustment": review.get("confidence_adjustment", 0),
                            "reasoning": review.get("reasoning", ""),
                            "appellate_model": result["model"],
                        })
                    else:
                        self.log.warn("appellate: LLM returned JSON but not a non-empty list — review discarded",
                                      phase="phase_3", finding_id=finding["id"],
                                      json_type=type(review_json).__name__)
                except json.JSONDecodeError as je:
                    self.log.parse_fail("phase_3_appellate_review",
                                        result["content"], je, phase="phase_3")

            except Exception as e:
                self.log.error("Appellate exception", phase="phase_3",
                               finding_id=finding.get("id", "unknown"), exc=e)
                continue

        print(f"[{self.audit_id}] Phase 3: Complete ({len(self.phase_3_appellate)} reviews)")

    async def _phase_4_source_verification(self) -> None:
        """Phase 4: Ground findings in source code and sanitize evidence."""
        self.log.phase_banner("4: source verification")
        print(f"[{self.audit_id}] Phase 4: Source Verification (starting)")

        for consensus in self.phase_2_consensus:
            finding_dict = consensus["finding"]

            # Try to read the actual file and verify
            try:
                file_path = f"{self.repo_path}/{finding_dict['file']}"
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    lines = f.readlines()
                    line_num = finding_dict["line"]
                    if 0 <= line_num < len(lines):
                        actual_code = lines[line_num].strip()
                        verified_finding = Finding(**finding_dict)
                        verified_finding.evidence = actual_code
                    else:
                        self.log.debug("line number out of range during source verification — evidence unchanged",
                                       phase="phase_4", file=finding_dict["file"],
                                       line=line_num, file_lines=len(lines))
                        verified_finding = Finding(**finding_dict)

                        # Sanitize evidence before adding to verified findings
                        verified_finding, was_redacted, reason = self._sanitize_finding(verified_finding)

                        self.phase_4_verified.append(verified_finding)

            except Exception as e:
                # File might not exist or other issue, still include finding
                verified_finding = Finding(**finding_dict)

                # Still sanitize even if file couldn't be verified
                verified_finding, was_redacted, reason = self._sanitize_finding(verified_finding)

                self.phase_4_verified.append(verified_finding)

        print(f"[{self.audit_id}] Phase 4: Complete ({len(self.phase_4_verified)} verified findings, sanitization applied)")

    def _build_report(self) -> Dict[str, Any]:
        """Build final audit report."""
        elapsed = time.time() - self.start_time

        # Count tier distribution from consensus findings
        tier_counts = {"A": 0, "B": 0, "C": 0, "D": 0}
        for consensus in self.phase_2_consensus:
            tier = consensus["consensus_tier"]
            tier_counts[tier] += 1

        self.log.info("audit complete", phase="report",
                      findings_verified=len(self.phase_4_verified),
                      findings_consensus=len(self.phase_2_consensus),
                      duration_s=round(elapsed, 2))

        return {
            "audit_id": self.audit_id,
            "repo_path": self.repo_path,
            "status": "complete",
            "timing": {
                "started_at": datetime.now().isoformat(),
                "elapsed_seconds": round(elapsed, 2),
            },
            "code_metrics": self.chunk_metadata,
            "findings": {
                "phase_1_raw": {
                    "surface_scanner": len(self.phase_1_results["surface_scanner"]),
                    "deep_reasoner": len(self.phase_1_results["deep_reasoner"]),
                    "specialist": len(self.phase_1_results["specialist"]),
                },
                "phase_2_consensus": len(self.phase_2_consensus),
                "phase_2_tier_distribution": tier_counts,
                "phase_3_reviewed": len(self.phase_3_appellate),
                "phase_4_verified": len(self.phase_4_verified),
            },
            "verified_findings": [asdict(f) for f in self.phase_4_verified],
            "consensus_findings": self.phase_2_consensus,
            "data_handling": {
                "encryption_enabled": self.data_handling_config["encryption_enabled"],
                "audit_trail_enabled": self.data_handling_config["audit_trail_enabled"],
                "evidence_sanitized": self.data_handling_config["sanitize_evidence"],
                "retention_days": self.data_handling_config["retention_days"],
            },
            "audit_log": self.audit_log,  # Full audit trail for compliance
        }

    def _log(self, entry: Dict) -> None:
        """Log audit event."""
        entry["timestamp"] = datetime.now().isoformat()
        self.audit_log.append(entry)

    def _sanitize_evidence(self, evidence: str) -> tuple[str, bool, str]:
        """
        Sanitize evidence to remove secrets and PII.

        Returns: (sanitized_evidence, was_redacted, redaction_reason)
        """
        if not self.data_handling_config["sanitize_evidence"]:
            return evidence, False, ""

        original_evidence = evidence
        redaction_reason = []

        # AWS Key pattern: AKIA followed by 16 alphanumerics
        evidence = re.sub(
            r"AKIA[0-9A-Z]{16}",
            "[REDACTED:AWS_KEY]",
            evidence
        )
        if evidence != original_evidence:
            redaction_reason.append("aws_key")

        # Private key markers
        evidence = re.sub(
            r"-----BEGIN [A-Z ]+ PRIVATE KEY-----[^-]+-----END [A-Z ]+ PRIVATE KEY-----",
            "[REDACTED:PRIVATE_KEY]",
            evidence
        )

        # API keys (Bearer, Token, api_key patterns)
        evidence = re.sub(
            r"(api[_-]?key|token|bearer|authorization)\s*[=:]\s*['\"]?[^\s'\"]+['\"]?",
            r"\1 = '[REDACTED:API_KEY]'",
            evidence,
            flags=re.IGNORECASE
        )
        if evidence != original_evidence:
            redaction_reason.append("api_key")

        # Email addresses
        evidence = re.sub(
            r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
            "[REDACTED:EMAIL]",
            evidence
        )
        if evidence != original_evidence:
            redaction_reason.append("email")

        # Phone numbers (US format)
        evidence = re.sub(
            r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b",
            "[REDACTED:PHONE]",
            evidence
        )
        if evidence != original_evidence:
            redaction_reason.append("phone")

        # Social Security numbers
        evidence = re.sub(
            r"\b\d{3}-\d{2}-\d{4}\b",
            "[REDACTED:SSN]",
            evidence
        )
        if evidence != original_evidence:
            redaction_reason.append("ssn")

        was_redacted = evidence != original_evidence
        reason = ", ".join(redaction_reason) if redaction_reason else ""

        return evidence, was_redacted, reason

    def _sanitize_finding(self, finding: Finding) -> tuple[Finding, bool, str]:
        """Sanitize a finding's evidence field and return updated finding."""
        sanitized_evidence, was_redacted, reason = self._sanitize_evidence(finding.evidence)

        if was_redacted:
            finding.evidence = sanitized_evidence
            self._log({
                "phase": "sanitization",
                "finding_id": finding.id,
                "action": "evidence_redacted",
                "redaction_reason": reason,
            })

        return finding, was_redacted, reason
