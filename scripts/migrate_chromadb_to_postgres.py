#!/usr/bin/env python3
"""
Migrate vector embeddings from ChromaDB to PostgreSQL + pgvector.

This is a one-time migration script for users with existing ChromaDB data.
If you're starting fresh, you don't need to run this.

Usage:
    python scripts/migrate_chromadb_to_postgres.py \
        --chroma-path ./data/chroma \
        --postgres-url postgresql://localhost/beigebox

The script will:
    1. Connect to both ChromaDB and PostgreSQL
    2. Extract all vectors from ChromaDB
    3. Insert them into PostgreSQL (with ON CONFLICT handling)
    4. Verify the counts match
    5. Report success/failure

Note: This does NOT delete your ChromaDB data. You can safely delete
./data/chroma manually after verifying the migration succeeded.
"""

import argparse
import json
import logging
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def migrate(chroma_path: str, postgres_url: str, batch_size: int = 100):
    """
    Migrate vectors from ChromaDB to PostgreSQL.

    Args:
        chroma_path: Path to ChromaDB persistent storage
        postgres_url: PostgreSQL connection string
        batch_size: Number of vectors to insert per batch
    """
    # Import ChromaDB
    try:
        import chromadb
    except ImportError:
        logger.error(
            "chromadb is required for migration but not installed. "
            "Install with: pip install chromadb"
        )
        sys.exit(1)

    # Import PostgreSQL
    try:
        import psycopg2
        from pgvector.psycopg2 import register_vector
    except ImportError:
        logger.error(
            "psycopg2 and pgvector are required but not installed. "
            "Install with: pip install psycopg2-binary pgvector"
        )
        sys.exit(1)

    logger.info("=" * 60)
    logger.info("ChromaDB → PostgreSQL Vector Migration")
    logger.info("=" * 60)

    # Step 1: Connect to ChromaDB
    logger.info(f"Connecting to ChromaDB at {chroma_path}...")
    try:
        chroma_client = chromadb.PersistentClient(path=chroma_path)
        collection = chroma_client.get_or_create_collection(name="conversations")
        chroma_count = collection.count()
        logger.info(f"  ✓ Found {chroma_count} vectors in ChromaDB")
    except Exception as e:
        logger.error(f"Failed to connect to ChromaDB: {e}")
        sys.exit(1)

    if chroma_count == 0:
        logger.info("  No vectors to migrate (ChromaDB is empty)")
        return

    # Step 2: Fetch all vectors from ChromaDB
    logger.info("Fetching vectors from ChromaDB...")
    try:
        results = collection.get(include=["embeddings", "documents", "metadatas"])
        ids = results["ids"]
        embeddings = results["embeddings"]
        documents = results["documents"]
        metadatas = results["metadatas"]
        logger.info(f"  ✓ Fetched {len(ids)} vectors")
    except Exception as e:
        logger.error(f"Failed to fetch vectors from ChromaDB: {e}")
        sys.exit(1)

    # Step 3: Connect to PostgreSQL
    logger.info(f"Connecting to PostgreSQL at {postgres_url}...")
    try:
        pg_conn = psycopg2.connect(postgres_url)
        register_vector(pg_conn)
        cursor = pg_conn.cursor()
        logger.info("  ✓ Connected to PostgreSQL")
    except psycopg2.Error as e:
        logger.error(f"Failed to connect to PostgreSQL: {e}")
        logger.error(
            "  Make sure Postgres is running and the connection string is correct."
        )
        sys.exit(1)

    # Step 4: Create schema if needed
    logger.info("Ensuring PostgreSQL schema...")
    try:
        cursor.execute("CREATE EXTENSION IF NOT EXISTS vector")
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
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS embeddings_hnsw_idx
            ON embeddings USING ivfflat (embedding vector_cosine_ops)
            WITH (lists = 100)
        """
        )
        pg_conn.commit()
        logger.info("  ✓ Schema ready")
    except psycopg2.Error as e:
        logger.error(f"Failed to create schema: {e}")
        pg_conn.close()
        sys.exit(1)

    # Step 5: Insert vectors in batches
    logger.info(f"Inserting {len(ids)} vectors into PostgreSQL (batch_size={batch_size})...")
    try:
        inserted = 0
        for i in range(0, len(ids), batch_size):
            batch_ids = ids[i : i + batch_size]
            batch_embeddings = embeddings[i : i + batch_size]
            batch_documents = documents[i : i + batch_size]
            batch_metadatas = metadatas[i : i + batch_size]

            for vid, emb, doc, meta in zip(
                batch_ids, batch_embeddings, batch_documents, batch_metadatas
            ):
                cursor.execute(
                    """
                    INSERT INTO embeddings (id, embedding, document, metadata)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        embedding = EXCLUDED.embedding,
                        document = EXCLUDED.document,
                        metadata = EXCLUDED.metadata,
                        updated_at = NOW()
                """,
                    (
                        vid,
                        emb,
                        doc,
                        json.dumps(meta) if isinstance(meta, dict) else meta,
                    ),
                )
                inserted += 1

            # Commit every batch
            pg_conn.commit()
            if (i + batch_size) % (batch_size * 10) == 0:
                logger.info(f"  ... {inserted}/{len(ids)} inserted")

        logger.info(f"  ✓ Inserted {inserted} vectors")
    except psycopg2.Error as e:
        logger.error(f"Failed to insert vectors: {e}")
        pg_conn.rollback()
        pg_conn.close()
        sys.exit(1)

    # Step 6: Verify counts
    logger.info("Verifying migration...")
    try:
        cursor.execute("SELECT COUNT(*) FROM embeddings")
        pg_count = cursor.fetchone()[0]
        if pg_count == len(ids):
            logger.info(f"  ✓ Count match: {pg_count} vectors in PostgreSQL")
        else:
            logger.warning(
                f"  ⚠ Count mismatch: ChromaDB={len(ids)}, PostgreSQL={pg_count}"
            )
    except psycopg2.Error as e:
        logger.error(f"Failed to verify count: {e}")
    finally:
        cursor.close()
        pg_conn.close()

    logger.info("=" * 60)
    logger.info("✓ Migration complete!")
    logger.info("=" * 60)
    logger.info("")
    logger.info("Next steps:")
    logger.info("  1. Update config.yaml: storage.vector_backend = 'postgres'")
    logger.info("  2. Test the application with the new backend")
    logger.info(f"  3. Delete old ChromaDB data (if confident): rm -rf {chroma_path}")
    logger.info("")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Migrate vectors from ChromaDB to PostgreSQL + pgvector"
    )
    parser.add_argument(
        "--chroma-path",
        default="./data/chroma",
        help="Path to ChromaDB persistent storage (default: ./data/chroma)",
    )
    parser.add_argument(
        "--postgres-url",
        default="postgresql://localhost/beigebox",
        help="PostgreSQL connection URL (default: postgresql://localhost/beigebox)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Number of vectors per batch (default: 100)",
    )

    args = parser.parse_args()

    try:
        migrate(args.chroma_path, args.postgres_url, args.batch_size)
    except KeyboardInterrupt:
        logger.info("\nMigration cancelled by user")
        sys.exit(0)
