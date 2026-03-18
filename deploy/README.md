# BeigeBox Deployment

Three deployment options. Pick one.

## Docker Compose (recommended)

Full stack: BeigeBox + Ollama, data persisted on the host.

```bash
cd deploy/docker
cp .env.prod.example .env
# Edit .env — set BEIGEBOX_DATA_PATH, OLLAMA_DATA_PATH
mkdir -p /mnt/storage/beigebox/data /mnt/storage/ollama
docker compose -f docker-compose.prod.yaml up -d
```

See [docker/docker-compose.prod.yaml](docker/docker-compose.prod.yaml) and
[docker/.env.prod.example](docker/.env.prod.example).

---

## Kubernetes

Apply manifests in order:

```bash
cd deploy/k8s

# 1. PersistentVolumeClaim
kubectl apply -f pvc.yaml

# 2. ConfigMap — import your actual config files
kubectl create configmap beigebox-config \
  --from-file=config.yaml=../../config.yaml \
  --from-file=runtime_config.yaml=../../runtime_config.yaml \
  --dry-run=client -o yaml | kubectl apply -f -

# 3. Secrets (BB_MASTER_KEY, API keys)
kubectl create secret generic beigebox-secrets \
  --from-literal=bb_master_key="$(cat ~/.bb/.key)" \
  --dry-run=client -o yaml | kubectl apply -f -

# 4. Deployment + Service
kubectl apply -f deployment.yaml
kubectl apply -f service.yaml
```

Files: [k8s/](k8s/)

---

## Systemd (bare metal)

```bash
# 1. Create user + install
sudo useradd -r -s /usr/sbin/nologin beigebox
sudo mkdir -p /opt/beigebox
sudo chown beigebox:beigebox /opt/beigebox

# 2. Install BeigeBox into a virtualenv
sudo -u beigebox python3 -m venv /opt/beigebox/.venv
sudo -u beigebox /opt/beigebox/.venv/bin/pip install beigebox

# 3. Drop in config
sudo cp config.yaml /opt/beigebox/config.yaml
sudo mkdir -p /opt/beigebox/data /opt/beigebox/workspace/out /opt/beigebox/logs

# 4. Install the unit file
sudo cp deploy/systemd/beigebox.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now beigebox
sudo journalctl -u beigebox -f
```

File: [systemd/beigebox.service](systemd/beigebox.service)
