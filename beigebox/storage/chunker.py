"""
Text chunker for document indexing.

Splits text into overlapping chunks, respecting paragraph boundaries where
possible.  Falls back to hard splits when a single paragraph exceeds the
chunk budget.  No external dependencies.
"""
from __future__ import annotations

import re


def chunk_text(
    text: str,
    chunk_chars: int = 1200,
    overlap_chars: int = 150,
    source_file: str = "",
) -> list[dict]:
    """
    Split text into overlapping chunks.

    Tries to split at paragraph boundaries (blank lines).  Falls back to hard
    splits at chunk_chars when a paragraph is too large.

    Returns a list of dicts:
      {
        "text":        str,   # chunk content (stripped)
        "chunk_index": int,   # 0-based index
        "source_file": str,   # passed through from caller
        "char_offset": int,   # start position in original text
      }
    """
    if not text.strip():
        return []

    # Split on paragraph boundaries, keeping the delimiter with the preceding block.
    raw_parts = re.split(r"(\n\n+)", text)
    parts: list[str] = []
    i = 0
    while i < len(raw_parts):
        if i + 1 < len(raw_parts) and re.match(r"\n\n+", raw_parts[i + 1]):
            parts.append(raw_parts[i] + raw_parts[i + 1])
            i += 2
        else:
            if raw_parts[i]:
                parts.append(raw_parts[i])
            i += 1

    chunks: list[dict] = []
    current = ""
    current_offset = 0
    text_offset = 0

    for part in parts:
        if current and len(current) + len(part) > chunk_chars:
            if current.strip():
                chunks.append({
                    "text": current.strip(),
                    "chunk_index": len(chunks),
                    "source_file": source_file,
                    "char_offset": current_offset,
                })
            overlap_start = max(0, len(current) - overlap_chars)
            current = current[overlap_start:] + part
            current_offset = text_offset - overlap_chars
        else:
            if not current:
                current_offset = text_offset
            current += part

        # Hard split if a single part is larger than the budget.
        while len(current) > chunk_chars:
            chunks.append({
                "text": current[:chunk_chars].strip(),
                "chunk_index": len(chunks),
                "source_file": source_file,
                "char_offset": current_offset,
            })
            current_offset += chunk_chars - overlap_chars
            current = current[chunk_chars - overlap_chars:]

        text_offset += len(part)

    if current.strip():
        chunks.append({
            "text": current.strip(),
            "chunk_index": len(chunks),
            "source_file": source_file,
            "char_offset": current_offset,
        })

    return chunks
