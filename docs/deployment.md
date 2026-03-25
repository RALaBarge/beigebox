# Deployment

BeigeBox ships with three production-ready deployment options: Docker Compose (single-host), Kubernetes (multi-node), and Systemd (bare metal).

## Quick Start

```bash
git clone --recursive https://github.com/ralabarge/beigebox.git
cd beigebox/docker
cp env.example .env        # optional — set GPU, ports, API keys
docker compose up -d
```

**The `--recursive` flag** initializes community skill submodules (187 skills). Skip it if you don't need them. If already cloned without it:
```bash
git submodule update --init --recursive
```

### What comes up

| Service | Port | What it does |
|---|---|---|
| Ollama | `11434` | Local model inference |
| **BeigeBox** | `1337` | Middleware proxy + integrated web UI + API + embedded vector store |

Open **http://localhost:1337** for the web UI. The OpenAI-compatible API is at `http://localhost:1337/v1`.

## Profiles

Add inference engines or tools via Docker Compose profiles:

```bash
# Base (default)
docker compose up -d

# Add browser automation
docker compose --profile cdp up -d

# Add voice I/O
docker compose --profile voice up -d

# Add both
docker compose --profile cdp --profile voice up -d

# Use alternative inference engine (llama.cpp, vLLM, or ExecutorTorch)
docker compose --profile engines-cpp up -d
docker compose --profile engines-vllm up -d
docker compose --profile engines-executorch up -d
```

### Profile reference

| Profile | Service | Port | Use case |
|---|---|---|---|
| default | ollama | 11434 | CPU inference |
| cdp | browserless/chrome | 9222 | Browser automation (operator tools) |
| voice | whisper + kokoro | 9000, 8880 | STT + TTS |
| engines-cpp | llama.cpp | 8001 | Quantized models (GGUF) |
| engines-vllm | vLLM | 8002 | Production inference (batching, multi-GPU) |
| engines-executorch | ExecutorTorch | 8003 | Edge/mobile models |

## Docker Compose (Single-Host)

### Configuration

Edit `docker/.env` to customize:

```bash
# Ports
BEIGEBOX_PORT=1337
OLLAMA_PORT=11434
WHISPER_PORT=9000
KOKORO_PORT=8880

# GPU
# Uncomment the deploy block on ollama service in docker-compose.yaml

# API keys (optional)
GOOGLE_API_KEY=...
GOOGLE_CSE_ID=...
OPENROUTER_API_KEY=...
```

### Dev vs Prod

Use the bundled helper script:

```bash
cd docker
./compose-switch.sh dev      # Development mode (local build, host mounts, auto-reload)
./compose-switch.sh prod     # Production mode (image pull, named volumes, auto-restart)
./compose-switch.sh status   # Show current configuration
docker compose up -d         # Start using current configuration
```

**Dev mode:**
- Builds image locally
- Mounts entire codebase into container (rw)
- Suitable for development; not for production

**Prod mode:**
- Pulls pre-built image
- Uses named volumes (persistent across rebuilds)
- Suitable for stable deployments

### Health checks

All services include healthchecks:

```bash
docker compose ps          # Shows health status
docker logs beigebox       # View logs
docker compose exec beigebox curl http://localhost:8000/beigebox/health
```

### Persistent data

Data persists in Docker volumes:

```bash
beigebox_data           # Conversations, metrics, embeddings
ollama_data (host)      # Model weights (mounts /mnt/storage/ollama)
```

To backup:
```bash
docker compose cp beigebox:/app/data ./backup/
```

### GPU acceleration

Uncomment the `deploy` block on the `ollama` service:

```yaml
ollama:
  deploy:
    resources:
      reservations:
        devices:
          - driver: nvidia
            count: all
            capabilities: [gpu]
```

Then restart: `docker compose up -d`.

For per-model GPU layer control, see [Configuration](configuration.md#per-model-options).

### Scaling

To run multiple inference backends in parallel:

```bash
# In docker-compose.yaml, duplicate the ollama service with different container names
# Then in config.yaml, add both to the backends list
```

BeigeBox's latency-aware router will automatically split traffic and fail over.

---

## Kubernetes

For multi-node clusters with auto-scaling and managed upgrades.

**Files:** `deploy/k8s/`

See `deploy/README.md` for detailed setup.

### Assumptions

- Kubernetes 1.24+
- kubectl access to your cluster
- A container registry (Docker Hub, ECR, GCR, etc.)
- Persistent storage (e.g., AWS EBS, GCP Persistent Disks, NFS)

### Quick deploy

```bash
cd deploy/k8s
cp values.yaml values.prod.yaml
# Edit values.prod.yaml (replicas, image, storage class, etc.)
helm install beigebox . -f values.prod.yaml
```

### Features

- StatefulSets for data consistency
- HorizontalPodAutoscaler for dynamic scaling
- NetworkPolicy for segmentation
- PersistentVolumeClaims for model storage
- Service mesh integration (Istio optional)

---

## Systemd (Bare Metal)

For Linux servers with minimal overhead and VirtualEnv isolation.

**Files:** `deploy/systemd/`

### Quick setup

```bash
# Clone and install
git clone --recursive https://github.com/ralabarge/beigebox.git /opt/beigebox
cd /opt/beigebox
python3.11 -m venv venv
source venv/bin/activate
pip install -e .

# Install systemd unit
sudo cp deploy/systemd/beigebox.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable beigebox
sudo systemctl start beigebox

# Verify
sudo systemctl status beigebox
journalctl -u beigebox -f
```

### Configuration

Edit `beigebox.service`:

```ini
[Service]
Environment=OLLAMA_HOST=ollama:11434
Environment=BEIGEBOX_PORT=8000
ExecStart=/opt/beigebox/venv/bin/python -m beigebox dial
```

### Logs

```bash
journalctl -u beigebox -f              # Follow logs
journalctl -u beigebox --since today   # Since today
journalctl -u beigebox -n 100 -q       # Last 100 lines
```

### Monitoring

```bash
curl http://localhost:8000/beigebox/health        # Health check
curl http://localhost:8000/api/v1/system-metrics  # VRAM/CPU
```

---

## Alternative Frontends

BeigeBox exposes a standard OpenAI-compatible `/v1` endpoint. Use any compatible client:

### Open WebUI

Feature-rich chat interface (runs separately):

```bash
docker run -d -p 3000:8080 \
  -e OPENAI_API_BASE_URL=http://host.docker.internal:1337/v1 \
  -e OPENAI_API_KEY=any-key \
  ghcr.io/open-webui/open-webui:main
```

Then open http://localhost:3000.

### LM Studio

Desktop client — configure:
- Server: `http://localhost:1337/v1`
- API Key: any value

### VS Code Codeium / Cursor

IDE plugins — use BeigeBox as a local fallback when cloud service is unavailable.

---

## Troubleshooting

### Models don't pull

```bash
docker compose logs ollama
# Check network — does the ollama-model-pull container have internet?
docker compose exec ollama ollama list
```

### BeigeBox won't start

```bash
docker compose logs beigebox
# Common: OLLAMA_HOST not set, or ollama isn't running yet
docker compose exec beigebox cat /app/config.yaml
```

### High latency or OOM

```bash
docker compose exec beigebox curl http://localhost:8000/api/v1/system-metrics
# Check VRAM usage and adjust OLLAMA_NUM_PARALLEL
```

### API key not working

```bash
# Check auth is enabled in config.yaml:
docker compose exec beigebox cat /app/config.yaml | grep -A5 "auth:"
# If auth.api_key is empty, auth is disabled
```

---

## Updating

### Docker Compose

```bash
git pull origin main
cd docker
docker compose pull           # Pull latest images
docker compose up -d          # Restart with new images
```

### Kubernetes

```bash
git pull origin main
cd deploy/k8s
helm upgrade beigebox . -f values.prod.yaml
```

### Systemd

```bash
cd /opt/beigebox
git pull origin main
source venv/bin/activate
pip install -e .              # Update package
sudo systemctl restart beigebox
```

---

## See also

- [Configuration](configuration.md) — runtime settings, feature flags, per-model options
- [Security](security.md) — supply chain hardening, network isolation
- [Observability](observability.md) — logging, metrics, debugging
