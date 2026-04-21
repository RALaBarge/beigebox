# Trinity Pipeline - Data Handling & Security Specifications

**Status**: Production Security Standards  
**Effective Date**: April 20, 2026  
**Compliance**: OWASP Top 10, Data Privacy Best Practices

---

## 1. Data Classification

### Input Data (Code Under Analysis)
- **Classification**: Confidential / Proprietary
- **Sensitivity**: High (may contain business logic, secrets, proprietary algorithms)
- **Handling**: Ephemeral (temporary, immediate deletion post-analysis)
- **Examples**: Source code files, configuration files, .gitignore patterns

### Findings (Audit Results)
- **Classification**: Confidential / Internal Use Only
- **Sensitivity**: High (contains vulnerability descriptions, code locations, evidence)
- **Handling**: Persistent (retained per policy, encrypted at rest)
- **Examples**: Security findings, confidence scores, severity levels, code evidence

### Metadata
- **Classification**: Internal
- **Sensitivity**: Medium (audit timestamps, model names, chunk counts)
- **Handling**: Persistent (logged and retained per policy)
- **Examples**: audit_id, start_time, elapsed_time, models_used, token_counts

---

## 2. Encryption Requirements

### Data In Transit
- **Protocol**: HTTPS/TLS 1.3 minimum
- **Requirement**: All communication with LLM providers (Anthropic, OpenRouter) must use encrypted channels
- **Implementation**:
  - Direct Anthropic API calls: Use anthropic library (auto-TLS)
  - OpenRouter calls via BeigeBbox: Validate HTTPS only, no HTTP fallback
  - Validation: Check certificate validity on every request
- **Enforcement**: Fail audit if any unencrypted transmission attempted

### Data At Rest
- **Standard**: AES-256-GCM encryption
- **Scope**: All audit reports, findings, and code evidence stored on disk
- **Key Management**:
  - Master encryption key stored in environment variable (BB_ENCRYPTION_KEY)
  - Key rotation policy: Every 90 days or on compromise
  - Key derivation: PBKDF2 with 100,000 iterations minimum
- **Implementation**:
  - Use cryptography.fernet for symmetric encryption
  - Each audit report encrypted separately
  - Initialization vector (IV) stored with ciphertext (not secret)
- **File Format**: JSON encrypted as base64-encoded binary blob

### Exception: Ephemeral Code Data
- **Rule**: Code chunks submitted to LLMs are NOT encrypted at rest (they're not stored)
- **Reasoning**: Code is only in memory during analysis, deleted immediately after LLM processing
- **Verification**: Confirm no code artifacts written to disk in pipeline.py

---

## 3. Data Retention Policy

### Input Code
- **Retention Period**: 0 seconds (ephemeral)
- **Implementation**: Never write code to disk, only hold in memory
- **Deletion**: Garbage collected after LLM call completes
- **Verification**: Audit log confirms no file I/O for code chunks

### Audit Reports & Findings
- **Retention Period**: 90 days by default, configurable per organization
- **Deletion Method**: Secure deletion (overwrite 3x with random data before deletion)
- **Override Options**:
  - Permanent retention (for compliance/legal holds)
  - Custom retention window (30-365 days)
  - Immediate deletion (security-conscious orgs)
- **Configuration**: Trinity audit config accepts `retention_days` parameter

### Audit Logs
- **Retention Period**: 180 days (2x findings retention for forensics)
- **Deletion Method**: Same secure deletion as reports
- **Immutability**: Logs cannot be modified post-write (append-only)

### Exception: Failed Audits
- **Definition**: Audits that encountered errors and did not complete
- **Retention**: 30 days only (debugging purpose only)
- **Deletion**: Automatic after 30 days

---

## 4. Comprehensive Audit Trail

### What Gets Logged

Every audit action is logged with:
- **Timestamp**: ISO 8601 UTC timezone
- **Event Type**: One of: AUDIT_START, LLM_CALL, FINDING_CREATED, FINDING_REVIEWED, AUDIT_COMPLETE, ERROR
- **Actor**: Model name (haiku, grok-4.1-fast, qwen-max) or "system"
- **Resource**: File being analyzed, finding ID, or audit ID
- **Action**: Specific operation (e.g., "analyzed_chunk", "generated_finding", "consensus_vote")
- **Result**: Success/failure, token count, latency
- **Access Context**: IP address (if available), session ID, user identity

### Audit Log Entry Schema

```json
{
  "timestamp": "2026-04-20T19:45:23.456Z",
  "audit_id": "trinity-abc123def456",
  "event_type": "LLM_CALL",
  "actor": "grok-4.1-fast",
  "resource": "src/app.py (lines 100-150)",
  "action": "deep_reasoner_analysis",
  "tokens_used": 2847,
  "latency_ms": 8400,
  "result": "success",
  "finding_count": 3,
  "user_ip": "192.168.1.100",
  "access_level": "admin"
}
```

### Audit Trail Access Control
- **Read Access**: Only audit owner, security team leads, legal hold requests
- **Write Access**: System only (append-only, no modification)
- **Admin Access**: Database administrator, audit compliance officer
- **Query Logging**: Who queried which audits, when, and what they viewed

### Implementation
- Store in dedicated audit log table (SQLite or PostgreSQL)
- Index on: audit_id, timestamp, event_type, actor
- Backup: Separate backup stream (encrypted, immutable)

---

## 5. Output Sanitization

### Finding Evidence Handling
- **Rule**: Evidence field contains actual source code line
- **Sanitization**: Remove or redact:
  - Hardcoded credentials, API keys, secrets (regex-based detection)
  - PII (email addresses, phone numbers, social security numbers)
  - Proprietary business logic identifiers
  - Internal service names or infrastructure details

### Sanitization Process
1. **Detection**: Pattern matching for known secret formats
   - AWS keys: `AKIA[0-9A-Z]{16}`
   - Private keys: `-----BEGIN.*PRIVATE KEY-----`
   - API tokens: Common patterns (Bearer, Token, api_key values)
   - Email addresses: Standard regex
2. **Redaction**: Replace with `[REDACTED:TYPE]` (e.g., `[REDACTED:AWS_KEY]`)
3. **Logging**: Log what was redacted (for audit trail) without logging the secret itself

### Evidence Examples (Post-Sanitization)

```
BEFORE:
  "evidence": "password = 'super_secret_123'; api_key = 'sk-1234567890abcdef'"

AFTER:
  "evidence": "password = '[REDACTED:PASSWORD]'; api_key = '[REDACTED:API_KEY]'"
```

### Severity Impact
- If evidence cannot be redacted safely (too much PII), downgrade finding severity by 1 level
- Document redaction in finding: `evidence_redacted: true, redaction_reason: "contains_pii"`

---

## 6. LLM Data Handling Agreements

### Anthropic (Haiku - Direct API)
- **Data Handling**: Default Anthropic privacy policy applies
- **Training Data**: By default, Anthropic may use API inputs for model improvement
- **Opt-Out**: Set `anthropic_request_header: "anthropic-no-training"` to disable training use
- **Verification**: Confirm header in all _call_anthropic requests
- **Risk Level**: Low (first-party provider, SOC 2 certified)

### OpenRouter (Grok, Arcee, Qwen, Deepseek)
- **Data Handling**: OpenRouter's privacy policy is primary
- **Key Provisions**:
  - Code is routed through OpenRouter proxy
  - Different upstream providers (xAI, Arcee, Alibaba, Deepseek) may have different policies
  - Upstream providers may use data for model improvement (default assumption)
- **Upstream Mapping**:
  - Grok 4.1 Fast: xAI (likely training use)
  - Arcee Trinity Large: Arcee AI (enterprise model, assumed no training use)
  - Qwen Max: Alibaba (training use likely)
  - Deepseek Coder: Deepseek (training use likely)
- **Recommendation**: For sensitive code, use Arcee (enterprise) or direct Anthropic only
- **Verification**: Document upstream provider for each request in audit log

### Required Written Agreement
Before production deployment, obtain written confirmation from:
- OpenRouter: Data handling practices, retention, deletion guarantees
- Each upstream provider: Training data usage, retention periods, data deletion SLAs
- Document in: Trinity security folder, reference in audit reports

---

## 7. Access Control

### Who Can Access Audit Data?
- **Audit Owner**: Always (can view all phases, download reports)
- **Security Team**: Leads can view findings tier A/B only (not raw model outputs)
- **Developers**: Can view findings for their own code (tier A only)
- **Admin**: Can view everything (with full audit trail)
- **Legal/Compliance**: Full access on subpoena or legal hold

### Implementation
- RBAC (Role-Based Access Control) in Trinity configuration
- All access logged with user identity, timestamp, resource accessed
- Approval workflow for access above baseline permissions
- Time-limited access grants (max 30 days, auto-revoke)

---

## 8. Compliance & Standards

### Standards Alignment
- **OWASP Top 10**: Follows A02:2021 Cryptographic Failures, A05:2021 Access Control
- **SOC 2 Type II**: Encryption, audit trails, access control
- **GDPR**: Data minimization (ephemeral code), right to deletion (reports), audit trails
- **ISO 27001**: Information security management, encryption, access control

### Required Before Production
- [ ] Encrypt all findings at rest (AES-256)
- [ ] Implement audit trail (100% coverage)
- [ ] Sanitize findings evidence (secrets redacted)
- [ ] Document LLM data handling agreements (written)
- [ ] Define retention policy (configurable)
- [ ] Implement secure deletion (3x overwrite)
- [ ] RBAC configuration (roles defined)
- [ ] Compliance attestation (sign-off by security lead)

---

## 9. Implementation Checklist

### Phase 1: Encryption (Week 1)
- [ ] Add encryption/decryption utilities
- [ ] Wrap all finding serialization with encryption
- [ ] Test encryption round-trip (encrypt → serialize → encrypt)
- [ ] Verify audit reports unreadable without key

### Phase 2: Audit Trail (Week 1-2)
- [ ] Add audit log schema to database
- [ ] Add logging calls to all 4 phases
- [ ] Test audit trail completeness (sample audit, verify all actions logged)
- [ ] Implement audit log querying / search

### Phase 3: Output Sanitization (Week 2)
- [ ] Build secret/PII regex patterns
- [ ] Add sanitization to finding evidence field
- [ ] Test redaction (confirm secrets removed)
- [ ] Document redacted fields in findings

### Phase 4: Configuration & Documentation (Week 2-3)
- [ ] Add retention_days config parameter
- [ ] Add encryption_key configuration
- [ ] Document LLM data handling (get written agreements)
- [ ] Implement access control roles
- [ ] Create compliance attestation template

---

## 10. Testing Requirements

### Encryption Tests
- Verify AES-256 encryption/decryption with various key sizes
- Confirm encrypted files are unreadable without key
- Test key rotation (old findings still decrypt with new key)

### Audit Trail Tests
- Run sample audit, verify every LLM call is logged
- Check log format consistency and timestamp accuracy
- Verify audit logs are immutable (append-only)

### Sanitization Tests
- Test with files containing known secrets (AWS keys, API tokens, PII)
- Verify redaction doesn't break finding coherence
- Test edge cases (secret in comment, secret split across lines)

### Access Control Tests
- Verify only authorized roles can view findings
- Test access denial (attempt unauthorized view, confirm rejection)
- Verify all access logged (timestamp, user, resource, result)

---

**Document Status**: Ready for Implementation  
**Next Steps**: Implement Phase 1-4 per checklist above  
**Owner**: Security Team  
**Review Date**: 60 days post-implementation
