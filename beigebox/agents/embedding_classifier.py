"""
Embedding Classifier — fast routing via cosine similarity.

Inspired by NadirClaw's approach: pre-compute centroid vectors for
"simple" and "complex" prompt categories, then classify new prompts
by measuring which centroid they're closest to in embedding space.

Key difference from NadirClaw: we use the nomic-embed-text model
already loaded in Ollama (for ChromaDB) instead of adding
sentence-transformers as a new dependency. This means:
  - No new ~80MB model download
  - No new Python dependency
  - Reuses the embedding model already pinned in memory
  - Slightly slower (~50ms vs ~10ms) due to HTTP round-trip to Ollama

The classifier ships pre-computed centroid vectors as .npy files.
Run `beigebox build-centroids` to regenerate them from seed prompts.

The hybrid router (in proxy.py) uses this as the fast path:
  - Embedding classification: ~50ms, handles 80% of requests
  - Decision LLM: ~500ms-2s, only called for borderline cases
"""

import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np
import httpx

from beigebox.config import get_config

logger = logging.getLogger(__name__)

_PKG_DIR = os.path.dirname(__file__)
_CENTROID_DIR = os.path.join(_PKG_DIR, "centroids")


@dataclass
class EmbeddingDecision:
    """Result of embedding-based classification."""
    tier: str = "default"          # "simple" or "complex"
    confidence: float = 0.0        # Distance between centroid similarities
    model: str = ""                # Resolved model string
    latency_ms: int = 0            # Classification time
    borderline: bool = False       # True if confidence below threshold


# ---------------------------------------------------------------------------
# Seed prototypes — used to generate centroids via `beigebox build-centroids`
# ---------------------------------------------------------------------------

SIMPLE_PROTOTYPES = [
    "What is the capital of France?",
    "Who wrote Romeo and Juliet?",
    "What year did World War II end?",
    "What is 25 times 4?",
    "Define photosynthesis",
    "Translate 'thank you' to Spanish",
    "Is Python a compiled language?",
    "List the days of the week",
    "Hello, how are you?",
    "Tell me a joke",
    "What is the boiling point of water?",
    "What timezone is New York in?",
    "Give me a synonym for 'happy'",
    "What comes after Tuesday?",
    "Read the file config.yaml",
    "Show me the contents of README.md",
    "Run npm install",
    "Check the git status",
    "What does this function do?",
    "How do I create a new branch in git?",
    "Change the port from 3000 to 8080",
    "Fix this typo: 'recieve' should be 'receive'",
    "What version of Python is installed?",
    "How much disk space is available?",
    "What's my IP address?",
    "Convert 5 kilometers to miles",
    "How many sides does a hexagon have?",
    "What is 10% of 250?",
    "Sort these numbers: 5, 2, 8, 1, 9",
    "Reverse the string 'hello'",
    "What's the weather like?",
    "How do I make a cup of tea?",
    "What color is the sky?",
    "Name three primary colors",
    "What is the speed of light?",
    "Who painted the Mona Lisa?",
    "What is the largest planet?",
    "How many continents are there?",
    "What does GDP stand for?",
    "Is 7 a prime number?",
]

COMPLEX_PROTOTYPES = [
    "Design a microservices architecture for a real-time multiplayer game",
    "Architect a distributed event-sourcing system for financial trading",
    "Implement a thread-safe LRU cache in Python with TTL support",
    "Write a complete REST API with authentication and rate limiting",
    "Debug this memory leak in a Node.js WebSocket application",
    "Optimize this SQL query on a table with 50 million rows",
    "Compare transformer architectures GPT-4 vs Claude vs Gemini",
    "Prove that the halting problem is undecidable",
    "Derive the backpropagation algorithm from first principles",
    "Refactor this 2000-line class into domain-driven design",
    "Design a zero-trust security architecture for multi-cloud",
    "Create a CI/CD pipeline with canary releases and rollback",
    "Analyze the trade-offs between consistency and availability",
    "Write a short story exploring AI consciousness and philosophy",
    "Build a comprehensive monitoring system with incident automation",
    "Perform a security audit identifying OWASP Top 10 risks",
    "Design a disaster recovery plan with 15 minute RPO",
    "Compare container orchestration Kubernetes vs Nomad vs ECS",
    "Investigate a race condition in concurrent Go code",
    "Migrate this Express.js app to TypeScript with full type safety",
    "Design a database schema for a social media platform with caching",
    "Explain quantum error correction implications for practical QC",
    "Analyze garbage collection for a latency-sensitive trading system",
    "Create a data strategy for healthcare AI addressing HIPAA",
    "Build a React component library with theming and accessibility",
    "Profile and optimize a Python pipeline processing 10GB CSVs",
    "Implement a B-tree with insert delete search and rebalancing",
    "Design a scalable notification system for push email SMS channels",
    "Review this distributed transaction for race conditions",
    "Evaluate migrating from REST to GraphQL with migration plan",
]

CODE_PROTOTYPES = [
    "Write a Python function to parse JSON",
    "Debug this segfault in my C++ code",
    "Implement a REST API endpoint in FastAPI",
    "How do I use async/await in JavaScript?",
    "Write a SQL query to find duplicate rows",
    "Refactor this class to use dependency injection",
    "What's the difference between a list and a tuple?",
    "Write a bash script to backup a directory",
    "How do I set up a virtual environment in Python?",
    "Explain how Git rebase works",
    "Write unit tests for this function",
    "How do I handle exceptions in Python?",
    "What is a Docker volume?",
    "Write a regex to validate an email address",
    "How do I connect to a PostgreSQL database in Python?",
]

CREATIVE_PROTOTYPES = [
    "Write a short story about a lonely robot",
    "Compose a poem about autumn rain",
    "Help me brainstorm names for my startup",
    "Write a product description for noise-canceling headphones",
    "Create a metaphor for explaining machine learning",
    "Write a haiku about debugging code",
    "Help me write a cover letter",
    "Suggest some themes for a sci-fi novel",
    "Write dialogue between two strangers on a train",
    "Create a tagline for a coffee shop",
    "Help me write a toast for a wedding",
    "Describe a sunset in the style of Hemingway",
    "Write a children's story about a brave mouse",
    "Create five names for a fantasy kingdom",
    "Help me write a LinkedIn post about my promotion",
]

class EmbeddingClassifier:
    """
    Fast binary prompt classifier using embedding cosine similarity.

    Uses the nomic-embed-text model already loaded in Ollama to embed
    prompts and compare against pre-computed simple/complex centroids.
    """

    def __init__(self):
        cfg = get_config()
        self.embed_model = cfg.get("embedding", {}).get("model", "nomic-embed-text")
        self.embed_url = cfg.get("embedding", {}).get(
            "backend_url", cfg["backend"]["url"]
        ).rstrip("/")

        # Load routes for model resolution
        d_cfg = cfg.get("decision_llm", {})
        self.routes = d_cfg.get("routes", {})
        self.default_model = cfg["backend"].get("default_model", "")

        # Classification threshold
        self.threshold = cfg.get("embedding_classifier", {}).get("threshold", 0.04)

        # Load centroids
        self._simple_centroid: Optional[np.ndarray] = None
        self._complex_centroid: Optional[np.ndarray] = None
        self._load_centroids()

def _load_centroids(self):
    """Load all available centroid vectors."""
    # Multi-class centroids
    self._centroids: dict[str, np.ndarray] = {}
    for npy_file in Path(_CENTROID_DIR).glob("*_centroid.npy"):
        route_name = npy_file.stem.replace("_centroid", "")
        self._centroids[route_name] = np.load(str(npy_file))
        logger.info("Loaded centroid: %s", route_name)

    # Backward compat aliases
    self._simple_centroid = self._centroids.get("simple")
    self._complex_centroid = self._centroids.get("complex")

    if not self._centroids:
        logger.warning(
            "No centroid files found in %s. "
            "Run 'beigebox build-centroids' to generate them.",
            _CENTROID_DIR,
        )

    @property
    def ready(self) -> bool:
        return bool(self._centroids)

    def _embed(self, text: str) -> Optional[np.ndarray]:
        """Get embedding vector from Ollama."""
        try:
            resp = httpx.post(
                f"{self.embed_url}/api/embed",
                json={"model": self.embed_model, "input": text},
                timeout=5.0,
            )
            resp.raise_for_status()
            data = resp.json()
            embeddings = data.get("embeddings", [[]])
            if embeddings and embeddings[0]:
                vec = np.array(embeddings[0], dtype=np.float32)
                # Normalize
                norm = np.linalg.norm(vec)
                if norm > 0:
                    vec = vec / norm
                return vec
        except Exception as e:
            logger.debug("Embedding failed: %s", e)
        return None

    def _embed_batch(self, texts: list[str]) -> list[np.ndarray]:
        """Get embeddings for multiple texts."""
        try:
            resp = httpx.post(
                f"{self.embed_url}/api/embed",
                json={"model": self.embed_model, "input": texts},
                timeout=30.0,
            )
            resp.raise_for_status()
            data = resp.json()
            embeddings = data.get("embeddings", [])
            results = []
            for emb in embeddings:
                vec = np.array(emb, dtype=np.float32)
                norm = np.linalg.norm(vec)
                if norm > 0:
                    vec = vec / norm
                results.append(vec)
            return results
        except Exception as e:
            logger.error("Batch embedding failed: %s", e)
            return []

    def _resolve_model(self, route_name: str) -> str:
        """Resolve any route name to a model string via config routes."""
        if route_name in self.routes:
            return self.routes[route_name].get("model", self.default_model)
        # Fallbacks for binary tiers
        if route_name == "simple":
            return self.routes.get("fast", {}).get("model", self.default_model)
        if route_name == "complex":
            return self.routes.get("large", {}).get("model", self.default_model)
        return self.default_model

    def classify(self, prompt: str) -> EmbeddingDecision:
        if not self.ready:
            return EmbeddingDecision(tier="default", borderline=True)

        start = time.monotonic()
        emb = self._embed(prompt)
        if emb is None:
            return EmbeddingDecision(tier="default", borderline=True)

        # Score against all available centroids
        scores = {
            name: float(np.dot(emb, centroid))
            for name, centroid in self._centroids.items()
        }

        best_route = max(scores, key=scores.get)
        best_score = scores[best_route]
        scores_sorted = sorted(scores.values(), reverse=True)
        confidence = scores_sorted[0] - scores_sorted[1] if len(scores_sorted) > 1 else 1.0

        latency_ms = int((time.monotonic() - start) * 1000)
        borderline = confidence < self.threshold

        # Map route name to tier for backward compat
        tier = best_route if best_route in ("simple", "complex") else "complex"
        model = self._resolve_model(best_route)

        logger.debug(
            "Embedding classify: best=%s confidence=%.4f borderline=%s scores=%s (%dms)",
            best_route, confidence, borderline, 
            {k: f"{v:.3f}" for k, v in scores.items()}, latency_ms,
        )

        return EmbeddingDecision(
            tier=tier,
            confidence=confidence,
            model=model,
            latency_ms=latency_ms,
            borderline=borderline,
        )

def build_centroids(self) -> bool:
    """
    Generate centroid vectors from seed prototypes for all routes.
    Saves one .npy file per route to the centroids directory.
    """
    prototype_sets = {
        "simple":   SIMPLE_PROTOTYPES,
        "complex":  COMPLEX_PROTOTYPES,
        "code":     CODE_PROTOTYPES,
        "creative": CREATIVE_PROTOTYPES,
    }

    logger.info("Building centroids for routes: %s", list(prototype_sets.keys()))

    self._centroids = {}
    for route_name, prototypes in prototype_sets.items():
        embs = self._embed_batch(prototypes)
        if not embs:
            logger.error("Failed to embed prototypes for route '%s'", route_name)
            return False

        centroid = np.mean(embs, axis=0).astype(np.float32)
        centroid = centroid / np.linalg.norm(centroid)

        path = os.path.join(_CENTROID_DIR, f"{route_name}_centroid.npy")
        os.makedirs(_CENTROID_DIR, exist_ok=True)
        np.save(path, centroid)
        logger.info("Centroid saved: %s (dim=%d)", path, len(centroid))

    # Keep backward-compat binary centroids
    self._simple_centroid = self._centroids.get("simple") or \
        np.load(os.path.join(_CENTROID_DIR, "simple_centroid.npy"))
    self._complex_centroid = self._centroids.get("complex") or \
        np.load(os.path.join(_CENTROID_DIR, "complex_centroid.npy"))

    return True

# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------
_singleton: Optional[EmbeddingClassifier] = None


def get_embedding_classifier() -> EmbeddingClassifier:
    """Return singleton embedding classifier."""
    global _singleton
    if _singleton is None:
        _singleton = EmbeddingClassifier()
    return _singleton
