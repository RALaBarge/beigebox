# ⚠️ PARTIAL — mDNS/DNS-SD (zeroconf) + SPIFFE identity + NATS heartbeat implemented in amf_mesh.py. AsyncAPI contract definition and MQTT broker not implemented. Low priority until federated multi-node deployment is needed.

# Existing Protocols for Agent Fabric Implementation

This document surveys existing standards and protocols you can repurpose for building an agent fabric—grouped by architectural layer, with notes on how each maps to your agent fabric concept.

---

## Discovery Layer (aDNS)

### DNS-SD + mDNS (RFC 6763)

You already mentioned this, but consider the `_sub` service type suffixes for capability filtering. Advertise as `_agent._tcp` with subtype `_agent._sub._ocr._tcp` for an OCR specialist. The TXT record can hold your minimal agent card URL (under 1300 bytes to avoid fragmentation).

### Bluetooth GATT / BLE Advertising

For physical proximity discovery (devices in the same room). Use the Manufacturer Specific Data field to broadcast a compressed agent ID and capability hash. The local stack scans passively—no pairing required. Useful for "walking stack" scenarios where you want to discover agents on your person (watch, glasses, phone) without Wi-Fi.

### DNS-SD over Unicast (RFC 6764)

When you leave the local link, you don't need a new protocol—just register your agents in a private DNS zone (e.g., `agent-7f3a._agent.user.local`) with SRV and TXT records. This scales to VPNs and segmented networks without abandoning the discovery semantics.

### Service Location Protocol (SLP, RFC 2608)

Largely abandoned, but the "Service URL" syntax (`service:agent://capability=ocr;lang=en`) is a clean way to encode capability advertisements in any text-based discovery system.

---

## Event Fabric (The Bus)

### MQTT (ISO/IEC 20922)

The strongest candidate for your event fabric. It's lightweight, has hierarchical topics (perfect for `agent/+/task/claim`), and supports:

- **Retained messages**: An agent publishes its current status to `agent/ocr/status` with retain=true—new subscribers immediately see last-known-state without querying.
- **Last Will and Testament**: Detect agent crashes (network partition vs. graceful shutdown).
- **QoS levels**: Fire-and-forget for telemetry, exactly-once for task delegation.

Map your event envelope to MQTT topics: `fabric/{trust_domain}/{agent_id}/{message_type}`.

### AsyncAPI

This is the OpenAPI equivalent for event-driven systems. Define your `capability.advertise` and `task.claim` events as AsyncAPI channels. It gives you:

- Schema validation (JSON Schema)
- Code generation for type-safe clients
- Documentation generation
- Bindings for MQTT, AMQP, Kafka, WebSockets

Use AsyncAPI to describe your event fabric contract without committing to a transport yet.

### Matrix Protocol (C-S API)

Decentralized, E2EE by default, and rooms are natural "task contexts." Each task becomes a Matrix room; agents join/leave as participants.

- **Advantage**: Built-in history, presence, and federation.
- **Mapping**: `m.room.message` with `msgtype: m.fabric.task_claim`. The room state events (`m.room.member`) track agent participation.
- **Security**: Megolm encryption for sensitive task payloads, but you can use unencrypted rooms for public advertisements.

### ActivityPub

Designed for federated social, but the Activity types map eerily well:

- `Offer` → capability advertisement
- `Accept` → task claim
- `Create` → artifact publish
- `Announce` → evidence forwarding

Use ActivityPub if you anticipate hierarchical delegation (sub-agents publishing to their parent's inbox).

### Server-Sent Events (SSE) + EventSource

For HTTP-only environments. Agents host an `/events` endpoint; the local coordinator opens persistent connections to advertised agents.

- **Advantage**: Works through corporate proxies, simple retry semantics.
- **Pattern**: Use SSE for downstream (agent→coordinator) and POST for upstream (coordinator→agent).

### WAMP (Web Application Messaging Protocol)

Provides both Pub/Sub and RPC over WebSockets. The "realm" concept maps to trust domains. Supports pattern-based subscriptions (`com.myapp.task..claim`) which fits your selective subscription model.

---

## State & Artifacts (Resources)

### CoAP (RFC 7252)

Constrained Application Protocol—HTTP semantics but binary, UDP-based, and supports **Observe** (RFC 7641). Perfect for "watching" another agent's state resource without polling.

- `GET /agent/ocr/state` with `Observe: 0` subscribes to state changes.
- Use for low-power local agents (IoT sensors acting as capability providers).

### IPLD (InterPlanetary Linked Data)

Content-addressed DAG structures. When an agent publishes an artifact, store it as IPLD and reference it by CID in your event envelope.

- **Provenance**: The CID cryptographically verifies the artifact hasn't changed.
- **Replication**: Agents can fetch from any peer that has the CID (local cache, IPFS, or just HTTP with CID in path).

### JSON-LD + Activity Streams 2.0

For capability descriptions that are machine-readable but also web-friendly. Your agent card becomes a JSON-LD document with `@context` pointing to your vocabulary. Search engines can index it; agents can parse it.

### WebDAV (RFC 4918)

If agents need to expose hierarchical namespaces (file systems, memory trees), WebDAV gives you PROPFIND (list), LOCK (concurrency control), and versioning (Delta-V). Overkill for simple agents, but useful for "knowledge repository" agents.

---

## Trust & Identity

### SPIFFE/SPIRE

For service identity without manual certificate management. Each agent gets a SPIFFE ID (`spiffe://domain.local/agent/ocr-123`). The local coordinator verifies SVIDs (SPIFFE Verifiable Identity Documents) via workload attestation.

- **Advantage**: No shared secrets; identity bound to the running process/environment.

### DIDs (Decentralized Identifiers) + Verifiable Credentials

Use DIDs for agent identity that survives beyond single deployments. The agent card contains a DID; the coordinator resolves it to a DID Document with public keys.

- **Trust domain**: Each trust domain is a DID method (e.g., `did:local:` for mDNS-discovered agents, `did:web:` for corporate agents).

### OAuth 2.0 Token Exchange (RFC 8693)

For delegation chains. When Agent A delegates to B, it exchanges its token for a token scoped to the specific task, with `actor` claim showing the delegation chain. Your event envelope's `auth_context` carries this token.

### Macaroons

Better than bearer tokens for capability attenuation. A coordinator issues a macaroon to Agent A for "read access to task X"; Agent A can further attenuate it (add caveats) before passing to Agent B for sub-tasking, without contacting the coordinator.

---

## Capability Description & Invocation

### OpenAPI 3.1 + JSON Schema

Describe agent capabilities as OpenAPI paths. The "tool" is a POST endpoint; the "resource" is a GET.

- **Twist**: Use `x-agent-capability` extensions to mark which endpoints are idempotent, safe, or require human confirmation.

### JSON-RPC 2.0

Lightweight request/response for tool invocation. Batch requests allow an agent to call multiple tools atomically. Use with MessagePack instead of JSON for binary efficiency.

### GraphQL + Subscriptions

If agents expose complex, interlinked state (knowledge graphs), GraphQL lets the coordinator query exactly what it needs. Subscriptions provide the event stream for changes.

### gRPC + Protocol Buffers

For high-performance local coordination. Define your event envelope and capability interfaces in `.proto` files. gRPC streams map well to your event fabric (server streaming for event subscription).

---

## Interesting Hybrids (The "Stack" View)

Instead of inventing one protocol, combine these existing layers:

### Option A: The Lightweight Local Stack

- **Discovery**: mDNS/DNS-SD
- **Eventing**: MQTT over local broker (Mosquitto)
- **Schema**: AsyncAPI definitions
- **State**: CoAP Observe for live resources
- **Identity**: SPIFFE with Unix domain socket attestation
- **Artifacts**: IPLD/CIDs referenced in MQTT payloads

### Option B: The Federated Enterprise Stack

- **Discovery**: DNS-SD over unicast (corporate DNS)
- **Eventing**: Matrix rooms (one per task)
- **Schema**: ActivityPub/ActivityStreams vocabulary
- **State**: Matrix state events or WebDAV collections
- **Identity**: DID:web with TLS mutual auth
- **Artifacts**: IPFS or S3 with signed URLs in Matrix messages

### Option C: The Web-Native Stack

- **Discovery**: Webfinger (RFC 7033) — lookup `agent@host` to get agent card URL
- **Eventing**: Server-Sent Events from agent endpoints
- **Schema**: JSON-LD + Schema.org extensions
- **State**: HTTP/REST with ETags and conditional requests
- **Identity**: OAuth 2.0 + DPoP (RFC 9449) for sender-constrained tokens
- **Artifacts**: Content-addressable HTTP (RFC 7233 range requests for partial content)

---

## The "Hidden Gem": ASGI + Uvicorn

If you're building in Python, ASGI (Asynchronous Server Gateway Interface) applications can handle HTTP, WebSocket, and Lifespan protocols in one process. You can mount:

- HTTP endpoints for MCP/resources
- WebSocket endpoint for WAMP or custom event fabric
- Background tasks for MQTT client loops

This lets you prototype the entire fabric in one runtime before splitting into separate services.

---

## Recommendation

Start with **MQTT + AsyncAPI + JSON-LD agent cards**.

- MQTT gives you the event fabric semantics (pub/sub, retained messages, last-will) with dozens of existing brokers and clients.
- AsyncAPI forces you to define your event schema explicitly (avoiding the "thought stream" anti-pattern).
- JSON-LD agent cards give you extensible capability descriptions that can be hosted on any HTTP server or embedded in MQTT CONNECT packets.

Only add Matrix or ActivityPub if you need federation (agents talking across organizational boundaries). Only add CoAP if you have constrained IoT agents. Only add DIDs if you need cryptographic identity persistence beyond a single session.

The existing stack is richer than most agent fabric proposals acknowledge—you can build a robust system without inventing new wire protocols.
