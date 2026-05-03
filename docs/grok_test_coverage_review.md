# Grok test-coverage review — 2026-05-02

Source: `x-ai/grok-4.3-20260430` via BeigeBox proxy (xAI).
Inventory of 65 test files generated 2026-05-03 02:24:35Z by automated agent.
Usage: prompt_tokens=4335, completion_tokens=1938, total_tokens=6273, cost=$0.01019655.

---

## Inventory sent to Grok

### Storage / Repos / DB

- **test_storage.py** (259 LOC) — Tests for SQLite storage.
  Top tests: test_store_and_retrieve, test_multiple_messages, test_separate_conversations, test_stats, test_stats_tokens
- **test_storage_db.py** (499 LOC) — Tests for the generic SQL shim at beigebox/storage/db/.
  Top tests: test_factory_unknown_backend, test_factory_returns_basedb, test_build_db_kwargs_default_sqlite, test_build_db_kwargs_postgres_from_cfg, test_build_db_kwargs_postgres_falls_back_to_env
- **test_api_key_repo.py** (124 LOC) — Tests for ApiKeyRepo — the first per-entity repo on top of BaseDB.
  Top tests: test_create_returns_id_and_plain_key, test_list_for_user_shows_created_key, test_list_for_user_empty_for_unknown_user, test_list_for_user_returns_only_own_keys, test_revoke_deactivates_key
- **test_user_repo.py** (89 LOC) — Tests for UserRepo (BaseDB-backed user storage).
  Classes: TestUpsert, TestGet, TestUpdatePassword, TestSchema
- **test_conversation_repo.py** (358 LOC) — Tests for ConversationRepo (BaseDB-backed conversation+message storage).
  Classes: TestSchema, TestEnsureConversation, TestStoreMessage, TestStoreCapturedRequest, TestStoreCapturedResponse, TestGetConversation, TestGetRecentConversations, TestForkConversation, TestExports, TestGetStats, TestGetModelPerformance, TestIntegrityRoundTrip
- **test_wire_event_repo.py** (131 LOC) — Tests for WireEventRepo (BaseDB-backed wire-events storage).
  Classes: TestLog, TestQuery, TestSchema
- **test_wire_sink.py** (160 LOC) — Tests for WireSink ABC and built-in implementations.
  Top tests: test_null_sink_is_wire_sink, test_null_sink_write_does_nothing, test_jsonl_sink_writes_line, test_jsonl_sink_appends_multiple, test_jsonl_sink_rotation
- **test_postgres_wire_sink.py** (149 LOC) — Tests for PostgresWireSink + per-sink fault isolation in WireLog.
  Classes: FakeRepo, TestPostgresWireSink, CapturingSink, BrokenSink, TestPerSinkFaultIsolation
- **test_wirelog_capture.py** (255 LOC) — Tests for ``WireLog.write_request`` / ``write_response`` helpers.
  Classes: _CapturingSink, TestWriteRequest, TestWriteResponse
- **test_messages_schema_migration.py** (197 LOC) — Tests for the v1.4 messages-schema migration.
  Top tests: test_legacy_db_gets_v14_columns_added, test_legacy_row_survives_with_null_v14_fields, test_migration_is_idempotent, test_fresh_db_has_v14_columns_from_create, test_v14_columns_accept_inserts
- **test_costs.py** (156 LOC) — Tests for cost tracking.
  Top tests: test_schema_has_cost_column, test_migration_idempotent, test_store_message_with_cost, test_store_message_without_cost, test_stats_include_cost
- **test_replay.py** (167 LOC) — Tests for conversation replay.
  Top tests: test_replay_empty_conversation, test_replay_basic, test_replay_includes_cost, test_replay_stats, test_replay_with_routing_decisions

### Capture pipeline / Normalizer boundary

- **test_capture.py** (296 LOC) — Unit tests for beigebox.capture envelope + factories.
  Classes: TestCapturedRequestFromNormalized, TestCapturedResponseFromNormalized, TestCapturedResponseFromPartial, TestAttachResponseTiming
- **test_capture_fanout.py** (246 LOC) — Tests for CaptureFanout — verifies sinks receive the right slices and
  Classes: FakeConversations, FakeWire, FakeVector, TestCaptureRequest, TestCaptureResponse
- **test_capture_sqlite_integration.py** (225 LOC) — Integration tests for ConversationRepo.store_captured_request/_response.
  Classes: TestStoreCapturedRequest, TestStoreCapturedResponse
- **test_proxy_capture_integration.py** (424 LOC) — End-to-end integration tests for proxy ↔ CaptureFanout (non-streaming).
  Classes: FakeRouter, FakeVector, TestNonStreamingCapture, FakeStreamingRouter, TestStreamingCapture
- **test_request_normalizer.py** (546 LOC) — Tests for the request normalizer.
  Top tests: test_is_reasoning_model_positive, test_is_reasoning_model_negative, test_is_reasoning_model_custom_markers, test_coerce_messages_none_to_empty, test_coerce_messages_dict_wrapped
- **test_response_normalizer.py** (327 LOC) — Tests for the response normalizer.
  Top tests: test_coerce_none_to_empty_string, test_coerce_string_passthrough, test_coerce_text_parts_joined, test_coerce_image_part_skipped, test_coerce_dict_serialized_as_json
- **test_normalizer_refinements.py** (548 LOC) — Tests for the panel-convergent refinements applied to request_normalizer
  Top tests: test_normalize_request_does_not_mutate_caller_body, test_normalize_request_deepcopy_isolates_nested_lists, test_normalize_request_docstring_documents_pipeline_order, test_finalize_stream_concatenates_content_and_reasoning, test_finalize_stream_handles_empty_iterable
- **test_log_events.py** (376 LOC) — Unit tests for beigebox.log_events.
  Classes: TestLogEventContext, TestRequestLifecycleEvent, TestToolExecutionEvent, TestErrorEvent, TestRoutingEvent, TestPayloadEvent, TestHookExecutionEvent, TestIdentityFields, TestEmitDispatch
- **test_tool_call_extractors.py** (303 LOC) — Tests for beigebox.tool_call_extractors and its integration into
  Top tests: test_anthropic_function_calls_xml_single_well_formed, test_anthropic_xml_two_invocations_in_one_response, test_anthropic_tool_use_xml_with_json_input, test_explicit_marker_pipe_style, test_fenced_tool_call_explicit_hint

### Proxy / Backends / Routing

- **test_proxy.py** (38 LOC) — Tests for proxy layer and data models.
  Top tests: test_message_creation, test_message_openai_format, test_message_token_count
- **test_v3_thin_proxy.py** (313 LOC) — Golden end-to-end tests for the v3 thin proxy.
  Top tests: test_v1_models_returns_catalog, test_beigebox_stats_returns_200_no_decision_llm_block, test_api_config_returns_200_no_operator_or_decision_blocks, test_mcp_initialize_handshake, test_mcp_tools_list_returns_some_tools
- **test_proxy_injection.py** (137 LOC) — Tests for proxy generation parameter injection.
  Classes: TestInjectGenerationParams
- **test_backends.py** (256 LOC) — Tests for multi-backend router.
  Top tests: test_backend_response_ok, test_backend_response_cost, test_ollama_backend_init, test_openrouter_env_resolution, test_openrouter_cost_extraction
- **test_backend_model_paths.py** (291 LOC) — Tests for backend model path resolution.
  Top tests: test_model_path_default, test_model_path_ollama_data_env, test_model_path_models_path_env, test_model_path_model_paths_list, test_model_path_model_paths_fallback
- **test_aliases.py** (40 LOC) — Tests for model alias resolver.
  Top tests: test_resolves_alias, test_passthrough_unknown, test_empty_string_passthrough, test_no_aliases_configured, test_list_aliases
- **test_model_advertising.py** (143 LOC) — Test model advertising / name transformation feature.
  Top tests: test_model_advertising_hidden_mode, test_model_advertising_advertise_mode, test_model_advertising_custom_prefix, test_model_advertising_defaults, test_model_advertising_malformed_response
- **test_v08.py** (405 LOC) — Tests for v0.8.0 features.
  Top tests: test_fork_copies_all_messages, test_fork_branch_at_limits_messages, test_fork_does_not_share_ids, test_fork_empty_source_returns_zero, test_fork_source_unchanged

### Auth / Web / UI

- **test_auth.py** (188 LOC) — Tests for beigebox/auth.py — MultiKeyAuthRegistry.
  Classes: TestIsEnabled, TestValidate, TestCheckModel, TestCheckEndpoint, TestRateLimit, TestNamedKeyACL
- **test_web_auth_factory.py** (73 LOC) — Tests for the AuthProvider ABC, NullAuthProvider, and make_auth() factory in web_auth.py.
  Classes: TestAuthProviderABC, TestNullAuthProvider, TestMakeAuth
- **test_web_ui.py** (409 LOC) — Tests for web UI config endpoint and vi mode toggle.
  Classes: TestUpdateRuntimeConfig, TestConfigEndpointWebUi, TestToggleViModeEndpoint, TestWebFileServing
- **test_workspace.py** (261 LOC) — Tests for workspace API endpoints:
  Classes: TestApiWorkspaceList, TestApiWorkspaceDelete, TestApiWorkspaceUpload
- **test_static.py** (616 LOC) — Tests for the static skill (beigebox.skills.static).
  Classes: _FakeProc, _FakeProcMypy

### Security (RAG poisoning, injection, anomaly, memory integrity)

- **test_api_anomaly_detector.py** (923 LOC) — Tests for API Anomaly Detector (P1-C Security Hardening).
  Classes: TestZScoreCalculation, TestRiskScoreComputation, TestRecommendedAction, TestSensitivityPresets, TestRequestRateDetection, TestErrorRateDetection, TestModelSwitchingDetection, TestLatencyAnomalyDetection, TestPayloadSizeDetection, TestUAInstabilityDetection, TestEdgeCases, TestFalsePositiveHandling, TestAnalyzeMethod, TestFullPipeline, TestBaselinePersistence, TestAPIAnomalyDetectorTool, TestAnomalyRules, TestAnomalyReport
- **test_enhanced_injection_guard.py** (383 LOC) — Tests for EnhancedInjectionGuard (P1-A).
  Classes: TestPatternLibrary, TestSemanticAnalysis, TestContextAnalysis, TestConfidenceScoring, TestRiskLevels, TestAdaptiveLearning, TestEdgeCases, TestPerformance, TestResultSerialization
- **test_extraction_detector.py** (622 LOC) — Tests for Extraction Detector (OWASP LLM10:2025 Model Extraction Prevention).
  Classes: TestQueryDiversityDetection, TestInstructionPatternDetection, TestTokenDistributionAnalysis, TestPromptInversionDetection, TestSessionTracking, TestRiskScoring, TestFalsePositiveValidation, TestIntegration, TestEdgeCases
- **test_memory_integrity.py** (467 LOC) — Tests for memory integrity validation (HMAC-SHA256 signatures).
  Classes: TestConversationIntegrityValidator, TestKeyManager, TestSQLiteStoreIntegrity, TestBackwardCompatibility
- **test_memory_validator.py** (1229 LOC) — Tests for the Memory Validator system (P1-D: Agent Memory Validator).
  Classes: TestHMACGeneration, TestSignatureFormat, TestKeyManagement, TestValidatorInit, TestMemoryValidator, TestMemoryValidationResult, TestMigration, TestIntegrationCycle, TestTamperingEdgeCases, TestKeyRotation, TestMemoryValidatorTool, TestIntegrityAuditLog, TestBatchValidation
- **test_rag_content_scanner.py** (477 LOC) — Tests for RAGContentScanner (P1-B).
  Classes: TestInstructionPatterns, TestContentFeatureExtraction, TestMetadataValidation, TestSemanticAnomalyDetection, TestScanIntegration, TestRiskLevelAssignment, TestQuarantine, TestContentHash, TestEdgeCases, TestPerformance, TestResultSerialization
- **test_rag_deployment.py** (939 LOC) — Tests for RAG Poisoning Defense — Production Deployment.
  Classes: TestBaselineCalibration, TestThresholdTuning, TestDeploymentStages, TestFalsePositiveValidation, TestMonitoringMetrics, TestIntegration, TestEdgeCases, TestAcceptanceCriteria
- **test_rag_integration.py** (321 LOC) — Integration tests for RAGPoisoningDetector integration with BeigeBox.
  Top tests: test_poisoning_detector_initialization, test_vector_store_accepts_poisoning_detector, test_vector_store_works_without_detector, test_config_loads_poisoning_detection, test_detector_rejects_zero_embedding
- **test_rag_monitoring.py** (407 LOC) — Tests for RAG poisoning monitoring & quarantine system.
  Classes: TestQuarantineRepo, TestPoisoningMetrics, TestCLICommands, TestVectorStoreIntegration, TestMetricsEndpoint
- **test_rag_poisoning_detector.py** (448 LOC) — Tests for RAG Poisoning Detector.
  Classes: TestDetectorInitialization, TestBaselineCalculation, TestPoisoningDetection, TestSyntheticPoisoningScenarios, TestFalsePositiveRate, TestThreadSafety, TestEdgeCases, TestStatisticsReporting
- **test_rag_poisoning_integration.py** (284 LOC) — Integration tests for RAG poisoning detection with ChromaBackend.
  Classes: TestChromaBackendDetection, TestPoisoningScenarios, TestPerformance, TestConfigIntegration
- **test_tool_call_validator.py** (344 LOC) — Tests for ToolCallValidator (P1-D).
  Classes: TestInjectionPatterns, TestRateLimiting, TestNamespaceIsolation, TestParameterValidation, TestValidationResult, TestIntegration, TestEdgeCases
- **test_parameter_validation.py** (691 LOC) — Test suite for MCP Parameter Validation (Phase 1).
  Classes: TestParameterValidator, TestInjectionDetector, TestPydanticSchemas, TestValidationIntegration, TestAttackPayloads

### Tools (MCP / agent tools)

- **test_tools.py** (21 LOC) — Tests for tool modules.
  Top tests: test_google_search_mock_mode, test_google_search_detects_real_mode
- **test_new_tools.py** (117 LOC) — Tests for calculator, datetime, system_info, and memory tools.
  Classes: TestCalculator, TestDateTime, TestSystemInfo, TestMemory
- **test_memory_tool.py** (166 LOC) — Tests for beigebox/tools/memory.py — MemoryTool with query preprocessing.
  Classes: TestMemoryToolRun, TestPreprocessDisabled, TestPreprocessEnabled
- **test_bluetruth_scenarios.py** (360 LOC) — BlueTruth Integration Test Scenarios
  Classes: TestBasicOperations, TestQueryOperations, TestDeviceTracking, TestEdgeCases, TestFullScenarios, TestDatabaseIntegrity
- **test_network_audit.py** (533 LOC) — NetworkAuditTool test suite
  Classes: TestOuiLookup, TestArpCacheParsing, TestPortResolution, TestServiceFingerprinting, TestCveLookup, TestPortFindingAssessment, TestHostRiskLevel, TestSummaryBuilder, TestToolCommandDispatch
- **test_research_agent_flexible.py** (566 LOC) — Tests for ResearchAgentFlexibleTool — backend-agnostic research agent.
  Classes: TestParseBackendSpec, TestBuildAdapter, TestAuthHandling, TestAdapterFallback, TestResearchLoop, TestRunInterface, TestCrossBackendConsistency, TestAnthropicFormatTranslation, TestBeigeBoxRouterAdapter, TestFindingsParsing
- **test_web_scraper.py** (174 LOC) — Tests for WebScraperTool — URL validation, HTML saving, and RAG embedding.
  Classes: TestValidateUrl, TestRun, TestSaveHtml, TestEmbedText
- **test_zip_inspector.py** (151 LOC) — Tests for plugins/zip_inspector.py — zip archive inspection.
  Classes: TestFmtSize, TestBuildTree, TestZipInspectorTool
- **test_system_info_shell.py** (129 LOC) — Tests for operator shell.enabled gate in system_info._run().
  Classes: TestShellDisabledGate, TestAllowlistEnforcement, TestShellGateExceptionSafety

### Skills (fanout / fuzz)

- **test_fanout.py** (431 LOC) — Tests for the fanout skill (beigebox.skills.fanout).
  Top tests: test_render_string_item, test_render_dict_item_dotted_field, test_render_dict_item_whole, test_render_index, test_render_unknown_placeholder_left_literal
- **test_fuzz.py** (375 LOC) — Tests for the fuzz skill (beigebox.skills.fuzz).
  Classes: TestRiskScorer, TestFunctionExtractor, TestSeedCorpusExtractor, TestCrashClassifier, TestSmartHarnessGenerator, TestAdaptiveTimeAllocator, TestFuzzer, TestEndToEnd

### Hooks / Observability / Webhooks / Cache

- **test_hooks.py** (99 LOC) — Tests for the hooks system.
  Top tests: test_load_hooks_from_directory, test_pre_request_hooks, test_broken_hook_doesnt_break_pipeline, test_post_response_hooks, test_empty_hooks_dir
- **test_observability_egress.py** (299 LOC) — Tests for observability egress (beigebox/observability/egress.py).
  Top tests: test_build_egress_hooks_empty, test_build_egress_hooks_no_url, test_build_egress_hooks_creates_instances
- **test_webhook_emitter.py** (331 LOC) — Tests for hooks/webhook_emitter.py.
  Classes: _Collector
- **test_cache.py** (71 LOC) — Tests for beigebox/cache.py — ToolResultCache.
  Classes: TestToolResultCache

### Guardrails / System context / Wasm

- **test_guardrails.py** (164 LOC) — Tests for the Guardrails engine.
  Top tests: test_disabled_allows_everything, test_keyword_block, test_keyword_case_insensitive, test_keyword_allow_clean, test_topic_block
- **test_system_context.py** (233 LOC) — Tests for system_context.py — hot-reloadable global prompt injection.
  Classes: TestGetSystemContext, TestInjectSystemContext, TestFileIO
- **test_wasm_runtime.py** (336 LOC) — Tests for WasmRuntime: reload(), default_module property, disabled-path behaviour,
  Classes: TestWasmRuntimeDisabled, TestWasmRuntimeEnabled, TestWasmReloadEndpoint, TestConfigEndpointWasm

---

## Grok's response

## 1. Rules of thumb

- **Capture fidelity is the core contract.** Every test suite for an LLM proxy should verify that the request/response pair can be reconstructed losslessly from the capture layer (normalizer → CaptureFanout → repos + vector store). Tests should focus on round-tripping rather than just "it didn't crash."
- **Security subsystems must be tested both in isolation and at the injection point.** RAG poisoning, injection guards, and anomaly detectors should have dedicated unit tests *and* integration tests that show they actually block or quarantine traffic in the proxy pipeline.
- **Backend abstraction boundaries need explicit failure testing.** Since the system routes across OpenRouter, Ollama, and others, tests should cover timeout, malformed response, partial stream, and cost extraction failures per backend rather than just happy-path responses.
- **Wire log and observability must be side-effect verified.** Tests should assert that `WireLog`, vector store writes, and event emission happen (or are correctly skipped) under error conditions, not just successful requests.
- **Auth and routing decisions should be tested as policy enforcement, not just unit logic.** `MultiKeyAuthRegistry` and model advertising should be tested through the actual request path to verify enforcement, not just the registry methods in isolation.
- **Streaming and non-streaming paths must be treated as separate contracts.** The capture, normalization, and tool extraction logic diverge significantly between the two; tests should not rely on one path to validate the other.

## 2. Well-covered

- **Storage layer and schema evolution**: The repo tests (`test_conversation_repo.py`, `test_api_key_repo.py`, `test_user_repo.py`, `test_wire_event_repo.py`) plus migration tests (`test_messages_schema_migration.py`, `test_costs.py`) provide strong coverage of the `BaseDB` shim and per-entity persistence.
- **Capture pipeline and normalizers**: `test_capture.py`, `test_capture_fanout.py`, `test_request_normalizer.py`, `test_response_normalizer.py`, and `test_normalizer_refinements.py` give solid coverage of the normalization boundary and fanout to multiple sinks.
- **Security subsystems**: The RAG poisoning suite (`test_rag_poisoning_detector.py`, `test_rag_deployment.py`, `test_rag_integration.py`), anomaly detection (`test_api_anomaly_detector.py`), memory integrity (`test_memory_integrity.py`, `test_memory_validator.py`), and injection guards are extensively tested, including edge cases and false positive handling.
- **Tool implementations**: Several individual tools have dedicated test files (`test_memory_tool.py`, `test_web_scraper.py`, `test_network_audit.py`, `test_research_agent_flexible.py`).
- **Cost tracking and replay**: `test_costs.py` and `test_replay.py` cover the financial and reconstruction aspects of captured conversations.

## 3. Coverage gaps

- **Core proxy endpoints**: No visible tests for `/v1/chat/completions` error paths (invalid model, backend 5xx, rate limit rejection), streaming error recovery, or partial response handling. `test_proxy_capture_integration.py` covers happy-path capture but not error propagation.
- **MCP server protocol**: Only `test_v3_thin_proxy.py` touches MCP initialization and tool listing. There are no tests for tool call execution, parameter validation failures at the MCP boundary, or session state management.
- **Authentication enforcement in the request path**: `test_auth.py` tests the registry in isolation. There are no integration tests showing a request being rejected at the FastAPI layer due to invalid keys, model restrictions, or endpoint ACLs.
- **Backend failure modes**: `test_backends.py` and `test_backend_model_paths.py` focus on initialization and happy paths. Missing: timeout handling, streaming interruption, cost extraction on failed responses, and fallback routing behavior.
- **Rate limiting and quotas**: `test_auth.py` has `TestRateLimit` but no tests showing actual request blocking or header emission under sustained load.
- **Wire log completeness under failure**: `test_wirelog_capture.py` and `test_wire_sink.py` exist, but there are no tests verifying that failed or aborted requests still produce usable wire events.
- **Configuration and hot-reload**: `test_system_context.py` and `test_wasm_runtime.py` touch some reload behavior, but there is no coverage of runtime config changes affecting routing, auth, or security thresholds.

## 4. Smells

- **Oversized security test files**: `test_memory_validator.py` (1229 LOC) and `test_api_anomaly_detector.py` (923 LOC) suggest heavy testing of internal classes and implementation details rather than observable behavior at the system boundary.
- **Implementation-coupled test names**: Many repo tests use class names like `TestSchema`, `TestEnsureConversation`, and `TestStoreCapturedRequest` that mirror method names instead of describing user-visible behavior.
- **Golden file risk in `test_v3_thin_proxy.py`**: The "golden" end-to-end tests for the thin proxy are likely brittle to response format changes from upstream providers.
- **Suspiciously small core proxy tests**: `test_proxy.py` is only 38 LOC while the project is fundamentally a proxy. This indicates the actual FastAPI request handling is under-tested relative to its importance.
- **Isolated tool tests without pipeline integration**: Most tool tests (`test_new_tools.py`, `test_zip_inspector.py`) test the tools in isolation. There is little evidence of tests exercising tool calls through the actual proxy + capture path.
- **Static analysis in the test suite**: `test_static.py` (616 LOC) contains mypy-related fakes (`_FakeProcMypy`). This is unusual for a runtime test suite and suggests either misplaced linting or an attempt to test type checking behavior.
