"""
Zip inspector plugin.

Inspects zip archives in workspace/in/ — returns a file tree and previews
UTF-8 decodable text content.

Examples the LLM would route here:
  "inspect test.zip"
  "show contents of archive.zip"
  "what's inside data.zip"

Enable in config.yaml:
    tools:
      plugins:
        enabled: true
        zip_inspector:
          enabled: true
"""

import os
import zipfile
from pathlib import Path

PLUGIN_NAME = "zip_inspector"

# Resolve workspace paths relative to the project root
_APP_ROOT      = Path(__file__).parent.parent
_WORKSPACE_IN  = _APP_ROOT / "workspace" / "in"
_WORKSPACE_OUT = _APP_ROOT / "workspace" / "out"

# Output caps
_MAX_PREVIEW_CHARS = 2000   # per file preview
_MAX_TOTAL_CHARS = 8000     # total output cap


def _fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / 1024 / 1024:.1f} MB"


def _build_tree(names: list[str], sizes: dict[str, int]) -> str:
    """Build a simple ASCII tree from a flat list of zip member paths."""
    # Sort so directories group naturally
    sorted_names = sorted(names)
    lines = []
    for i, name in enumerate(sorted_names):
        is_last = i == len(sorted_names) - 1
        prefix = "└── " if is_last else "├── "
        size_str = f"  ({_fmt_size(sizes[name])})" if name in sizes else ""
        lines.append(f"{prefix}{name}{size_str}")
    return "\n".join(lines)


class ZipInspectorTool:
    """Inspect zip archives — returns file tree and text previews."""

    def run(self, query: str) -> str:
        query = query.strip()

        # Resolve path: absolute takes priority, else look in workspace/in/
        if os.path.isabs(query):
            zip_path = Path(query)
        else:
            # Strip surrounding quotes if any
            name = query.strip("'\"")
            zip_path = _WORKSPACE_IN / name

        if not zip_path.exists():
            return f"File not found: {zip_path}"
        if not zipfile.is_zipfile(zip_path):
            return f"Not a valid zip file: {zip_path.name}"

        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                infos = zf.infolist()

                # Separate dirs from files
                file_members = [m for m in infos if not m.filename.endswith("/")]
                dir_members  = [m for m in infos if m.filename.endswith("/")]

                sizes = {m.filename: m.file_size for m in file_members}
                all_names = [m.filename for m in dir_members] + [m.filename for m in file_members]

                tree = _build_tree(all_names, sizes)
                total_uncompressed = sum(m.file_size for m in file_members)
                total_compressed   = sum(m.compress_size for m in file_members)

                output_parts = [
                    f"Archive: {zip_path.name}",
                    f"Members: {len(file_members)} files, {len(dir_members)} dirs",
                    f"Size: {_fmt_size(total_compressed)} compressed → {_fmt_size(total_uncompressed)} uncompressed",
                    "",
                    "FILE TREE:",
                    tree,
                ]

                total_chars = sum(len(p) for p in output_parts)

                # Preview UTF-8 decodable files
                previews = []
                for member in file_members:
                    if total_chars >= _MAX_TOTAL_CHARS:
                        previews.append("… (output cap reached)")
                        break
                    try:
                        raw = zf.read(member.filename)
                        text = raw.decode("utf-8")
                    except (UnicodeDecodeError, KeyError):
                        continue  # skip binary or missing files

                    if not text.strip():
                        continue

                    snippet = text[:_MAX_PREVIEW_CHARS]
                    if len(text) > _MAX_PREVIEW_CHARS:
                        snippet += f"\n… ({len(text) - _MAX_PREVIEW_CHARS} chars truncated)"

                    preview = f"\n--- {member.filename} ---\n{snippet}"
                    total_chars += len(preview)
                    previews.append(preview)

                if previews:
                    output_parts.append("")
                    output_parts.extend(previews)

                report = "\n".join(output_parts)

                # Save inspection report to workspace/out/
                out_name = f"{zip_path.stem}_inspection.txt"
                try:
                    _WORKSPACE_OUT.mkdir(parents=True, exist_ok=True)
                    (_WORKSPACE_OUT / out_name).write_text(report, encoding="utf-8")
                    report += f"\n\n[Report saved to workspace/out/{out_name}]"
                except Exception:
                    pass

                return report

        except zipfile.BadZipFile as e:
            return f"Could not read zip: {e}"
        except Exception as e:
            return f"Error inspecting zip: {e}"
