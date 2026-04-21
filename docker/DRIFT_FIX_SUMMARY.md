# Docker Configuration Drift — Complete Fix

## Problem Statement

Your Docker configuration kept **reverting to port 1337** because there were **4 separate sources of truth**, each with hardcoded values:

1. **`docker/FIRST_RUN.sh`** — Line 172: `BEIGEBOX_PORT=1337`
2. **`docker/.env`** — Line 5: `BEIGEBOX_PORT=1337`
3. **`~/.beigebox/config`** — Created by FIRST_RUN.sh with hardcoded values
4. **`docker-compose.yaml`** — Uses `${BEIGEBOX_PORT:-1337}` as fallback

### Why Settings Kept Changing

**Scenario:**
1. You changed `~/.beigebox/config` to use port 8080
2. You accidentally re-ran `./FIRST_RUN.sh`
3. FIRST_RUN.sh **overwrote** `~/.beigebox/config` with hardcoded defaults
4. Port went back to 1337 with **zero warning**

**Root causes:**
- FIRST_RUN.sh didn't check if config already existed
- FIRST_RUN.sh didn't ask for custom port values
- No protection against accidental re-runs
- Hardcoded defaults in multiple places

---

## The Fix (3 Changes)

### 1. **FIRST_RUN.sh — Now Preserves Existing Config**

**Added (lines 28-82):**
- Checks if `~/.beigebox/config` already exists
- If it does, asks: "Use existing config?" (with explicit warning)
- If yes: re-syncs to `docker/.env` and exits
- If no: continues with full reconfiguration
- New flag: `./FIRST_RUN.sh --reset` to force reconfiguration

**Before:**
```bash
cat > "$CONFIG_FILE" << EOF  # Just overwrites silently
BEIGEBOX_PORT=1337
...
EOF
```

**After:**
```bash
if [[ -f "$CONFIG_FILE" && "$ALLOW_RESET" != "--reset" ]]; then
    echo "Found existing config at: $CONFIG_FILE"
    echo "To preserve your settings, this will skip reconfiguration."
    echo "To reconfigure, run: ./FIRST_RUN.sh --reset"
    read -p "Continue with existing config? [Y/n]: " -r USE_EXISTING
    # ... loads and syncs existing config, then exits
fi
```

### 2. **FIRST_RUN.sh — Now Asks for Custom Ports**

**Added (lines 223-248):**
- New "Question 3" section for port customization
- Prompts for each port individually
- Defaults to standard ports if user presses Enter
- Shows summary of chosen ports

**Before:**
```bash
BEIGEBOX_PORT=1337         # Just hardcoded, no user input
WHISPER_PORT=9000
KOKORO_PORT=8880
OLLAMA_PORT=11434
```

**After:**
```bash
echo -e "${YELLOW}Port Configuration (press Enter to use defaults)${NC}"
echo "BeigeBox Web UI:     [Enter for 1337]"
read -p "> " -r CUSTOM_BEIGEBOX_PORT
BEIGEBOX_PORT=${CUSTOM_BEIGEBOX_PORT:-1337}
# ... repeat for other ports ...
echo -e "${GREEN}✓${NC} Ports configured:"
echo "  BeigeBox → localhost:$BEIGEBOX_PORT"
```

### 3. **FIRST_RUN.sh — Now Syncs ALL Ports to docker/.env**

**Added (lines 273-293):**
- Updates `docker/.env` with values from the user's config
- Covers all ports: BEIGEBOX_PORT, OLLAMA_PORT, WHISPER_PORT, KOKORO_PORT
- Runs on both fresh setup and config preservation

**Before:**
```bash
sed -i "s|^OLLAMA_DATA=.*|OLLAMA_DATA=$OLLAMA_DATA|" .env || true
sed -i "s|^REQUIRE_HASHES=.*|REQUIRE_HASHES=false|" .env || true
# Only updated OLLAMA_DATA and REQUIRE_HASHES; port left alone
```

**After:**
```bash
sed -i "s|^BEIGEBOX_PORT=.*|BEIGEBOX_PORT=$BEIGEBOX_PORT|" .env || true
sed -i "s|^OLLAMA_PORT=.*|OLLAMA_PORT=$OLLAMA_PORT|" .env || true
sed -i "s|^WHISPER_PORT=.*|WHISPER_PORT=$WHISPER_PORT|" .env || true
sed -i "s|^KOKORO_PORT=.*|KOKORO_PORT=$KOKORO_PORT|" .env || true
# All ports synchronized
```

### 4. **launch.sh — Properly Exports Config Variables**

**Added (lines 34-39):**
- Uses `set -a` to export all variables
- Shows loaded config when starting
- More helpful error messages if config missing

**Before:**
```bash
source "$CONFIG_FILE"
# Variables loaded but not exported to child processes
```

**After:**
```bash
set -a
source "$CONFIG_FILE"
set +a
echo "[launch.sh] Loaded config from $CONFIG_FILE"
echo "[launch.sh] Using: BeigeBox=$BEIGEBOX_PORT, Ollama=$OLLAMA_PORT, Whisper=$WHISPER_PORT, Kokoro=$KOKORO_PORT"
# Variables now exported; output shows what was loaded
```

### 5. **New Documentation: CONFIGURATION.md**

Comprehensive guide covering:
- Configuration architecture (single source of truth)
- File responsibilities (who owns what)
- How to change settings (3 methods)
- Troubleshooting (port conflicts, config loss, etc.)
- Summary table of all config files

---

## Before vs. After

### Before (Fragile)
```
User edits ~/.beigebox/config port → 8080
  ↓
User accidentally runs ./FIRST_RUN.sh
  ↓
FIRST_RUN.sh silently overwrites config
  ↓
Port reverts to 1337 (no warning)
  ↓
User frustrated, doesn't understand why
```

### After (Safe)
```
User edits ~/.beigebox/config port → 8080
  ↓
User runs ./FIRST_RUN.sh
  ↓
FIRST_RUN.sh: "Found existing config. Use it?" [Y/n]
  ↓
User says Y
  ↓
Config preserved, docker/.env synced
  ↓
Port stays at 8080
```

---

## How to Use (New Behavior)

### First Time Setup
```bash
cd docker
./FIRST_RUN.sh
# Answers interactive questions (platform, profiles, models, ports)
# Creates ~/.beigebox/config with your choices
# Syncs to docker/.env
./launch.sh up -d
```

### Change Ports Later
**Option 1: Direct Edit (Fastest)**
```bash
# Edit the source of truth
nano ~/.beigebox/config
# Change BEIGEBOX_PORT=1337 → BEIGEBOX_PORT=8080

# Restart Docker
./launch.sh down
./launch.sh up -d
```

**Option 2: Re-run Setup**
```bash
./FIRST_RUN.sh --reset
# Interactively reconfigure everything
./launch.sh up -d
```

### Safely Re-run Setup (Existing Users)
```bash
# Your config is safe now
./FIRST_RUN.sh
# Detects existing config, asks to preserve it
# If you say Y: config kept, just synced to .env
# If you say N: full reconfiguration
```

---

## Testing the Fix

Try this to verify it works:

```bash
# 1. Fresh setup
cd docker
./FIRST_RUN.sh
# Set port to 8080 (when asked)

# 2. Verify it's saved
cat ~/.beigebox/config | grep BEIGEBOX_PORT
# Should show: BEIGEBOX_PORT=8080

# 3. Accidentally re-run setup
./FIRST_RUN.sh
# Should ask: "Found existing config. Use it?"
# Say Y

# 4. Verify port is preserved
cat ~/.beigebox/config | grep BEIGEBOX_PORT
# Should still show: BEIGEBOX_PORT=8080 ✓

# 5. Verify it synced to .env
grep BEIGEBOX_PORT .env
# Should show: BEIGEBOX_PORT=8080 ✓

# 6. Try forced reset
./FIRST_RUN.sh --reset
# Fully reconfigures, asks for new port

# 7. Verify launch.sh shows what it's using
./launch.sh up -d
# Output shows: "Using: BeigeBox=1337, Ollama=11434, ..."
```

---

## Why This Happened (Root Cause Analysis)

The Docker setup was designed with good intent (SETUP_PROPOSAL.md, commit 2d527097) but had two implementation gaps:

1. **Design said "one-time setup" but didn't enforce it**
   - FIRST_RUN.sh asked 2 questions (platform, models) but didn't ask about ports
   - No protection against accidentally re-running and losing customization

2. **Port configuration wasn't centralized**
   - Hardcoded in FIRST_RUN.sh, .env, and docker-compose.yaml
   - No single source of truth for what the "intended" defaults were
   - User confusion about which file to edit

**This fix closes both gaps** by making the system:
- **Safe:** Config preserved by default, explicit opt-in to reset
- **Flexible:** User can customize any setting during setup
- **Documented:** Clear architecture in CONFIGURATION.md
- **Transparent:** launch.sh shows what it loaded

---

## Files Changed

1. ✅ `docker/FIRST_RUN.sh` — Added config preservation, port customization, full port sync
2. ✅ `docker/launch.sh` — Added proper variable export, better error messages
3. ✅ `docker/CONFIGURATION.md` — New file documenting the architecture

**No breaking changes.** Existing users can:
- Run `./FIRST_RUN.sh` and keep their config (default behavior)
- Or run `./FIRST_RUN.sh --reset` to reconfigure from scratch
- Or manually edit `~/.beigebox/config` anytime
