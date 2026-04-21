"""
Trinity Code Chunker - Sliding window chunking with .gitignore respect.

Chunks source code into 4000-token windows with 500-token overlap.
Strictly respects .gitignore to avoid sending excluded files.
"""

import os
import re
from pathlib import Path
from typing import List, Dict, Tuple, Optional
import tiktoken

from .logger import TrinityLogger, TrinityLogConfig


class TrinityChunker:
    """Chunks repository code for Trinity audit."""

    def __init__(self, repo_path: str, chunk_size: int = 4000, overlap: int = 500,
                 logger: Optional[TrinityLogger] = None):
        self.repo_path = Path(repo_path)
        self.chunk_size = chunk_size
        self.overlap = overlap
        self.enc = tiktoken.get_encoding("cl100k_base")  # GPT tokenizer
        self.exclude_patterns = self._load_gitignore()
        self.all_chunks = []  # Track all chunks for audit log
        self.logger = logger if logger is not None else TrinityLogger("noop", TrinityLogConfig(enabled=False))

    def _load_gitignore(self) -> List[str]:
        """Load .gitignore patterns and return list of regex patterns to exclude."""
        gitignore_path = self.repo_path / ".gitignore"
        patterns = [
            r'\.git.*',
            r'.*\.pyc',
            r'__pycache__.*',
            r'\.venv.*',
            r'node_modules.*',
            r'dist.*',
            r'build.*',
        ]

        if gitignore_path.exists():
            with open(gitignore_path, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#'):
                        # Convert gitignore pattern to regex
                        regex = self._gitignore_to_regex(line)
                        patterns.append(regex)

        return patterns

    def _gitignore_to_regex(self, pattern: str) -> str:
        """Convert gitignore pattern to regex."""
        pattern = pattern.rstrip('/')
        pattern = pattern.replace('.', r'\.')
        pattern = pattern.replace('*', '.*')
        pattern = pattern.replace('?', '.')
        return f"({pattern}|{pattern}/.*)"

    def _should_exclude(self, file_path: Path) -> bool:
        """Check if file should be excluded based on .gitignore patterns."""
        rel_path = str(file_path.relative_to(self.repo_path))

        for pattern in self.exclude_patterns:
            if re.match(pattern, rel_path):
                return True

        return False

    def _get_files(self) -> List[Path]:
        """Get all files to audit, respecting .gitignore."""
        files = []

        for file_path in self.repo_path.rglob('*'):
            if file_path.is_file() and not self._should_exclude(file_path):
                # Only include text-like files
                try:
                    if self._is_text_file(file_path):
                        files.append(file_path)
                except:
                    pass

        return sorted(files)

    def _is_text_file(self, file_path: Path) -> bool:
        """Check if file is likely text (not binary)."""
        text_extensions = {
            '.py', '.go', '.rs', '.js', '.ts', '.java', '.c', '.cpp', '.h',
            '.md', '.txt', '.yaml', '.yml', '.json', '.xml', '.sql', '.sh',
            '.rb', '.php', '.swift', '.kt', '.scala', '.r', '.pl'
        }

        if file_path.suffix.lower() in text_extensions:
            return True

        # Check for common text files without extension
        if file_path.name in ['Makefile', 'Dockerfile', 'README', 'LICENSE', 'requirements.txt']:
            return True

        # Peek at first 512 bytes for null bytes (binary indicator)
        try:
            with open(file_path, 'rb') as f:
                chunk = f.read(512)
                return b'\x00' not in chunk
        except:
            return False

    def chunk_repository(self) -> Tuple[List[Dict], Dict[str, int]]:
        """
        Chunk all source files in repository.

        Returns:
            (chunks, metadata) where:
            - chunks: List of {file, line_start, line_end, token_count, content}
            - metadata: {total_files, total_chunks, total_tokens, excluded_files}
        """
        files = self._get_files()
        chunks = []
        total_tokens = 0

        for file_path in files:
            try:
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()

                file_chunks = self._chunk_file(file_path, content)
                chunks.extend(file_chunks)
                total_tokens += sum(c['token_count'] for c in file_chunks)

            except Exception as e:
                self.logger.warn("Could not read file — skipping",
                                 phase="chunker", file=str(file_path), exc_msg=str(e))
                continue

        self.all_chunks = chunks

        metadata = {
            "total_files": len(files),
            "total_chunks": len(chunks),
            "total_tokens": total_tokens,
            "chunk_size": self.chunk_size,
            "overlap": self.overlap,
        }

        return chunks, metadata

    def _chunk_file(self, file_path: Path, content: str) -> List[Dict]:
        """Chunk a single file using sliding window."""
        lines = content.split('\n')
        rel_path = str(file_path.relative_to(self.repo_path))
        chunks = []

        # Tokenize entire file
        tokens = self.enc.encode(content)

        # Sliding window over tokens
        start_token = 0
        chunk_num = 0

        while start_token < len(tokens):
            end_token = min(start_token + self.chunk_size, len(tokens))

            # Decode tokens back to text
            chunk_text = self.enc.decode(tokens[start_token:end_token])

            # Estimate line numbers via proportional interpolation — not precise.
            # Phase 4 source verification may report slightly wrong line numbers.
            chunk_lines = chunk_text.count('\n')
            line_start = int((start_token / len(tokens)) * len(lines)) if tokens else 0
            line_end = min(line_start + chunk_lines + 10, len(lines))
            self.logger.trace("line numbers are estimated (proportional interpolation)",
                              phase="chunker", file=rel_path, chunk=chunk_num,
                              line_start=line_start, line_end=line_end)

            chunk = {
                "file": rel_path,
                "chunk_id": f"{rel_path}:chunk_{chunk_num}",
                "line_start": line_start,
                "line_end": line_end,
                "token_count": end_token - start_token,
                "content": chunk_text,
            }

            chunks.append(chunk)
            chunk_num += 1

            # Slide window with overlap
            start_token += (self.chunk_size - self.overlap)

            # Stop if we've covered all tokens
            if end_token >= len(tokens):
                break

        return chunks
