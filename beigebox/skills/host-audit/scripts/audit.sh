#!/usr/bin/env bash
# host-audit: profile a list of machines regardless of network or auth style.
# Each target may be local, an SSH alias, user@host, or user:password@host.
# Emits a markdown report (default), JSON, or CLAUDE.md-ready section.
set -uo pipefail

FORMAT="markdown"
PROBE_MODE=0
TARGETS=()
FILE_TARGETS=""
SAVE=0
DIFF=0
LIST=0
LIST_LABEL=""
SNAPSHOT_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/beigebox/host-audit/snapshots"
SCHEMA_VERSION="1"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)" || SCRIPT_DIR=""
SCRIPT_PATH="${SCRIPT_DIR:+$SCRIPT_DIR/}$(basename "${BASH_SOURCE[0]}")"

usage() {
  cat <<'EOF'
Usage: audit.sh [OPTIONS] TARGET [TARGET ...]

Profile machines: OS, hardware, network, installed virtualization stacks,
running containers/VMs, and listening services.

TARGET forms:
  local | localhost           probe this machine
  alias                       ssh alias from ~/.ssh/config
  user@host                   ssh with key auth (keys, agent, ssh-config)
  user:password@host          ssh with sshpass (requires sshpass locally)
  label=TARGET                prepend a display label to any of the above

Options:
  --format {markdown|json|claude}   Output format (default: markdown).
                                    'claude' emits a CLAUDE.md-ready section.
  --file PATH                       Read targets from file (one per line).
  --save                            Persist each result as a timestamped JSON
                                    snapshot under the snapshot dir.
  --diff                            Instead of a fresh audit, diff each target's
                                    latest snapshot against its previous one.
  --list [LABEL]                    List saved snapshots (all labels, or one).
  --snapshot-dir PATH               Override snapshot dir (default:
                                    $XDG_STATE_HOME/beigebox/host-audit/snapshots
                                    or ~/.local/state/... on most systems).
  --probe                           (internal) Run local probes and emit JSON.
  -h, --help                        Show this help.

Snapshots & schema:
  Each saved snapshot is one JSON document conforming to schema.json
  (alongside this script). schema_version is "1". Snapshots are stored as
  <snapshot-dir>/<label>/<UTC-timestamp>.json, with a 'latest.json' symlink
  per label pointing at the most recent one.

Examples:
  audit.sh local
  audit.sh user@server
  audit.sh debian=ryan@192.168.1.235 mac=useruser:1234@192.168.1.214
  audit.sh --format json local user@server
  audit.sh --format claude --file ~/.config/beigebox/audit-targets
EOF
}

have() { command -v "$1" >/dev/null 2>&1; }

require_jq() {
  if ! have jq; then
    echo "host-audit: jq is required. Install via 'apt install jq' or 'brew install jq'." >&2
    exit 2
  fi
}

if have timeout; then _t() { timeout "$@"; }
elif have gtimeout; then _t() { gtimeout "$@"; }
else _t() { shift; "$@"; }
fi

# ---------- probe (runs on target, emits JSON) ----------

probe_run() {
  local os kernel arch pretty hostname
  os="$(uname -s 2>/dev/null || echo unknown)"
  kernel="$(uname -r 2>/dev/null || echo unknown)"
  arch="$(uname -m 2>/dev/null || echo unknown)"
  hostname="$(hostname 2>/dev/null || echo unknown)"

  if [ "$os" = "Linux" ]; then
    pretty="$(grep -E '^PRETTY_NAME=' /etc/os-release 2>/dev/null \
              | sed -E 's/^PRETTY_NAME=//; s/^"//; s/"$//' )"
    [ -z "$pretty" ] && pretty="Linux $kernel"
  elif [ "$os" = "Darwin" ]; then
    local mv; mv="$(sw_vers -productVersion 2>/dev/null || echo "?")"
    pretty="macOS $mv"
  else
    pretty="$os $kernel"
  fi

  local cores=0 mem_gb=0 gpu=""
  if [ "$os" = "Linux" ]; then
    cores="$(nproc 2>/dev/null || getconf _NPROCESSORS_ONLN 2>/dev/null || echo 0)"
    mem_gb="$(awk '/^MemTotal/{printf "%.0f\n", $2/1024/1024}' /proc/meminfo 2>/dev/null || echo 0)"
    if have lspci; then
      gpu="$(lspci 2>/dev/null | grep -iE 'vga|3d controller' | head -1 | sed 's/^[^:]*: //' || true)"
    fi
  elif [ "$os" = "Darwin" ]; then
    cores="$(sysctl -n hw.ncpu 2>/dev/null || echo 0)"
    mem_gb="$(sysctl -n hw.memsize 2>/dev/null | awk '{printf "%.0f\n", $1/1024/1024/1024}' || echo 0)"
    gpu="$(system_profiler SPDisplaysDataType 2>/dev/null | awk -F': ' '/Chipset Model/{sub(/^ +/,"",$2); print $2; exit}')"
    local cpu_model
    cpu_model="$(sysctl -n machdep.cpu.brand_string 2>/dev/null)"
    [ -n "$cpu_model" ] && gpu="${gpu:+$gpu; }CPU: $cpu_model"
  fi

  local disks_json
  disks_json="$(
    df -h -P 2>/dev/null | awk 'NR>1 && ($NF ~ /^\/$|^\/home$|^\/var$|^\/data$/){print $NF"|"$2"|"$3"|"$5}' \
    | jq -R -sc 'split("\n") | map(select(length>0)) | map(split("|")) | map({mount:.[0], size:.[1], used:.[2], pct:.[3]})'
  )"
  [ -z "$disks_json" ] && disks_json='[]'

  local ifaces_json='[]'
  if [ "$os" = "Linux" ] && have ip; then
    ifaces_json="$(
      ip -j -4 addr show 2>/dev/null \
      | jq -c '[.[] | select(.ifname != "lo") | {name: .ifname, state: (.operstate // "UNKNOWN"), ips: [(.addr_info // [])[] | .local]}]' 2>/dev/null || echo '[]'
    )"
  elif [ "$os" = "Darwin" ] && have ifconfig; then
    ifaces_json="$(
      ifconfig -a 2>/dev/null | awk '
        /^[a-z]/ { iface=$1; sub(/:$/,"",iface); next }
        /inet / && iface != "lo0" { print iface"|"$2 }
      ' | jq -R -sc 'split("\n") | map(select(length>0)) | map(split("|")) | group_by(.[0]) | map({name: .[0][0], state: "UP", ips: [.[] | .[1]]})'
    )"
  fi
  [ -z "$ifaces_json" ] && ifaces_json='[]'

  local all_ips
  all_ips="$(hostname -I 2>/dev/null | tr -s ' ' || true)"
  [ -z "$all_ips" ] && all_ips="$(
    echo "$ifaces_json" | jq -r '[.[].ips[]] | join(" ")' 2>/dev/null
  )"

  local tools_list="docker podman lxc lxc-ls incus virsh machinectl systemd-nspawn qemu-system-x86_64 orb orbctl colima lima limactl multipass VBoxManage prlctl vmrun tart brew kubectl k3s microk8s"
  local tools_json
  tools_json="$(
    for t in $tools_list; do
      if command -v "$t" >/dev/null 2>&1; then
        echo "$t"
      fi
    done | jq -R -sc 'split("\n") | map(select(length>0))'
  )"

  local docker_json='[]'
  if have docker && _t 3 docker info >/dev/null 2>&1; then
    docker_json="$(_t 3 docker ps --format '{{json .}}' 2>/dev/null \
      | jq -sc 'map({name: .Names, image: .Image, ports: .Ports, state: (.State // .Status)})')"
    [ -z "$docker_json" ] && docker_json='[]'
  fi

  local lxc_dirs_json='[]'
  if [ -r /var/lib/lxc ] 2>/dev/null; then
    lxc_dirs_json="$(ls /var/lib/lxc/ 2>/dev/null \
      | jq -R -sc 'split("\n") | map(select(length>0))')"
    [ -z "$lxc_dirs_json" ] && lxc_dirs_json='[]'
  fi

  local lxd_json='[]'
  if have lxc; then
    lxd_json="$(_t 3 lxc list --format json 2>/dev/null \
      | jq -c 'map({name: .name, status: .status, ipv4: [(.state.network // {}) | to_entries[] | select(.key != "lo") | .value.addresses[]? | select(.family == "inet" and .scope == "global") | .address]})' 2>/dev/null)"
    [ -z "$lxd_json" ] && lxd_json='[]'
  fi

  local incus_json='[]'
  if have incus; then
    incus_json="$(_t 3 incus list --format json 2>/dev/null \
      | jq -c 'map({name: .name, status: .status, ipv4: [(.state.network // {}) | to_entries[] | select(.key != "lo") | .value.addresses[]? | select(.family == "inet" and .scope == "global") | .address]})' 2>/dev/null)"
    [ -z "$incus_json" ] && incus_json='[]'
  fi

  local virsh_json='[]'
  if have virsh; then
    virsh_json="$(_t 3 virsh list --all --name 2>/dev/null \
      | jq -R -sc 'split("\n") | map(select(length>0))')"
    [ -z "$virsh_json" ] && virsh_json='[]'
  fi

  local listeners_json='[]'
  if [ "$os" = "Linux" ] && have ss; then
    listeners_json="$(
      ss -Hltnp 2>/dev/null \
      | awk '{
          addr=$4; proc=$6; name="?";
          if (match(proc, /"[^"]+"/)) name=substr(proc, RSTART+1, RLENGTH-2);
          print addr"|"name;
        }' \
      | sort -u \
      | jq -R -sc 'split("\n") | map(select(length>0)) | map(split("|")) | map({addr: .[0], proc: (.[1] // "?")})'
    )"
  elif [ "$os" = "Darwin" ] && have lsof; then
    listeners_json="$(
      lsof -nP -iTCP -sTCP:LISTEN 2>/dev/null \
      | awk 'NR>1 {print $9"|"$1}' \
      | sort -u \
      | jq -R -sc 'split("\n") | map(select(length>0)) | map(split("|")) | map({addr: .[0], proc: (.[1] // "?")})'
    )"
  fi
  [ -z "$listeners_json" ] && listeners_json='[]'

  local hints_json='[]'
  local home="${HOME:-}"
  if [ -n "$home" ]; then
    local hints=()
    [ -d "$home/.ollama" ] && hints+=("ollama (~/.ollama)")
    [ -d "$home/.open-webui" ] && hints+=("openwebui (~/.open-webui)")
    [ -d "$home/.config/libvirt" ] && hints+=("libvirt user config (~/.config/libvirt)")
    [ -d "$home/.docker" ] && hints+=("docker client config (~/.docker)")
    [ -d "$home/.kube" ] && hints+=("kubectl config (~/.kube)")
    [ -d "$home/Library/Group Containers/group.com.docker" ] && hints+=("Docker Desktop (macOS)")
    [ -d "$home/.orbstack" ] && hints+=("OrbStack (~/.orbstack)")
    [ -d "$home/.colima" ] && hints+=("Colima (~/.colima)")
    [ -d "$home/.lima" ] && hints+=("Lima (~/.lima)")
    [ ${#hints[@]} -gt 0 ] && hints_json="$(printf '%s\n' "${hints[@]}" \
      | jq -R -sc 'split("\n") | map(select(length>0))')"
  fi

  local sudo_needed_json='[]'
  local needs=()
  if [ -d /var/lib/lxc ] 2>/dev/null && [ ! -r /var/lib/lxc ] 2>/dev/null; then
    needs+=("read /var/lib/lxc (container listing)")
  fi
  if echo "$listeners_json" | jq -e 'any(.[]; .proc == "?")' >/dev/null 2>&1; then
    needs+=("listener process names (sudo ss/lsof)")
  fi
  if have docker && ! _t 2 docker info >/dev/null 2>&1; then
    needs+=("docker socket access")
  fi
  [ ${#needs[@]} -gt 0 ] && sudo_needed_json="$(printf '%s\n' "${needs[@]}" \
    | jq -R -sc 'split("\n") | map(select(length>0))')"

  jq -n \
    --arg hostname "$hostname" \
    --arg os "$os" \
    --arg pretty "$pretty" \
    --arg kernel "$kernel" \
    --arg arch "$arch" \
    --argjson cores "${cores:-0}" \
    --argjson mem "${mem_gb:-0}" \
    --arg gpu "$gpu" \
    --argjson disks "$disks_json" \
    --argjson ifaces "$ifaces_json" \
    --arg all_ips "$all_ips" \
    --argjson tools "$tools_json" \
    --argjson docker "$docker_json" \
    --argjson lxc_dirs "$lxc_dirs_json" \
    --argjson lxd "$lxd_json" \
    --argjson incus "$incus_json" \
    --argjson virsh "$virsh_json" \
    --argjson listeners "$listeners_json" \
    --argjson hints "$hints_json" \
    --argjson sudo_needed "$sudo_needed_json" '
    {
      schema_version: "1",
      reachable: true,
      hostname: $hostname,
      os: {family: $os, pretty: $pretty, kernel: $kernel, arch: $arch},
      hw: {
        cores: $cores,
        memory_gb: $mem,
        gpu: (if $gpu == "" then null else $gpu end),
        disks: $disks
      },
      network: {
        interfaces: $ifaces,
        all_ips: ($all_ips | split(" ") | map(select(length>0)))
      },
      tools: $tools,
      containers: {
        docker: $docker,
        lxc_classic_dirs: $lxc_dirs,
        lxd: $lxd,
        incus: $incus,
        libvirt: $virsh
      },
      listeners: $listeners,
      hints: $hints,
      sudo_needed: $sudo_needed
    }
    '
}

# ---------- target parsing ----------

# Emits TAB-separated: label \t method \t user_at_host \t password
parse_target() {
  local spec="$1"
  local label=""
  if [[ "$spec" == *"="* ]]; then
    label="${spec%%=*}"
    spec="${spec#*=}"
  fi
  if [[ "$spec" == "local" || "$spec" == "localhost" ]]; then
    printf '%s\t%s\t%s\t%s\n' "${label:-localhost}" "local" "" ""
    return
  fi
  # user:pass@host — password may contain anything except literal '@'
  if [[ "$spec" =~ ^([^:@]+):([^@]*)@(.+)$ ]]; then
    local u="${BASH_REMATCH[1]}" p="${BASH_REMATCH[2]}" h="${BASH_REMATCH[3]}"
    printf '%s\t%s\t%s\t%s\n' "${label:-$h}" "sshpass" "$u@$h" "$p"
    return
  fi
  printf '%s\t%s\t%s\t%s\n' "${label:-$spec}" "ssh" "$spec" ""
}

# ---------- probe dispatch ----------

run_probe() {
  local method="$1" target="$2" pass="$3"
  case "$method" in
    local)
      probe_run
      ;;
    ssh)
      if [ -z "$SCRIPT_PATH" ] || [ ! -f "$SCRIPT_PATH" ]; then
        echo '{"reachable": false, "error": "cannot locate script file for remote execution"}'
        return
      fi
      local out rc
      out="$(ssh -o ConnectTimeout=5 -o BatchMode=yes -o StrictHostKeyChecking=accept-new \
              "$target" "bash -s -- --probe" < "$SCRIPT_PATH" 2>/dev/null)"
      rc=$?
      if [ $rc -ne 0 ] || [ -z "$out" ]; then
        jq -n --arg err "ssh failed (rc=$rc) — is key auth set up?" '{reachable: false, error: $err}'
      else
        printf '%s' "$out"
      fi
      ;;
    sshpass)
      if ! have sshpass; then
        echo '{"reachable": false, "error": "sshpass not installed locally"}'
        return
      fi
      if [ -z "$SCRIPT_PATH" ] || [ ! -f "$SCRIPT_PATH" ]; then
        echo '{"reachable": false, "error": "cannot locate script file for remote execution"}'
        return
      fi
      local out rc
      out="$(sshpass -p "$pass" ssh -o ConnectTimeout=5 \
              -o StrictHostKeyChecking=accept-new -o PubkeyAuthentication=no \
              "$target" "bash -s -- --probe" < "$SCRIPT_PATH" 2>/dev/null)"
      rc=$?
      if [ $rc -ne 0 ] || [ -z "$out" ]; then
        jq -n --arg err "sshpass failed (rc=$rc) — wrong password or unreachable?" '{reachable: false, error: $err}'
      else
        printf '%s' "$out"
      fi
      ;;
  esac
}

# ---------- formatters ----------

format_markdown_one() {
  # $1 = label, stdin = probe JSON
  local label="$1"
  jq -r --arg label "$label" '
    if (.reachable // true) == false then
      "## " + $label + "\n\n**Unreachable:** " + (.error // "unknown error") + "\n"
    else
      "## " + $label + " (" + (.hostname // "?") + ")\n\n"
      + "- **OS**: " + (.os.pretty // "?") + ", kernel " + (.os.kernel // "?") + ", " + (.os.arch // "?") + "\n"
      + "- **Hardware**: " + (.hw.cores|tostring) + " cores, " + (.hw.memory_gb|tostring) + " GB RAM"
      + (if .hw.gpu then ", " + .hw.gpu else "" end) + "\n"
      + (if (.hw.disks|length) > 0 then
          "- **Disks**: " + ([.hw.disks[] | .mount + " " + .size + " (" + .pct + " used)"] | join(", ")) + "\n"
        else "" end)
      + "- **Addresses**: " + (.network.all_ips | join(", ")) + "\n"
      + (if (.network.interfaces|length) > 0 then
          "- **Interfaces**: " + ([.network.interfaces[] | .name + "(" + (.ips|join(",")) + ")"] | join(", ")) + "\n"
        else "" end)
      + "\n### Stacks installed\n"
      + (if (.tools|length) > 0 then ([.tools[] | "- " + .] | join("\n")) + "\n" else "- (none detected)\n" end)
      + (if (.containers.docker|length) > 0 then
          "\n### Docker containers\n"
          + ([.containers.docker[] | "- `" + .name + "` — " + .image + (if .ports != "" and .ports != null then " — " + (.ports|tostring) else "" end)] | join("\n")) + "\n"
        else "" end)
      + (if (.containers.lxc_classic_dirs|length) > 0 then
          "\n### LXC (classic) containers\n"
          + ([.containers.lxc_classic_dirs[] | "- " + .] | join("\n")) + "\n"
        else "" end)
      + (if (.containers.lxd|length) > 0 then
          "\n### LXD\n"
          + ([.containers.lxd[] | "- `" + .name + "` " + .status + (if (.ipv4|length) > 0 then " — " + (.ipv4|join(",")) else "" end)] | join("\n")) + "\n"
        else "" end)
      + (if (.containers.incus|length) > 0 then
          "\n### Incus\n"
          + ([.containers.incus[] | "- `" + .name + "` " + .status + (if (.ipv4|length) > 0 then " — " + (.ipv4|join(",")) else "" end)] | join("\n")) + "\n"
        else "" end)
      + (if (.containers.libvirt|length) > 0 then
          "\n### libvirt domains\n"
          + ([.containers.libvirt[] | "- " + .] | join("\n")) + "\n"
        else "" end)
      + (if (.listeners|length) > 0 then
          "\n### Listening services\n| Addr | Process |\n|------|---------|\n"
          + ([.listeners[] | "| " + .addr + " | " + .proc + " |"] | join("\n")) + "\n"
        else "" end)
      + (if (.hints|length) > 0 then
          "\n### Hints\n"
          + ([.hints[] | "- " + .] | join("\n")) + "\n"
        else "" end)
      + (if (.sudo_needed|length) > 0 then
          "\n### Needs sudo (not gathered)\n"
          + ([.sudo_needed[] | "- " + .] | join("\n")) + "\n"
        else "" end)
    end
  '
}

format_claude_one() {
  # CLAUDE.md-flavored: tighter, one opinionated block per host.
  local label="$1"
  jq -r --arg label "$label" '
    if (.reachable // true) == false then
      "## " + $label + "\n\n**Unreachable:** " + (.error // "unknown") + "\n"
    else
      "## " + $label + "  (host: `" + (.hostname // "?") + "`)\n\n"
      + "- **OS**: " + (.os.pretty // "?") + " · kernel " + (.os.kernel // "?") + " · " + (.os.arch // "?") + "\n"
      + "- **Hardware**: " + (.hw.cores|tostring) + " cores · " + (.hw.memory_gb|tostring) + " GB RAM"
      + (if .hw.gpu then " · " + .hw.gpu else "" end) + "\n"
      + "- **Addresses**: " + (.network.all_ips | join(", ")) + "\n"
      + "- **Stacks**: " + ((.tools // []) | join(", ")) + "\n"
      + (if (.containers.docker|length) + (.containers.lxc_classic_dirs|length)
          + (.containers.lxd|length) + (.containers.incus|length)
          + (.containers.libvirt|length) > 0 then
          "\n**Containers/VMs**\n\n"
          + (if (.containers.docker|length) > 0 then
              "| Backend | Name | Detail |\n|---|---|---|\n"
              + ([.containers.docker[] | "| docker | `" + .name + "` | " + .image + "  `" + (.ports|tostring) + "` |"] | join("\n")) + "\n"
            else "" end)
          + (if (.containers.lxc_classic_dirs|length) > 0 then
              "\n- **LXC (classic)**: " + (.containers.lxc_classic_dirs | join(", ")) + "\n"
            else "" end)
          + (if (.containers.lxd|length) > 0 then
              "\n- **LXD**: " + ([.containers.lxd[] | .name + "(" + .status + ")"] | join(", ")) + "\n"
            else "" end)
          + (if (.containers.incus|length) > 0 then
              "\n- **Incus**: " + ([.containers.incus[] | .name + "(" + .status + ")"] | join(", ")) + "\n"
            else "" end)
          + (if (.containers.libvirt|length) > 0 then
              "\n- **libvirt**: " + (.containers.libvirt | join(", ")) + "\n"
            else "" end)
        else "" end)
      + (if (.listeners|length) > 0 then
          "\n**Listening**\n\n| Port/Addr | Process |\n|---|---|\n"
          + ([.listeners[] | "| " + .addr + " | " + .proc + " |"] | join("\n")) + "\n"
        else "" end)
      + (if (.sudo_needed|length) > 0 then
          "\n_Gaps (needs sudo):_ " + (.sudo_needed | join("; ")) + "\n"
        else "" end)
    end
  '
}

# ---------- snapshots ----------

# Portable ISO-8601 UTC timestamp safe for filenames.
now_stamp() { date -u +'%Y-%m-%dT%H-%M-%SZ'; }

sanitize_label() {
  # Replace anything not alnum/_/-/. with _
  printf '%s' "$1" | tr -c 'A-Za-z0-9_.-' '_'
}

save_snapshot() {
  # $1 = label, stdin = probe JSON (already augmented with label/target/auth_method/audit_time)
  local label; label="$(sanitize_label "$1")"
  local dir="$SNAPSHOT_DIR/$label"
  mkdir -p "$dir" || return 1
  local stamp; stamp="$(now_stamp)"
  local out="$dir/$stamp.json"
  cat > "$out" || return 1
  # Update latest.json symlink (relative so dir is portable).
  (cd "$dir" && ln -sfn "$stamp.json" latest.json)
  printf '%s\n' "$out"
}

list_snapshots() {
  local label_filter="${1:-}"
  if [ ! -d "$SNAPSHOT_DIR" ]; then
    echo "No snapshots found: $SNAPSHOT_DIR does not exist." >&2
    return 0
  fi
  local any=0
  for d in "$SNAPSHOT_DIR"/*/; do
    [ -d "$d" ] || continue
    local lbl; lbl="$(basename "$d")"
    if [ -n "$label_filter" ] && [ "$lbl" != "$label_filter" ]; then
      continue
    fi
    any=1
    echo "== $lbl =="
    ls -1 "$d" | grep -E '\.json$' | grep -v '^latest\.json$' | sort | sed 's/^/  /'
    if [ -L "$d/latest.json" ]; then
      echo "  -> latest: $(readlink "$d/latest.json")"
    fi
  done
  if [ $any -eq 0 ]; then
    echo "No snapshots matched."
  fi
}

# Find the second-newest snapshot (previous to latest) for a label.
# Echoes path, or empty if none.
previous_snapshot() {
  local label; label="$(sanitize_label "$1")"
  local dir="$SNAPSHOT_DIR/$label"
  [ -d "$dir" ] || return 0
  ls -1 "$dir" 2>/dev/null \
    | grep -E '\.json$' | grep -v '^latest\.json$' \
    | sort -r | sed -n '2p' | awk -v d="$dir" '{print d"/"$0}'
}

latest_snapshot() {
  local label; label="$(sanitize_label "$1")"
  local link="$SNAPSHOT_DIR/$label/latest.json"
  [ -L "$link" ] || [ -f "$link" ] || return 0
  # Resolve symlink to absolute path.
  (cd "$(dirname "$link")" && readlink -f "$link" 2>/dev/null || readlink "$link")
}

# Diff two snapshot JSON files, emit markdown-ish human diff.
diff_two() {
  local a="$1" b="$2" label="$3"
  jq -rn \
    --arg label "$label" \
    --slurpfile A "$a" --slurpfile B "$b" '
    ($A[0]) as $old | ($B[0]) as $new |
    def set(xs): (xs // []) | map(tostring) | unique;
    def added(k; a; b):  (set(b[k // ""]) - set(a[k // ""]));
    def removed(k; a; b): (set(a[k // ""]) - set(b[k // ""]));
    {
      label: $label,
      when: {from: ($old.audit_time // "?"), to: ($new.audit_time // "?")},
      kernel:   (if $old.os.kernel  != $new.os.kernel  then {from: $old.os.kernel,  to: $new.os.kernel}  else null end),
      pretty:   (if $old.os.pretty  != $new.os.pretty  then {from: $old.os.pretty,  to: $new.os.pretty}  else null end),
      hostname: (if $old.hostname   != $new.hostname   then {from: $old.hostname,   to: $new.hostname}   else null end),
      memory:   (if $old.hw.memory_gb != $new.hw.memory_gb then {from: $old.hw.memory_gb, to: $new.hw.memory_gb} else null end),
      tools_added:   (($new.tools   // []) - ($old.tools   // [])),
      tools_removed: (($old.tools   // []) - ($new.tools   // [])),
      ips_added:     (($new.network.all_ips // []) - ($old.network.all_ips // [])),
      ips_removed:   (($old.network.all_ips // []) - ($new.network.all_ips // [])),
      docker_added:   ([$new.containers.docker[]?.name] - [$old.containers.docker[]?.name]),
      docker_removed: ([$old.containers.docker[]?.name] - [$new.containers.docker[]?.name]),
      lxc_added:   (($new.containers.lxc_classic_dirs // []) - ($old.containers.lxc_classic_dirs // [])),
      lxc_removed: (($old.containers.lxc_classic_dirs // []) - ($new.containers.lxc_classic_dirs // [])),
      listeners_added:   ([$new.listeners[]?.addr] - [$old.listeners[]?.addr]),
      listeners_removed: ([$old.listeners[]?.addr] - [$new.listeners[]?.addr])
    } |
    "## " + .label + " diff\n"
    + "- " + .when.from + " → " + .when.to + "\n"
    + (if .kernel   then "- kernel: " + .kernel.from + " → " + .kernel.to + "\n" else "" end)
    + (if .pretty   then "- OS: " + .pretty.from + " → " + .pretty.to + "\n" else "" end)
    + (if .hostname then "- hostname: " + .hostname.from + " → " + .hostname.to + "\n" else "" end)
    + (if .memory   then "- memory_gb: " + (.memory.from|tostring) + " → " + (.memory.to|tostring) + "\n" else "" end)
    + (if (.tools_added|length)   > 0 then "- tools +:   " + (.tools_added   | join(", ")) + "\n" else "" end)
    + (if (.tools_removed|length) > 0 then "- tools -:   " + (.tools_removed | join(", ")) + "\n" else "" end)
    + (if (.docker_added|length)   > 0 then "- docker +:  " + (.docker_added   | join(", ")) + "\n" else "" end)
    + (if (.docker_removed|length) > 0 then "- docker -:  " + (.docker_removed | join(", ")) + "\n" else "" end)
    + (if (.lxc_added|length)   > 0 then "- lxc +:     " + (.lxc_added   | join(", ")) + "\n" else "" end)
    + (if (.lxc_removed|length) > 0 then "- lxc -:     " + (.lxc_removed | join(", ")) + "\n" else "" end)
    + (if (.ips_added|length)   > 0 then "- ips +:     " + (.ips_added   | join(", ")) + "\n" else "" end)
    + (if (.ips_removed|length) > 0 then "- ips -:     " + (.ips_removed | join(", ")) + "\n" else "" end)
    + (if (.listeners_added|length)   > 0 then "- listeners +: " + (.listeners_added   | join(", ")) + "\n" else "" end)
    + (if (.listeners_removed|length) > 0 then "- listeners -: " + (.listeners_removed | join(", ")) + "\n" else "" end)
  '
}

# ---------- main ----------

while [ $# -gt 0 ]; do
  case "$1" in
    --format)
      shift; [ $# -gt 0 ] || { echo "--format needs a value" >&2; exit 2; }
      FORMAT="$1"; shift ;;
    --format=*) FORMAT="${1#--format=}"; shift ;;
    --file)
      shift; [ $# -gt 0 ] || { echo "--file needs a value" >&2; exit 2; }
      FILE_TARGETS="$1"; shift ;;
    --file=*) FILE_TARGETS="${1#--file=}"; shift ;;
    --save) SAVE=1; shift ;;
    --diff) DIFF=1; shift ;;
    --list) LIST=1; shift
            if [ $# -gt 0 ] && [[ "$1" != -* ]]; then LIST_LABEL="$1"; shift; fi ;;
    --snapshot-dir)
      shift; [ $# -gt 0 ] || { echo "--snapshot-dir needs a value" >&2; exit 2; }
      SNAPSHOT_DIR="$1"; shift ;;
    --snapshot-dir=*) SNAPSHOT_DIR="${1#--snapshot-dir=}"; shift ;;
    --probe) PROBE_MODE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    --) shift; while [ $# -gt 0 ]; do TARGETS+=("$1"); shift; done ;;
    -*) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    *) TARGETS+=("$1"); shift ;;
  esac
done

require_jq

if [ "$PROBE_MODE" -eq 1 ]; then
  probe_run
  exit 0
fi

if [ "$LIST" -eq 1 ]; then
  list_snapshots "$LIST_LABEL"
  exit 0
fi

if [ -n "$FILE_TARGETS" ]; then
  if [ ! -f "$FILE_TARGETS" ]; then
    echo "host-audit: file not found: $FILE_TARGETS" >&2
    exit 1
  fi
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%%#*}"
    line="$(printf '%s' "$line" | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//')"
    [ -n "$line" ] && TARGETS+=("$line")
  done < "$FILE_TARGETS"
fi

if [ "${#TARGETS[@]}" -eq 0 ]; then
  echo "host-audit: no targets given" >&2
  usage >&2
  exit 2
fi

case "$FORMAT" in
  markdown|json|claude) : ;;
  *) echo "host-audit: unknown format: $FORMAT" >&2; exit 2 ;;
esac

# --- diff mode: no probing; just compare on-disk snapshots per label. ---
if [ "$DIFF" -eq 1 ]; then
  first=1
  for spec in "${TARGETS[@]}"; do
    parsed="$(parse_target "$spec")"
    label="$(printf '%s' "$parsed" | cut -f1)"
    latest="$(latest_snapshot "$label")"
    previous="$(previous_snapshot "$label")"
    if [ $first -eq 1 ]; then first=0; else echo; fi
    if [ -z "$latest" ] || [ ! -f "$latest" ]; then
      echo "## $label diff"
      echo "- No snapshots saved yet for '$label'. Run with --save first."
      continue
    fi
    if [ -z "$previous" ] || [ ! -f "$previous" ]; then
      echo "## $label diff"
      echo "- Only one snapshot exists for '$label' — nothing to diff against yet."
      continue
    fi
    diff_two "$previous" "$latest" "$label"
  done
  exit 0
fi

# --- probe each target, optionally save, then format ---
json_items=()
formatted_items=()

for spec in "${TARGETS[@]}"; do
  parsed="$(parse_target "$spec")"
  label="$(printf '%s' "$parsed" | cut -f1)"
  method="$(printf '%s' "$parsed" | cut -f2)"
  target="$(printf '%s' "$parsed" | cut -f3)"
  pass="$(printf '%s' "$parsed" | cut -f4)"

  probe_json="$(run_probe "$method" "$target" "$pass")"

  audit_time="$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
  augmented="$(printf '%s' "$probe_json" | jq -c \
    --arg l "$label" --arg t "$target" --arg m "$method" --arg a "$audit_time" --arg sv "$SCHEMA_VERSION" \
    '. + {label: $l, target: $t, auth_method: $m, audit_time: $a, schema_version: $sv}' 2>/dev/null)"
  if [ -z "$augmented" ]; then
    augmented="$(jq -n --arg l "$label" --arg t "$target" --arg m "$method" --arg a "$audit_time" --arg sv "$SCHEMA_VERSION" \
      '{schema_version: $sv, label: $l, target: $t, auth_method: $m, audit_time: $a, reachable: false, error: "invalid probe output"}')"
  fi

  if [ "$SAVE" -eq 1 ]; then
    saved="$(printf '%s' "$augmented" | save_snapshot "$label")"
    if [ -n "$saved" ] && [ "$FORMAT" != "json" ]; then
      echo "# saved: $saved" >&2
    fi
  fi

  json_items+=("$augmented")
done

if [ "$FORMAT" = "json" ]; then
  printf '%s\n' "${json_items[@]}" | jq -s '.'
  exit 0
fi

first=1
for augmented in "${json_items[@]}"; do
  label="$(printf '%s' "$augmented" | jq -r '.label // "host"')"
  if [ $first -eq 1 ]; then first=0; else echo; fi
  if [ "$FORMAT" = "claude" ]; then
    printf '%s' "$augmented" | format_claude_one "$label"
  else
    printf '%s' "$augmented" | format_markdown_one "$label"
  fi
done
