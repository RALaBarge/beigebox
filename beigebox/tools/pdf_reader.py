"""
PDF reader tool for the operator agent — powered by pdf_oxide (Rust-backed).

Reads PDFs from workspace/in/ and converts them to markdown or extracts
plain text, tables, and form fields. Accepts a filename or absolute path.

Requires: pip install pdf_oxide
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class PdfReaderTool:
    capture_tool_io: bool = True
    max_context_chars: int = 8000

    description = (
        "Read and extract content from a PDF file. "
        "Input: filename in workspace/in/ (e.g. 'report.pdf'), or absolute path. "
        "Returns markdown conversion of the full document including text and tables. "
        "Use when the user asks about a PDF or uploads one to workspace."
    )

    def __init__(self, workspace_in: str | Path | None = None):
        self._workspace_in = Path(workspace_in) if workspace_in else None

    def _resolve_path(self, input_str: str) -> Path:
        p = Path(input_str)
        if p.is_absolute() and p.exists():
            return p
        # Try workspace/in/
        if self._workspace_in:
            candidate = self._workspace_in / input_str
            if candidate.exists():
                return candidate
        # Try relative to cwd
        if p.exists():
            return p.resolve()
        raise FileNotFoundError(
            f"PDF not found: {input_str!r}. "
            f"Drop it into workspace/in/ and try again."
        )

    def run(self, input_str: str) -> str:
        try:
            import pdf_oxide as pox
        except ImportError:
            return (
                "pdf_oxide is not installed. "
                "Add 'pdf_oxide' to requirements.txt and rebuild."
            )

        input_str = input_str.strip()
        try:
            pdf_path = self._resolve_path(input_str)
        except FileNotFoundError as e:
            return str(e)

        try:
            doc = pox.PdfDocument(str(pdf_path))
            page_count = doc.page_count()

            parts = [f"# {pdf_path.name}  ({page_count} page{'s' if page_count != 1 else ''})\n"]

            for i in range(page_count):
                md = doc.to_markdown(i, detect_headings=True)
                if md.strip():
                    parts.append(f"\n---\n## Page {i + 1}\n\n{md}")

            # Append form fields if present
            try:
                fields = doc.get_form_fields()
                if fields:
                    parts.append("\n---\n## Form Fields\n")
                    for f in fields:
                        parts.append(f"- **{f.get('name', '?')}**: {f.get('value', '')}")
            except Exception:
                pass

            return "\n".join(parts)

        except Exception as e:
            logger.error("pdf_reader failed on %s: %s", input_str, e)
            return f"Error reading PDF {input_str!r}: {e}"
