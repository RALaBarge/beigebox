#!/usr/bin/env python3
import subprocess
from pathlib import Path

ZIP_SRC = Path("/srv/agent_inputs/job123.zip")
AGENT_CMD = ["/usr/bin/python3", "-m", "my_agent", "--workdir", "/work"]

def run(cmd):
    print("+", " ".join(map(str, cmd)))
    subprocess.run(list(map(str, cmd)), check=True)

def main():
    if not ZIP_SRC.is_file():
        raise SystemExit(f"ZIP missing: {ZIP_SRC}")

    # Bubblewrap sandbox:
    # - Empty root
    # - RO bind host runtimes
    # - tmpfs /work
    # - RO bind zip into /input/job.zip
    # - no network
    bwrap = [
        "bwrap",
        "--die-with-parent",
        "--unshare-all",
        "--unshare-net",
        "--new-session",

        "--ro-bind", "/usr", "/usr",
        "--ro-bind", "/bin", "/bin",
        "--ro-bind", "/lib", "/lib",
        "--ro-bind", "/lib64", "/lib64",

        "--tmpfs", "/work",
        "--dir", "/input",
        "--ro-bind", str(ZIP_SRC), "/input/job.zip",

        "--chdir", "/work",
        # optional: small /dev
        "--dev", "/dev",
        # optional: proc (some libraries/tools expect it)
        "--proc", "/proc",
    ]

    # Extract zip inside sandbox, then run agent
    inner = r"""
set -euo pipefail
python3 - <<'PY'
import zipfile
from pathlib import Path
zip_path = Path("/input/job.zip")
out_dir  = Path("/work")
with zipfile.ZipFile(zip_path, 'r') as z:
    # zip-slip protection
    for member in z.infolist():
        p = (out_dir / member.filename).resolve()
        if not str(p).startswith(str(out_dir.resolve()) + "/") and p != out_dir.resolve():
            raise SystemExit(f"Blocked zip path traversal: {member.filename}")
    z.extractall(out_dir)
PY
exec "$@"
"""
    run(bwrap + ["/bin/sh", "-lc", inner, "--"] + AGENT_CMD)

if __name__ == "__main__":
    main()
