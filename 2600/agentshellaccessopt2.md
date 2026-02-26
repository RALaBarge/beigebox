#!/usr/bin/env python3
import os
import subprocess
from pathlib import Path

ZIP_SRC = Path("/srv/agent_inputs/job123.zip")

# Whatever you run as the agent inside the sandbox:
AGENT_CMD = ["/usr/bin/python3", "-m", "my_agent", "--workdir", "/work"]

MAX_ZIP_BYTES = 200 * 1024 * 1024  # 200 MiB safety cap (adjust)

def run(cmd):
    print("+", " ".join(map(str, cmd)))
    subprocess.run(list(map(str, cmd)), check=True)

def main():
    if not ZIP_SRC.is_file():
        raise SystemExit(f"ZIP missing: {ZIP_SRC}")
    if ZIP_SRC.stat().st_size > MAX_ZIP_BYTES:
        raise SystemExit(f"ZIP too large (> {MAX_ZIP_BYTES} bytes)")

    # tmpfs flags:
    # - noexec: cannot execute unpacked binaries/scripts directly from /work
    # - nodev,nosuid: standard hardening for scratch mounts
    tmpfs_opts = "size=1024m,nodev,nosuid,noexec"

    # bwrap:
    # - unshare-all + unshare-net => no network and fresh namespaces
    # - tmpfs /work with noexec
    # - RO bind the ZIP only
    # - RO bind minimal runtime paths so python can run
    bwrap = [
        "bwrap",
        "--die-with-parent",
        "--unshare-all",
        "--unshare-net",
        "--new-session",

        # Minimal runtime (RO). Adjust for your distro layout.
        "--ro-bind", "/usr", "/usr",
        "--ro-bind", "/bin", "/bin",
        "--ro-bind", "/lib", "/lib",
        "--ro-bind", "/lib64", "/lib64",

        # Provide proc/dev (often required for normal process behavior)
        "--proc", "/proc",
        "--dev", "/dev",

        # Scratch in RAM with noexec
        "--tmpfs", "/work",
        "--remount-ro", "/work",  # keeps it a mountpoint; writeability is about filesystem perms, see note below
        "--chdir", "/work",
    ]

    # bubblewrap's --tmpfs doesn't accept mount options directly.
    # So we do a remount inside the sandbox to apply noexec/nodev/nosuid.
    # Inner script: remount /work with noexec, extract ZIP safely, then run agent.
    inner = r"""
set -euo pipefail

# Remount /work with hardened options (noexec,nodev,nosuid). Keep it writable.
mount -o remount,%(tmpfs_opts)s /work

mkdir -p /input
# /input/job.zip is already bind-mounted by bwrap (see below)

python3 - <<'PY'
import zipfile
from pathlib import Path

zip_path = Path("/input/job.zip")
out_dir  = Path("/work")

MAX_TOTAL = 1024 * 1024 * 1024  # 1 GiB extracted cap (adjust)
MAX_FILES = 20000               # file count cap (adjust)

total = 0
count = 0

with zipfile.ZipFile(zip_path, 'r') as z:
    # zip-slip + zip-bomb-ish checks
    out_root = out_dir.resolve()

    for m in z.infolist():
        count += 1
        if count > MAX_FILES:
            raise SystemExit("Too many files in zip")

        total += m.file_size
        if total > MAX_TOTAL:
            raise SystemExit("Zip expands too large")

        dest = (out_dir / m.filename).resolve()
        if dest == out_root:
            continue
        if not str(dest).startswith(str(out_root) + "/"):
            raise SystemExit(f"Blocked zip path traversal: {m.filename}")

    z.extractall(out_dir)

print("unzipped_ok")
PY

exec "$@"
""" % {"tmpfs_opts": tmpfs_opts}

    # Bind the ZIP read-only
    bwrap += [
        "--dir", "/input",
        "--ro-bind", str(ZIP_SRC), "/input/job.zip",
        "/bin/sh", "-lc", inner, "--",
        *AGENT_CMD,
    ]

    run(bwrap)

if __name__ == "__main__":
    main()
