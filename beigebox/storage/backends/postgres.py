"""
PostgresBackend — PostgreSQL + pgvector implementation of VectorBackend.

Uses pgvector extension for vector similarity search with HNSW indexing.
Auto-creates tables and indexes on first connection (dummy-proof).

Thread safety: psycopg2 connection objects are thread-safe by default.
Each async task gets a fresh connection from the pool.
"""

import json
import logging
from pathlib import Path

try:
    import psycopg2
    from psycopg2.pool import SimpleConnectionPool
    from psycopg2.extras import execute_batch
except ImportError as _pg_err:
    raise ImportError(
        "psycopg2 is required for Postgres vector storage but is not installed. "
        "Install it with: pip install psycopg2-binary pgvector"
    ) from _pg_err

try:
    from pgvector.psycopg2 import register_vector
except ImportError as _pgv_err:
    raise ImportError(
        "pgvector is required for vector storage but is not installed. "
        "Install it with: pip install pgvector"
    ) from _pgv_err

from .base import VectorBackend
from beigebox.security.rag_poisoning_detector import RAGPoisoningDetector

logger = logging.getLogger(__name__)


class PostgresBackend(VectorBackend):
    """PostgreSQL + pgvector backend for vector storage (thread-safe, auto-setup)."""

    def __init__(
        self,
        connection_string: str,
        rag_detector: RAGPoisoningDetector | None = None,
        detection_mode: str = "warn",
        pool_size: int = 5,
    ):
        """
        Initialize PostgresBackend with auto-schema creation.

        Args:
            connection_string: PostgreSQL connection string (psycopg2 format)
                Examples:
                  - "postgresql://localhost/beigebox"
                  - "postgresql://user:pass@localhost:5432/beigebox"
                  - From env: $DATABASE_URL
            rag_detector: RAGPoisoningDetector instance (created if None)
            detection_mode: "warn", "quarantine", or "strict"
            pool_size: Connection pool size (default 5 for async workloads)

        Raises:
            psycopg2.Error: If connection fails or schema creation fails
        """
        self.connection_string = connection_string
        self._detector = rag_detector or RAGPoisoningDetector()
        self._detection_mode = detection_mode
        self._quarantine_count = 0

        # Create connection pool
        try:
            self._pool = SimpleConnectionPool(1, pool_size, connection_string)
            logger.info(
                "PostgresBackend initialized (pool_size=%d, "
                "rag_detection=%s, mode=%s)",
                pool_size,
                self._detector is not None,
                self._detection_mode,
            )
        except psycopg2.Error as e:
            raise psycopg2.Error(
                f"Failed to connect to PostgreSQL at {connection_string}. "
                f"Ensure Postgres is running and connection string is correct. "
                f"Error: {e}"
            ) from e

        # Auto-create schema on first connection
        self._ensure_schema()

    def _get_connection(self):
        """Get a connection from the pool."""
        try:
            return self._pool.getconn()
        except psycopg2.Error as e:
            logger.error("Failed to get connection from pool: %s", e)
            raise

    def _return_connection(self, conn):
        """Return a connection to the pool."""
        self._pool.putconn(conn)

    def _ensure_schema(self):
        """Create pgvector extension and tables if they don't exist (idempotent)."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()

            # Enable pgvector extension
            try:
                cursor.execute("CREATE EXTENSION IF NOT EXISTS vector")
            except psycopg2.Error as e:
                logger.warning("Failed to create pgvector extension: %s", e)
                raise psycopg2.Error(
                    "pgvector extension is required but could not be created. "
                    "Ensure you have superuser permissions or the extension is installed. "
                    "Install with: sudo -u postgres psql -c 'CREATE EXTENSION IF NOT EXISTS vector'"
                ) from e

            # Register pgvector type with psycopg2
            register_vector(conn)

            # Create embeddings table
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS embeddings (
                    id TEXT PRIMARY KEY,
                    embedding vector(1536),
                    document TEXT,
                    metadata JSONB,
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                )
            """
            )

            # Create HNSW index for fast similarity search
            # Lists = 100 is a good balance for most workloads
            # (tune based on your dataset size: 10-1000)
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS embeddings_hnsw_idx
                ON embeddings USING ivfflat (embedding vector_cosine_ops)
                WITH (lists = 100)
            """
            )

            # Create metadata index for filtering
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS embeddings_metadata_idx
                ON embeddings USING GIN (metadata)
            """
            )

            conn.commit()
            logger.info("PostgresBackend schema initialized successfully")

        except psycopg2.Error as e:
            conn.rollback()
            logger.error("Schema creation failed: %s", e)
            raise
        finally:
            cursor.close()
            self._return_connection(conn)

    # ------------------------------------------------------------------
    # VectorBackend interface
    # ------------------------------------------------------------------

    def upsert(
        self,
        ids: list[str],
        embeddings: list[list[float]],
        documents: list[str],
        metadatas: list[dict],
    ) -> None:
        """
        Upsert vectors with optional RAG poisoning detection.

        If a vector is flagged as suspicious:
        - warn mode: log and store anyway
        - quarantine mode: skip storage (log warning)
        - strict mode: raise error
        """
        safe_ids = []
        safe_embeddings = []
        safe_documents = []
        safe_metadatas = []

        # Check for poisoning before storing
        for vid, emb, doc, meta in zip(ids, embeddings, documents, metadatas):
            is_poisoned, confidence, reason = self._detector.is_poisoned(emb)

            if is_poisoned:
                msg = (
                    f"RAG poisoning detected in embedding {vid}: {reason} "
                    f"(confidence={confidence:.2f})"
                )
                logger.warning(msg)

                if self._detection_mode == "warn":
                    safe_ids.append(vid)
                    safe_embeddings.append(emb)
                    safe_documents.append(doc)
                    safe_metadatas.append(meta)
                elif self._detection_mode == "quarantine":
                    self._quarantine_count += 1
                    continue
                elif self._detection_mode == "strict":
                    raise ValueError(msg)
            else:
                safe_ids.append(vid)
                safe_embeddings.append(emb)
                safe_documents.append(doc)
                safe_metadatas.append(meta)
                # Update baseline with safe embeddings
                self._detector.update_baseline(emb)

        if not safe_ids:
            return

        # Bulk upsert to Postgres
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            register_vector(conn)

            # Use ON CONFLICT to handle updates
            execute_batch(
                cursor,
                """
                INSERT INTO embeddings (id, embedding, document, metadata, updated_at)
                VALUES (%s, %s, %s, %s, NOW())
                ON CONFLICT (id) DO UPDATE SET
                    embedding = EXCLUDED.embedding,
                    document = EXCLUDED.document,
                    metadata = EXCLUDED.metadata,
                    updated_at = NOW()
            """,
                [
                    (vid, emb, doc, json.dumps(meta) if isinstance(meta, dict) else meta)
                    for vid, emb, doc, meta in zip(
                        safe_ids, safe_embeddings, safe_documents, safe_metadatas
                    )
                ],
                page_size=100,
            )

            conn.commit()
        except psycopg2.Error as e:
            conn.rollback()
            logger.error("Upsert failed: %s", e)
            raise
        finally:
            cursor.close()
            self._return_connection(conn)

    def query(
        self,
        embedding: list[float],
        n_results: int,
        where: dict | None = None,
    ) -> dict:
        """
        Nearest-neighbour similarity search using pgvector.

        Args:
            embedding: Query vector (should be same dimension as stored embeddings)
            n_results: Number of results to return
            where: Optional JSONB filter (e.g., {"source_type": "conversation"})

        Returns:
            Dict matching ChromaDB collection.query() format:
            {
                "ids": [[id1, id2, ...]],
                "documents": [[doc1, doc2, ...]],
                "metadatas": [[meta1, meta2, ...]],
                "distances": [[dist1, dist2, ...]]
            }
        """
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            register_vector(conn)

            # Build WHERE clause if metadata filter provided
            where_clause = ""
            params = [embedding, n_results]
            if where:
                where_clause = "WHERE metadata @> %s"
                params.insert(1, json.dumps(where))

            # Query using cosine distance (<-> operator)
            # Cast embedding list to vector type using pgvector format
            cursor.execute(
                f"""
                SELECT id, document, metadata, embedding <-> %s::vector AS distance
                FROM embeddings
                {where_clause}
                ORDER BY distance
                LIMIT %s
            """,
                params,
            )

            rows = cursor.fetchall()

            return {
                "ids": [[row[0] for row in rows]],
                "documents": [[row[1] for row in rows]],
                "metadatas": [[json.loads(row[2]) if isinstance(row[2], str) else row[2] for row in rows]],
                "distances": [[row[3] for row in rows]],
            }

        except psycopg2.Error as e:
            logger.error("Query failed: %s", e)
            raise
        finally:
            cursor.close()
            self._return_connection(conn)

    def count(self) -> int:
        """Return total number of stored vectors."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM embeddings")
            count = cursor.fetchone()[0]
            return count
        except psycopg2.Error as e:
            logger.error("Count query failed: %s", e)
            raise
        finally:
            cursor.close()
            self._return_connection(conn)

    def get_detector_stats(self) -> dict:
        """Get RAG poisoning detector statistics (for monitoring/debugging)."""
        return {
            "detector": self._detector.get_baseline_stats(),
            "quarantine_count": self._quarantine_count,
            "detection_mode": self._detection_mode,
        }

    def close(self):
        """Close all connections in the pool."""
        try:
            self._pool.closeall()
            logger.info("PostgresBackend connection pool closed")
        except psycopg2.Error as e:
            logger.error("Error closing connection pool: %s", e)
