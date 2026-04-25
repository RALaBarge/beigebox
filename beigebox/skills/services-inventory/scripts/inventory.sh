#!/usr/bin/env bash
# services-inventory: cross-platform service/container/VM discovery.
# Emits either a human table (default) or JSON (--json).
# Local by default; --host / --hosts / --all-hosts for SSH aggregation.
set -uo pipefail

OS="$(uname -s)"
OUTPUT_JSON=0
PROBE_MODE=0
INCLUDE_LISTENERS=0
HOSTS=()
CONFIG_FILE="${XDG_CONFIG_HOME:-$HOME/.config}/beigebox/inventory-hosts"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)" || SCRIPT_DIR=""
SCRIPT_PATH="${SCRIPT_DIR:+$SCRIPT_DIR/}$(basename "${BASH_SOURCE[0]}")"

usage() {
  cat <<'EOF'
Usage: inventory.sh [OPTIONS]

Discover running services across container/VM backends on Linux and macOS.

Options:
  --json               Emit JSON instead of a human table
  --host LABEL=TARGET  Inspect remote host via SSH. TARGET is an ssh alias or
    --host TARGET      user@host. LABEL is an optional display name. Repeatable.
  --hosts LIST         Comma-separated list of TARGETs (or LABEL=TARGETs).
                       Include 'localhost' to also probe the local host.
  --all-hosts          Read hosts from $XDG_CONFIG_HOME/beigebox/inventory-hosts
                       (default ~/.config/beigebox/inventory-hosts).
                       Format: "label ssh-target" or "ssh-target" per line.
  --include-listeners  Also include raw listening ports (filtered). Off by default.
  --probe              (internal) Probe local backends and emit raw JSON.
  -h, --help           Show this help.

Examples:
  inventory.sh
  inventory.sh --json
  inventory.sh --host debian=user@debian.home
  inventory.sh --hosts localhost,user@debian.home --json
  inventory.sh --all-hosts --include-listeners
EOF
}

# ---------- helpers ----------

have() { command -v "$1" >/dev/null 2>&1; }

# timeout shim: GNU on Linux, gtimeout on mac w/ coreutils, no-op otherwise.
if have timeout; then
  _t() { timeout "$@"; }
elif have gtimeout; then
  _t() { gtimeout "$@"; }
else
  _t() { shift; "$@"; }
fi

require_jq() {
  if ! have jq; then
    echo "services-inventory: jq is required. Install via 'apt install jq' or 'brew install jq'." >&2
    exit 2
  fi
}

# Safely emit '[]' on any probe failure so aggregation keeps going.
safe() {
  local out
  out="$("$@" 2>/dev/null)" || out=""
  if [ -z "$out" ]; then
    echo '[]'
  else
    echo "$out"
  fi
}

# ---------- linux/cross probes ----------

probe_docker() {
  have docker || { echo '[]'; return; }
  _t 5 docker info >/dev/null 2>&1 || { echo '[]'; return; }
  _t 5 docker ps --format '{{json .}}' 2>/dev/null \
    | jq -sc '
      map({
        backend: "docker",
        name: .Names,
        state: (.State // "running"),
        addresses: ((.Ports // "") | if . == "" then [] else split(", ") end),
        connect: (
          (.Ports // "")
          | [scan("(?:0\\.0\\.0\\.0|127\\.0\\.0\\.1|\\[::\\]):\\d+")]
          | map(sub("^.*:"; ""))
          | unique
          | map("http://localhost:" + .)
        ),
        extra: (
          "image=" + (.Image // "?")
          + (
            (.Labels // "")
            | (capture("com\\.docker\\.compose\\.project=(?<p>[^,]+)") ? // {p: null}).p
            | if . == null then "" else " compose=" + . end
          )
        )
      })
    '
}

probe_podman() {
  have podman || { echo '[]'; return; }
  _t 5 podman ps --format json 2>/dev/null \
    | jq -c '
      map({
        backend: "podman",
        name: (.Names[0] // .Name // "?"),
        state: (.State // "running"),
        addresses: ([(.Ports // [])[] | (
          (.host_ip // "0.0.0.0") + ":" + (.host_port|tostring) + "->" +
          (.container_port|tostring) + "/" + (.protocol // "tcp")
        )]),
        connect: ([(.Ports // [])[] | select(.host_port != null) | "http://localhost:" + (.host_port|tostring)] | unique),
        extra: ("image=" + (.Image // "?"))
      })
    '
}

probe_incus() {
  have incus || { echo '[]'; return; }
  _t 5 incus list --format json 2>/dev/null \
    | jq -c '
      map(select((.status // "") | ascii_downcase == "running") | {
        backend: "incus",
        name: .name,
        state: ((.status // "unknown") | ascii_downcase),
        addresses: [
          (.state.network // {}) | to_entries[]
          | select(.key != "lo")
          | .value.addresses[]?
          | select(.scope == "global")
          | .family + ":" + .address
        ],
        connect: ((
          [
            (.state.network // {}) | to_entries[]
            | select(.key != "lo")
            | .value.addresses[]?
            | select(.scope == "global" and .family == "inet")
            | .address
          ]
          | map("ssh root@" + .)
        ) + ["incus shell " + .name]),
        extra: (
          "type=" + (.type // "?")
          + " image=" + (.config["image.description"] // .config["image.os"] // "?")
        )
      })
    '
}

# LXD CLI (lxc) — same JSON schema as incus.
probe_lxd() {
  have lxc || { echo '[]'; return; }
  # Disambiguate: snap/LXD-style `lxc list` differs from classic `lxc-ls`.
  # `lxc list --format json` works for LXD. If it fails we silently skip;
  # classic LXC is handled by probe_lxc_classic.
  _t 5 lxc list --format json 2>/dev/null \
    | jq -c '
      (. // []) | map(select((.status // "") | ascii_downcase == "running") | {
        backend: "lxd",
        name: .name,
        state: ((.status // "unknown") | ascii_downcase),
        addresses: [
          (.state.network // {}) | to_entries[]
          | select(.key != "lo")
          | .value.addresses[]?
          | select(.scope == "global")
          | .family + ":" + .address
        ],
        connect: ((
          [
            (.state.network // {}) | to_entries[]
            | select(.key != "lo")
            | .value.addresses[]?
            | select(.scope == "global" and .family == "inet")
            | .address
          ]
          | map("ssh root@" + .)
        ) + ["lxc shell " + .name]),
        extra: ("type=" + (.type // "?"))
      })
    '
}

# Classic LXC (Debian/Ubuntu) — different CLI: lxc-ls / lxc-info.
probe_lxc_classic() {
  have lxc-ls || { echo '[]'; return; }
  local names
  names="$(_t 5 lxc-ls -1 --running 2>/dev/null)" || { echo '[]'; return; }
  [ -z "$names" ] && { echo '[]'; return; }
  local result='[]'
  while IFS= read -r name; do
    [ -z "$name" ] && continue
    local ip
    ip="$(_t 3 lxc-info -n "$name" -iH 2>/dev/null | head -1 || echo "")"
    result="$(
      printf '%s' "$result" \
      | jq -c --arg n "$name" --arg ip "$ip" '
        . + [{
          backend: "lxc",
          name: $n,
          state: "running",
          addresses: (if $ip == "" then [] else [$ip] end),
          connect: (
            (if $ip == "" then [] else ["ssh root@" + $ip] end)
            + ["lxc-attach -n " + $n]
          ),
          extra: null
        }]
      '
    )"
  done <<<"$names"
  echo "$result"
}

probe_virsh() {
  have virsh || { echo '[]'; return; }
  local names
  names="$(_t 5 virsh --connect qemu:///system list --state-running --name 2>/dev/null)" || \
    names="$(_t 5 virsh list --state-running --name 2>/dev/null)" || { echo '[]'; return; }
  [ -z "$names" ] && { echo '[]'; return; }
  local result='[]'
  while IFS= read -r name; do
    [ -z "$name" ] && continue
    local ips
    ips="$(_t 3 virsh --connect qemu:///system domifaddr "$name" 2>/dev/null \
      | awk 'NR>2 && $4 ~ /\// {split($4,a,"/"); if (a[1] != "") print a[1]}' \
      | paste -sd',' - || echo "")"
    result="$(
      printf '%s' "$result" \
      | jq -c --arg n "$name" --arg ips "$ips" '
        . + [{
          backend: "libvirt",
          name: $n,
          state: "running",
          addresses: ($ips | split(",") | map(select(length > 0))),
          connect: (
            ($ips | split(",") | map(select(length > 0)) | map("ssh root@" + .))
            + ["virsh console " + $n]
          ),
          extra: null
        }]
      '
    )"
  done <<<"$names"
  echo "$result"
}

probe_nspawn() {
  have machinectl || { echo '[]'; return; }
  _t 5 machinectl list --no-legend 2>/dev/null \
    | awk '{
        # MACHINE CLASS SERVICE OS VERSION ADDRESSES...
        if (NF >= 2 && ($2 == "container" || $2 == "vm")) {
          name=$1; class=$2; svc=$3; os=$4;
          addr="";
          for (i=6; i<=NF; i++) addr = (addr=="" ? $i : addr "," $i);
          print name "\t" class "\t" os "\t" addr;
        }
      }' \
    | jq -R -sc '
      split("\n") | map(select(length > 0)) | map(
        split("\t") as $p |
        {
          backend: "nspawn",
          name: $p[0],
          state: "running",
          addresses: ($p[3] | split(",") | map(select(length > 0))),
          connect: (
            ($p[3] | split(",") | map(select(length > 0)) | map("ssh root@" + .))
            + ["machinectl shell " + $p[0]]
          ),
          extra: ("class=" + $p[1] + " os=" + $p[2])
        }
      )
    '
}

# ---------- macOS probes ----------

probe_orb() {
  have orbctl || have orb || { echo '[]'; return; }
  local bin; bin="$(command -v orbctl 2>/dev/null || command -v orb)"
  # OrbStack: "orbctl list -f json" on recent versions.
  _t 5 "$bin" list -f json 2>/dev/null \
    | jq -c '
      (if type == "array" then . else [.] end)
      | map(select((.state // .status // "") | ascii_downcase == "running") | {
          backend: "orb",
          name: (.name // "?"),
          state: ((.state // .status // "running") | ascii_downcase),
          addresses: ([.address // .ip // empty] | map(tostring)),
          connect: (([.address // .ip // empty] | map("ssh " + (if .|test("@") then . else "default@" + . end))) + ["orb -m " + (.name // "?")]),
          extra: ("image=" + (.image.distro // .distro // "?"))
        })
    ' 2>/dev/null || echo '[]'
}

probe_colima() {
  have colima || { echo '[]'; return; }
  _t 5 colima list -j 2>/dev/null \
    | jq -sc '
      map(select((.status // "") | ascii_downcase | contains("running")) | {
        backend: "colima",
        name: (.name // "default"),
        state: ((.status // "unknown") | ascii_downcase),
        addresses: ([.address // empty]),
        connect: ["colima ssh -p " + (.name // "default")],
        extra: ("runtime=" + (.runtime // "?") + " arch=" + (.arch // "?"))
      })
    ' 2>/dev/null || echo '[]'
}

probe_multipass() {
  have multipass || { echo '[]'; return; }
  _t 5 multipass list --format json 2>/dev/null \
    | jq -c '
      .list // [] | map(select((.state // "") | ascii_downcase == "running") | {
        backend: "multipass",
        name: .name,
        state: ((.state // "unknown") | ascii_downcase),
        addresses: (.ipv4 // []),
        connect: (((.ipv4 // []) | map("ssh ubuntu@" + .)) + ["multipass shell " + .name]),
        extra: ("release=" + (.release // "?"))
      })
    ' 2>/dev/null || echo '[]'
}

probe_vbox() {
  have VBoxManage || { echo '[]'; return; }
  local names
  names="$(_t 5 VBoxManage list runningvms 2>/dev/null | sed -E 's/^"([^"]+)".*/\1/')" || { echo '[]'; return; }
  [ -z "$names" ] && { echo '[]'; return; }
  local result='[]'
  while IFS= read -r name; do
    [ -z "$name" ] && continue
    local info ip=""
    info="$(_t 3 VBoxManage guestproperty enumerate "$name" 2>/dev/null || echo "")"
    ip="$(printf '%s\n' "$info" | awk -F'[:,]' '/Net\/0\/V4\/IP/ {gsub(/^[ \t]+|[ \t]+$/,"",$4); print $4; exit}')"
    result="$(
      printf '%s' "$result" \
      | jq -c --arg n "$name" --arg ip "$ip" '
        . + [{
          backend: "vbox",
          name: $n,
          state: "running",
          addresses: (if $ip == "" then [] else [$ip] end),
          connect: (
            (if $ip == "" then [] else ["ssh user@" + $ip] end)
            + ["VBoxManage controlvm \"" + $n + "\" ..."]
          ),
          extra: null
        }]
      '
    )"
  done <<<"$names"
  echo "$result"
}

probe_parallels() {
  have prlctl || { echo '[]'; return; }
  _t 5 prlctl list --json --all --full 2>/dev/null \
    | jq -c '
      (. // []) | map(select((.status // .State // "") | ascii_downcase == "running") | {
        backend: "parallels",
        name: (.name // .Name // "?"),
        state: ((.status // .State // "running") | ascii_downcase),
        addresses: ([.ip_configured // empty] | map(tostring) | map(select(length > 0))),
        connect: (([.ip_configured // empty] | map(tostring) | map(select(length > 0)) | map("ssh user@" + .)) + ["prlctl enter \"" + (.name // .Name // "?") + "\""]),
        extra: ("os=" + (.OS // "?"))
      })
    ' 2>/dev/null || echo '[]'
}

probe_vmware() {
  have vmrun || { echo '[]'; return; }
  local lines
  lines="$(_t 5 vmrun list 2>/dev/null | tail -n +2)" || { echo '[]'; return; }
  [ -z "$lines" ] && { echo '[]'; return; }
  printf '%s\n' "$lines" \
    | jq -R -sc '
      split("\n") | map(select(length > 0)) | map({
        backend: "vmware",
        name: (split("/") | last | sub("\\.vmx$"; "")),
        state: "running",
        addresses: [],
        connect: ["vmrun -gu USER -gp PASS getGuestIPAddress \"" + . + "\""],
        extra: ("vmx=" + .)
      })
    '
}

probe_tart() {
  have tart || { echo '[]'; return; }
  _t 5 tart list --format json 2>/dev/null \
    | jq -c '
      (. // []) | map(select((.State // .state // "") | ascii_downcase == "running") | {
        backend: "tart",
        name: (.Name // .name // "?"),
        state: ((.State // .state // "running") | ascii_downcase),
        addresses: [],
        connect: ["tart ip \"" + (.Name // .name // "?") + "\"  # then: ssh admin@<ip>"],
        extra: ("os=" + (.OS // .os // "?"))
      })
    ' 2>/dev/null || echo '[]'
}

# ---------- listeners (opt-in) ----------

probe_listeners_linux() {
  have ss || { echo '[]'; return; }
  _t 5 ss -Hltnp 2>/dev/null \
    | awk '{
        # State Recv-Q Send-Q Local:Port Peer:Port Process
        local_addr=$4; proc=$6;
        split(local_addr, a, ":"); port=a[length(a)];
        # strip users:(("...
        if (match(proc, /"[^"]+"/)) { name=substr(proc, RSTART+1, RLENGTH-2) } else { name="?" }
        print name "\t" port "\t" local_addr;
      }' \
    | jq -R -sc '
      split("\n") | map(select(length > 0)) | map(split("\t") as $p |
        {
          backend: "listener",
          name: $p[0],
          state: "listening",
          addresses: [$p[2]],
          connect: (if ($p[2] | startswith("127.") or startswith("[::1]")) then ["http://localhost:" + $p[1]] else ["http://localhost:" + $p[1], "http://<host>:" + $p[1]] end),
          extra: ("port=" + $p[1])
        }
      ) | map(select(.name | test("^(docker-proxy|rootlessport|slirp4netns|podman)$") | not))
    '
}

probe_listeners_mac() {
  have lsof || { echo '[]'; return; }
  _t 5 lsof -nP -iTCP -sTCP:LISTEN 2>/dev/null \
    | awk 'NR>1 {
        name=$1; addr=$9;
        split(addr, a, ":"); port=a[length(a)];
        print name "\t" port "\t" addr;
      }' \
    | jq -R -sc '
      split("\n") | map(select(length > 0)) | map(split("\t") as $p |
        {
          backend: "listener",
          name: $p[0],
          state: "listening",
          addresses: [$p[2]],
          connect: ["http://localhost:" + $p[1]],
          extra: ("port=" + $p[1])
        }
      ) | unique_by(.addresses[0] + "|" + .name)
        | map(select(.name | test("^(com\\.docker|vpnkit|ssh|rapportd)") | not))
    '
}

probe_listeners() {
  case "$OS" in
    Linux)  probe_listeners_linux ;;
    Darwin) probe_listeners_mac ;;
    *)      echo '[]' ;;
  esac
}

# ---------- local aggregation ----------

do_local_probe() {
  require_jq
  local outputs=()
  outputs+=("$(probe_docker)")
  outputs+=("$(probe_podman)")
  case "$OS" in
    Linux)
      outputs+=("$(probe_incus)")
      outputs+=("$(probe_lxd)")
      outputs+=("$(probe_lxc_classic)")
      outputs+=("$(probe_virsh)")
      outputs+=("$(probe_nspawn)")
      ;;
    Darwin)
      outputs+=("$(probe_orb)")
      outputs+=("$(probe_colima)")
      outputs+=("$(probe_multipass)")
      outputs+=("$(probe_vbox)")
      outputs+=("$(probe_parallels)")
      outputs+=("$(probe_vmware)")
      outputs+=("$(probe_tart)")
      ;;
  esac
  if [ "$INCLUDE_LISTENERS" -eq 1 ]; then
    outputs+=("$(probe_listeners)")
  fi
  # Merge all probe outputs into one JSON array.
  printf '%s\n' "${outputs[@]}" | jq -sc 'map(select(. != null)) | add // []'
}

# ---------- remote execution ----------

# Parse "label=target" or just "target". Echoes "label\ttarget".
parse_host_spec() {
  local spec="$1" label target
  if [[ "$spec" == *"="* ]]; then
    label="${spec%%=*}"
    target="${spec#*=}"
  else
    target="$spec"
    label="$target"
  fi
  printf '%s\t%s\n' "$label" "$target"
}

do_remote_probe() {
  local target="$1"
  local remote_args="--probe"
  if [ "$INCLUDE_LISTENERS" -eq 1 ]; then
    remote_args="$remote_args --include-listeners"
  fi
  if [ -z "$SCRIPT_PATH" ] || [ ! -f "$SCRIPT_PATH" ]; then
    echo "services-inventory: cannot locate script for remote execution" >&2
    echo '[]'
    return
  fi
  ssh -o ConnectTimeout=5 -o BatchMode=yes "$target" \
      "bash -s -- $remote_args" < "$SCRIPT_PATH" 2>/dev/null || echo '[]'
}

# ---------- hosts config ----------

load_config_hosts() {
  if [ ! -f "$CONFIG_FILE" ]; then
    echo "services-inventory: config file not found: $CONFIG_FILE" >&2
    return 1
  fi
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%%#*}"
    # trim
    line="$(printf '%s' "$line" | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//')"
    [ -z "$line" ] && continue
    # "label target" or just "target"
    if [[ "$line" == *" "* ]]; then
      local label="${line%% *}"
      local target="${line#* }"
      target="$(printf '%s' "$target" | sed -E 's/^[[:space:]]+//')"
      HOSTS+=("$label=$target")
    else
      HOSTS+=("$line")
    fi
  done < "$CONFIG_FILE"
}

# ---------- output formatting ----------

format_table() {
  jq -r '
    def trunc($n): if length > $n then .[0:$n-1] + "…" else . end;
    (["HOST","BACKEND","NAME","STATE","ADDRESS","CONNECT","EXTRA"]),
    (.[] | [
      (.host // "localhost"),
      .backend,
      (.name // "?"),
      (.state // "?"),
      ((.addresses // []) | join(",") | trunc(40)),
      ((.connect // []) | .[0] // ""),
      ((.extra // "") | tostring | trunc(30))
    ])
    | @tsv
  ' | column -ts $'\t'
}

# ---------- main ----------

# Arg parsing
while [ $# -gt 0 ]; do
  case "$1" in
    --json) OUTPUT_JSON=1; shift ;;
    --probe) PROBE_MODE=1; shift ;;
    --include-listeners) INCLUDE_LISTENERS=1; shift ;;
    --host) shift; [ $# -gt 0 ] || { echo "--host needs an argument" >&2; exit 2; }; HOSTS+=("$1"); shift ;;
    --host=*) HOSTS+=("${1#--host=}"); shift ;;
    --hosts) shift; [ $# -gt 0 ] || { echo "--hosts needs an argument" >&2; exit 2; }
             IFS=',' read -r -a _tmp <<<"$1"; HOSTS+=("${_tmp[@]}"); shift ;;
    --hosts=*) IFS=',' read -r -a _tmp <<<"${1#--hosts=}"; HOSTS+=("${_tmp[@]}"); shift ;;
    --all-hosts) load_config_hosts || exit 1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

require_jq

# --probe mode: just run local probes and emit raw JSON (used over SSH).
if [ "$PROBE_MODE" -eq 1 ]; then
  do_local_probe
  exit 0
fi

# Decide scope.
if [ "${#HOSTS[@]}" -eq 0 ]; then
  # No hosts specified: local only.
  raw="$(do_local_probe | jq -c 'map(. + {host: "localhost"})')"
else
  # Aggregate across specified hosts.
  parts=()
  for spec in "${HOSTS[@]}"; do
    [ -z "$spec" ] && continue
    pair="$(parse_host_spec "$spec")"
    label="${pair%%$'\t'*}"
    target="${pair##*$'\t'}"
    if [ "$target" = "localhost" ] || [ "$target" = "local" ]; then
      part="$(do_local_probe)"
    else
      part="$(do_remote_probe "$target")"
    fi
    parts+=("$(printf '%s' "$part" | jq -c --arg h "$label" 'map(. + {host: $h})' 2>/dev/null || echo '[]')")
  done
  raw="$(printf '%s\n' "${parts[@]}" | jq -sc 'add // []')"
fi

if [ "$OUTPUT_JSON" -eq 1 ]; then
  printf '%s' "$raw" | jq .
else
  printf '%s' "$raw" | format_table
fi
