# BeigeBox Docker Setup

Run BeigeBox locally via Docker. Works on macOS and Linux.

---

## ⚡ Quick Start (5 minutes)

### Step 1: Start Everything

Open your terminal in the `docker/` folder and run:

```bash
./launch.sh up -d
```

**What happens:**
1. **First time only:** You'll be asked 2 simple questions
   - Do you want browser automation (CDP)? (yes/no)
   - Where should models be stored? (use the default, or pick a custom folder)
2. Models are automatically downloaded (`llama3.2:3b` and `nomic-embed-text`)
3. BeigeBox starts automatically
4. Your choices are saved to `~/.beigebox/config` (shared across all BeigeBox versions on your machine)

**That's it!** Open your browser to **http://localhost:1337**

### Step 2: Stop Everything (when you're done)

```bash
./launch.sh down
```

### Step 3: Start Again Later

```bash
./launch.sh up -d
```

No wizard this time — it remembers your choices and just launches.

---

## 📋 What You Need

- **Docker Desktop** installed ([download here](https://www.docker.com/products/docker-desktop))
- **Ollama** installed and running on your machine ([download here](https://ollama.ai))
  - On macOS: `brew install ollama` then `brew services start ollama`
  - On Linux: follow [ollama.ai installation](https://ollama.ai)
- **5-10 GB of disk space** for models

---

## 🎛️ What Gets Started

| Service | What It Does | Where |
|---------|-------------|-------|
| **BeigeBox** | The main app — web UI + API | http://localhost:1337 |
| **Postgres** | Stores vectors for search | Internal only |
| Models | `llama3.2:3b` (chat) + `nomic-embed-text` (search) | On your machine via Ollama |

---

## 🤔 Common Questions

### "I already ran the wizard. Can I skip it next time?"

Yes! The wizard only runs once. After that:

```bash
./launch.sh up -d
```

Just launches with your saved settings. No questions asked.

### "I want to change my answers (storage location, features, etc.)"

```bash
./launch.sh --reset up -d
```

This re-runs the wizard and lets you change everything.

### "Where are my settings saved?"

They're saved in your home folder in `~/.beigebox/config`. You can edit this file directly if you want, but `--reset` is easier.

### "I want to use browser automation (like Selenium)"

```bash
./launch.sh --profile cdp up -d
```

This adds Chrome. Now you can automate browser interactions. (Only needed once; re-running `up -d` without `--profile` won't remove it.)

### "I want to try different settings without restarting"

After BeigeBox is running, you can edit `data/runtime_config.yaml` and the changes apply immediately — no restart needed.

### "How do I check if everything is healthy?"

```bash
docker compose ps
```

You should see all containers in green (healthy).

### "Something broke. How do I start fresh?"

```bash
./launch.sh down
rm ~/.beigebox/config
./launch.sh up -d
```

This deletes your saved choices and runs the wizard again. Your conversations/data are preserved in `data/`.

---

## 🔧 For the Curious: What's Running

When you run `./launch.sh up -d`, Docker starts:

- **beigebox** — The app itself (http://localhost:1337)
- **postgres** — Database for storing search vectors
- **Ollama** — Already running on your machine (not in Docker)

If you added `--profile cdp`:
- **chrome** — Browser for automation

---

## 📁 Files Explained

| File | What It Is |
|------|-----------|
| `launch.sh` | The main script — handles setup wizard and launching |
| `docker-compose.yaml` | Tells Docker which services to run and how |
| `config.yaml` | Main settings (models, features, routing) |
| `env.example` | Template for environment variables |
| `Dockerfile` | Recipe for building the BeigeBox container |
| `Dockerfile.claude` | Recipe for the Claude Code sidecar (advanced) |

---

## 📦 Multi-Version Development

If you're working on multiple versions of BeigeBox (e.g., local dev + GitHub version), here's what's shared and what's separate:

### Shared Across All Versions
- `~/.beigebox/config` — User-level configuration (ports, features, model location)
- `~/.ollama/models` — Model files (Ollama is typically run on the host, not in Docker)

### Per-Version
- `docker/.env` — Environment variables specific to this repo copy
- `beigebox_data` volume — Docker-managed storage (conversations, embeddings)
- `docker/logs/` — Logs for this specific deployment

**Tip:** If you want both versions to use the same models and settings, just run `./launch.sh up -d` from each version — they'll automatically pick up the shared config and models from your home directory.

---

## 🌐 Docker Networking

To connect to services running inside the Docker containers:

### From the Host Machine
Use `localhost` + the exposed port:
```bash
# Connect to BeigeBox API
curl http://localhost:1337/v1/...

# Connect to Ollama inference (if exposed)
curl http://localhost:11434/api/tags
```

### From Inside a Container
Use the service name + internal port (no `localhost`):
```bash
# From inside beigebox container, reach Ollama on the host:
curl http://host.docker.internal:11434/api/tags
```

### Attaching to a Container for Debugging
```bash
docker exec -it beigebox /bin/bash
# Now you're inside the container — can debug, check logs, etc.
```

---

## 🚀 Advanced: Custom Configuration

### Using a Different Model

Edit `config.yaml` and change the `default` model. For example:

```yaml
models:
  default: "mistral:7b"  # Instead of llama3.2:3b
```

Then restart:

```bash
./launch.sh down
./launch.sh up -d
```

### Enabling GPU (NVIDIA)

Edit `docker-compose.yaml`, find the line that says `# deploy:` under the `postgres` service, and uncomment the GPU block:

```yaml
deploy:
  resources:
    reservations:
      devices:
        - driver: nvidia
          count: all
          capabilities: [gpu]
```

Then restart.

### Custom API Keys

Edit `docker/.env` and add your keys:

```bash
OPENROUTER_API_KEY=your_key_here
ANTHROPIC_API_KEY=your_key_here
```

No restart needed — changes apply on next request.

---

## ❌ Troubleshooting

### "Connection refused" or "Can't reach BeigeBox"

**Problem:** BeigeBox isn't healthy yet.

**Fix:** Wait 10-15 seconds and refresh your browser. If still broken:

```bash
docker compose logs beigebox
```

This shows you what went wrong.

### "Model not found"

**Problem:** Models didn't download.

**Fix:** Check that Ollama is running:

```bash
ollama list
```

If you see nothing, manually pull the model:

```bash
ollama pull llama3.2:3b
ollama pull nomic-embed-text
```

Then restart BeigeBox:

```bash
./launch.sh down
./launch.sh up -d
```

### "Docker Compose not found" or "Unknown flag"

**Problem:** Old version of Docker.

**Fix:** Update Docker Desktop from [docker.com](https://docker.com)

### "Permission denied" when creating `~/.beigebox/config`

**Problem:** The `~/.beigebox` directory is owned by root and you can't write to it.

**Fix:**
```bash
sudo chown -R $(whoami) ~/.beigebox
chmod 755 ~/.beigebox
```

Then re-run `launch.sh up -d`.

### "Mounts denied" on macOS

**Problem:** Docker can't access your folders.

**Fix:**
1. Open Docker Desktop
2. Click **Settings** → **Resources** → **File Sharing**
3. Add the folder where your models are stored (usually `~/.ollama`)
4. Restart BeigeBox

### "Everything is slow"

**Problem:** Running on CPU (no GPU).

**Fix:** If you have an NVIDIA GPU, follow the GPU section above. Otherwise, this is normal for CPU inference.

---

## 📚 Learn More

- **[../README.md](../README.md)** — What is BeigeBox?
- **[../d0cs/configuration.md](../d0cs/configuration.md)** — All config options
- **[../d0cs/deployment.md](../d0cs/deployment.md)** — Kubernetes, Systemd, other platforms

---

## 💡 Tips

**Use BeigeBox from other apps:**

BeigeBox has an OpenAI-compatible API at `http://localhost:1337/v1`. You can use it with any tool that supports OpenAI (Open WebUI, LM Studio, etc.):

```bash
docker run -d -p 3000:8080 \
  -e OPENAI_API_BASE_URL=http://host.docker.internal:1337/v1 \
  -e OPENAI_API_KEY=any-key \
  ghcr.io/open-webui/open-webui:main
```

Now Open WebUI is at http://localhost:3000

**Check BeigeBox health anytime:**

```bash
curl http://localhost:1337/beigebox/health
```

Returns `{"status": "healthy"}` if all is well.

---

**Questions?** Check the logs: `docker compose logs beigebox`
