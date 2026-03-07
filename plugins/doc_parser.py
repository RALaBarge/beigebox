"""
Document parser plugin.

Parses files from workspace/in/ into Markdown text suitable for LLM consumption.
After parsing, content is chunked and upserted into ChromaDB so it becomes
searchable via z: memory and the operator's memory recall tool.

Supported formats (via MarkItDown):
  PDF, DOCX, PPTX, XLSX, HTML, MD, TXT, CSV, JSON, XML, ZIP,
  images (JPG, PNG, GIF, BMP, TIFF, WEBP), audio

OCR support for scanned PDFs and standalone images:
  • Tesseract:  pip install pytesseract pillow  +  apt install tesseract-ocr
                Handles image files; MarkItDown calls it automatically.
  • Vision LLM: set ocr_model in config — MarkItDown passes each image page
                to the model for transcription (higher quality, uses VRAM).

Enable in config.yaml:
    tools:
      plugins:
        enabled: true
        doc_parser:
          enabled: true
          ocr_model: ""        # e.g. "llava:7b" or "qwen2-vl:7b"
          max_ref_tokens: 4000 # output token budget (approx 4 chars/token)
          chunk_size: 800      # chars per ChromaDB chunk
          chunk_overlap: 80    # overlap between adjacent chunks

Requires: pip install 'markitdown[all]'
"""

import hashlib
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

PLUGIN_NAME = "doc_parser"
PLUGIN_Z_ALIASES = {
    "parse": "doc_parser",
    "doc":   "doc_parser",
    "ingest": "doc_parser",
}

_APP_ROOT      = Path(__file__).parent.parent
_WORKSPACE_IN  = _APP_ROOT / "workspace" / "in"
_WORKSPACE_OUT = _APP_ROOT / "workspace" / "out"

_CHARS_PER_TOKEN = 4   # rough approximation for budget guard

# File extensions MarkItDown handles well without extra config
_SUPPORTED_EXTS = {
    ".pdf", ".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls",
    ".html", ".htm", ".md", ".txt", ".csv", ".json", ".xml",
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".tif", ".webp",
    ".zip",
}


# ── Config helpers ────────────────────────────────────────────────────────────

def _plugin_cfg() -> dict:
    try:
        from beigebox.config import get_config
        return get_config().get("tools", {}).get("plugins", {}).get("doc_parser", {})
    except Exception:
        return {}


def _backend_url() -> str:
    try:
        from beigebox.config import get_config
        return get_config().get("backend", {}).get("url", "http://localhost:11434")
    except Exception:
        return "http://localhost:11434"


# ── OCR helpers ───────────────────────────────────────────────────────────────

def _tesseract_available() -> bool:
    try:
        import pytesseract
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def _build_openai_client(base_url: str, model: str):
    """Return (client, model) for MarkItDown's llm_client kwarg."""
    try:
        from openai import OpenAI
        client = OpenAI(
            base_url=f"{base_url.rstrip('/')}/v1",
            api_key="ollama",
        )
        return client, model
    except ImportError:
        logger.warning("doc_parser: openai package not installed — vision OCR unavailable")
        return None, None


# ── MarkItDown parsing ────────────────────────────────────────────────────────

def _parse(file_path: Path, ocr_model: str) -> str:
    try:
        from markitdown import MarkItDown
    except ImportError:
        return "[doc_parser] markitdown not installed. Run: pip install 'markitdown[all]'"

    kwargs: dict = {}

    if ocr_model:
        client, model = _build_openai_client(_backend_url(), ocr_model)
        if client:
            kwargs["llm_client"] = client
            kwargs["llm_model"]  = model
            logger.debug("doc_parser: using vision OCR model '%s'", ocr_model)
    elif _tesseract_available():
        logger.debug("doc_parser: using Tesseract OCR")
        # MarkItDown auto-detects pytesseract when installed

    try:
        md     = MarkItDown(**kwargs)
        result = md.convert(str(file_path))
        return result.text_content or ""
    except Exception as e:
        logger.warning("doc_parser: MarkItDown failed on %s: %s", file_path.name, e)
        return f"[doc_parser] Parse error: {e}"


# ── Chunking ──────────────────────────────────────────────────────────────────

def _chunk(text: str, chunk_size: int, overlap: int) -> list[str]:
    """Split text into overlapping chunks, preferring paragraph breaks."""
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        segment = text[start:end]

        # Try to break at a paragraph boundary within the last 20% of the chunk
        if end < len(text):
            break_at = segment.rfind("\n\n", int(chunk_size * 0.8))
            if break_at != -1:
                end = start + break_at + 2

        chunks.append(text[start:end].strip())
        start = end - overlap if end - overlap > start else end

    return [c for c in chunks if c]


# ── ChromaDB ingestion ────────────────────────────────────────────────────────

def _ingest_chunks(chunks: list[str], source: str) -> int:
    """
    Embed and upsert chunks into ChromaDB via VectorStore.store_message().
    Returns number of chunks ingested. Silently skips if unavailable.

    Chunks are stored with:
      conversation_id = "doc:<source_hash>"  (groups chunks per document)
      role            = "document"
      model           = source filename      (for display in search results)
    """
    try:
        from beigebox.config import get_config, get_storage_paths
        from beigebox.storage.vector_store import VectorStore

        cfg              = get_config()
        _, vector_path   = get_storage_paths(cfg)
        embedding_model  = cfg.get("embedding", {}).get("model", "nomic-embed-text")
        embedding_url    = cfg.get("backend", {}).get("url", "http://localhost:11434")

        store    = VectorStore(
            embedding_model=embedding_model,
            embedding_url=embedding_url,
            chroma_path=vector_path,
        )
        src_hash = hashlib.sha256(source.encode()).hexdigest()[:8]
        conv_id  = f"doc:{src_hash}"

        for i, chunk in enumerate(chunks):
            store.store_message(
                message_id      = f"doc_{src_hash}_{i:04d}",
                conversation_id = conv_id,
                role            = "document",
                content         = chunk,
                model           = source,
            )

        return len(chunks)

    except Exception as e:
        logger.debug("doc_parser: ChromaDB ingest skipped: %s", e)
        return 0


# ── Tool class ────────────────────────────────────────────────────────────────

class DocParserTool:
    """
    Parse documents from workspace/in/ into Markdown.
    Chunks are also ingested into ChromaDB for z: memory recall.

    Usage (operator tool input):
        report.pdf
        slides.pptx
        /absolute/path/to/file.docx
    """

    def run(self, query: str) -> str:
        query = query.strip().strip("'\"")
        if not query:
            return "Usage: provide a filename from workspace/in/ (e.g. report.pdf)"

        # Resolve path
        if os.path.isabs(query):
            file_path = Path(query)
        else:
            file_path = _WORKSPACE_IN / query

        if not file_path.exists():
            try:
                files = sorted(f.name for f in _WORKSPACE_IN.iterdir() if f.is_file())
                hint  = f"Available in workspace/in: {files}" if files else "workspace/in/ is empty"
            except Exception:
                hint = ""
            return f"File not found: {query}. {hint}"

        ext = file_path.suffix.lower()
        if ext not in _SUPPORTED_EXTS:
            logger.info("doc_parser: unsupported extension '%s', attempting anyway", ext)

        cfg        = _plugin_cfg()
        max_tokens = int(cfg.get("max_ref_tokens", 4000))
        chunk_size = int(cfg.get("chunk_size", 800))
        overlap    = int(cfg.get("chunk_overlap", 80))
        ocr_model  = cfg.get("ocr_model", "")
        max_chars  = max_tokens * _CHARS_PER_TOKEN

        logger.info("doc_parser: parsing %s (ocr_model=%r)", file_path.name, ocr_model or "tesseract")
        text = _parse(file_path, ocr_model)

        if not text.strip():
            return f"[doc_parser] No text extracted from {file_path.name}. " \
                   f"For scanned PDFs, set ocr_model in config or install tesseract."

        # Ingest full text into ChromaDB
        chunks    = _chunk(text, chunk_size, overlap)
        n_ingested = _ingest_chunks(chunks, file_path.name)
        if n_ingested:
            logger.info("doc_parser: ingested %d chunks from %s into ChromaDB", n_ingested, file_path.name)

        # Apply token budget to the returned excerpt
        truncated = False
        if len(text) > max_chars:
            text      = text[:max_chars]
            truncated = True

        # Save full parsed markdown to workspace/out/
        out_path = _WORKSPACE_OUT / f"{file_path.stem}_parsed.md"
        saved_to = None
        try:
            _WORKSPACE_OUT.mkdir(parents=True, exist_ok=True)
            out_path.write_text(text if not truncated else text, encoding="utf-8")
            saved_to = out_path
            logger.info("doc_parser: saved parsed output to %s", out_path)
        except Exception as e:
            logger.debug("doc_parser: could not save to workspace/out: %s", e)

        header = f"# {file_path.name}\n"
        if n_ingested:
            header += f"*({n_ingested} chunks ingested into memory — searchable via z: memory)*\n"
        if saved_to:
            header += f"*Full parsed text saved to workspace/out/{saved_to.name}*\n"
        header += "\n"

        footer = ""
        if truncated:
            footer = f"\n\n*… truncated at ~{max_tokens} tokens. Full document saved to workspace/out/{out_path.name}*"

        return header + text + footer
