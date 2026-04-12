# DigitalOcean Droplet SSH Setup

## Your SSH Keys

### Public Key (add to DO)
```
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAII135caM690DiUaT+MgnVJEYOMZEs64qmscLqbcTaEcE beigebox-deploy-1776035309
```

### Private Key (keep safe)
```
-----BEGIN OPENSSH PRIVATE KEY-----
b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gtZW
QyNTUxOQAAACCNd+XGjOvdA4lGk/jIJ1SRGDjGRLOuKprHC6m3E2hHBAAAAKCKqjLGiqoy
xgAAAAtzc2gtZWQyNTUxOQAAACCNd+XGjOvdA4lGk/jIJ1SRGDjGRLOuKprHC6m3E2hHBA
AAAEBoZ0hq+8W9iwzaGIdox42FmrwAP1ev7PD/5Nf/xVMgpo135caM690DiUaT+MgnVJEY
OMZEs64qmscLqbcTaEcEAAAAGmJlaWdlYm94LWRlcGxveS0xNzc2MDM1MzA5AQID
-----END OPENSSH PRIVATE KEY-----
```

**Key Type:** ED25519 (modern, secure)  
**Fingerprint:** `SHA256:pb4g0MUACts2YL76W6fBEgMhg+fBWaWiLm1YqcTWl0Q`

---

## Step 1: Add Public Key to DigitalOcean

1. Log in to https://cloud.digitalocean.com
2. Go to **Settings** → **Security** → **SSH Keys**
3. Click **Add SSH Key**
4. Paste the public key above
5. Name it: `beigebox-deploy`
6. Click **Add SSH Key**

---

## Step 2: Create Your Droplet

1. Click **Create** → **Droplets**
2. Choose:
   - **Image:** Ubuntu 24.04 LTS
   - **Plan:** Basic ($6/month for testing, $12/month for production)
   - **Datacenter:** Closest region to you
   - **Auth:** SSH Keys → select `beigebox-deploy`
3. Finalize and create

---

## Step 3: Store Private Key Locally

Save the private key to your machine:

```bash
# Create ~/.ssh/beigebox_do with your private key (from above)
cat > ~/.ssh/beigebox_do << 'EOF'
-----BEGIN OPENSSH PRIVATE KEY-----
[paste full private key here]
-----END OPENSSH PRIVATE KEY-----
EOF

# Fix permissions (IMPORTANT)
chmod 600 ~/.ssh/beigebox_do

# Test connection
ssh -i ~/.ssh/beigebox_do root@<YOUR_DROPLET_IP>
```

---

## Step 4: Create SSH Config Entry (Optional but Recommended)

Add to `~/.ssh/config`:

```
Host beigebox-do
    HostName <YOUR_DROPLET_IP>
    User root
    IdentityFile ~/.ssh/beigebox_do
    StrictHostKeyChecking accept-new
```

Then connect with:
```bash
ssh beigebox-do
```

---

## Quick Deploy Commands

Once connected:

```bash
# 1. Update system
sudo apt update && sudo apt upgrade -y

# 2. Install Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh

# 3. Clone BeigeBox
cd /opt
sudo git clone https://github.com/beigebox-ai/beigebox.git
cd beigebox

# 4. Set up config
sudo cp config.example.yaml config.yaml
sudo nano config.yaml  # Edit with OAuth/backend settings

# 5. Start with Docker
sudo docker compose -f docker/docker-compose.yaml up -d beigebox

# 6. Check health
curl http://localhost:8000/beigebox/health
```

---

## Security Notes

✅ **Key Type:** ED25519 is modern and secure (2^128 security level)  
✅ **No Passphrase:** Key is unencrypted for automation (keep safe!)  
✅ **Permissions:** Private key must be `chmod 600` or SSH will refuse it  
✅ **Do Not Share:** Private key = full access to your droplet  

---

## Troubleshooting

**"Permission denied (publickey)"**
- Make sure private key is `chmod 600`
- Check key fingerprint matches DO console
- Verify you're using the correct IP

**"Connection refused"**
- Droplet may still be booting (wait 1-2 min)
- Check firewall rules in DO console (port 22 should be open)

**"Host key verification failed"**
- First SSH connection? Type `yes` to accept the host key
- SSH will remember it for future connections

---

## One-Line Status Check

```bash
ssh -i ~/.ssh/beigebox_do root@<IP> "curl -s http://localhost:8000/beigebox/health | jq .status"
```

If it returns `"ok"`, BeigeBox is running.

---

## Next Steps

1. Deploy droplet (takes ~1 min)
2. SSH in and run the quick deploy commands above
3. Access BeigeBox at `http://<DROPLET_IP>:8000`
4. Follow OAUTH_API_KEY_AUTH_SETUP.md to enable OAuth login
5. Test with: `curl http://<DROPLET_IP>:8000/auth/me`

Good to go for Tuesday! 🚀
