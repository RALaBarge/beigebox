# Authentication

API key authentication for all endpoints (optional if no keys configured).

## Single key

```yaml
# config.yaml
auth:
  api_key: ${BEIGEBOX_API_KEY}
```

```bash
export BEIGEBOX_API_KEY=sk-your-key
docker compose up -d
```

Pass via one of:
```bash
curl -H "Authorization: Bearer sk-..." http://localhost:1337/v1/chat/completions
curl -H "api-key: sk-..." http://localhost:1337/v1/chat/completions
curl "http://localhost:1337/v1/chat/completions?api_key=sk-..."
```

## Multi-key setup

```yaml
auth:
  keys:
    - name: web_ui
      key: sk-web-key
      allowed_endpoints: ["/v1/*"]
      allowed_models: ["llama3.1:*", "qwen2.5:*"]
      rate_limit_rpm: 60

    - name: api_client
      key: sk-api-key
      allowed_endpoints: ["/api/v1/*", "/v1/*"]
      allowed_models: ["*"]
      rate_limit_rpm: 120
```

Each key can have:
- **Endpoint ACL** — glob patterns of allowed endpoints
- **Model ACL** — glob patterns of allowed models
- **Rate limit** — requests/minute rolling window

## Keychain storage

Keys are stored in OS keychain (via `agentauth` library), not plaintext:

```bash
# Add a key to keychain
beigebox auth add --name mykey --key sk-... --model "llama*" --endpoint "/v1/*"

# List keys
beigebox auth list

# Delete a key
beigebox auth delete mykey
```

See [Security](security.md#api-authentication) for threat model.
