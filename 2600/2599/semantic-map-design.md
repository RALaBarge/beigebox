# Semantic Conversation Map Design (v0.6.0)

## Overview

**Semantic Map** visualizes conversation topics as a graph, showing how topics relate and cluster together.

## Problem Statement

In long conversations, hard to understand:
- What topics did we discuss?
- How do they relate?
- Which topics form clusters?
- What's the conversation structure?

Semantic Map answers these visually.

## Design

### Data Source

Use existing embeddings from ChromaDB:
- Every message already has embedding
- Use cosine similarity to find relationships
- Cluster by similarity

### Graph Representation

```
Nodes: Topics (representative sentences from conversation)
Edges: Similarity > threshold (e.g., 0.5)
Clusters: Groups of highly similar topics

Visualization:
[Docker] ──(0.94)── [Kubernetes]
   │(0.87)           │(0.81)
[Containerization]   [Orchestration]
   │
[Microservices]

[Python Async] ──(0.87)── [Concurrency] ──(0.81)── [Goroutines]
```

### Algorithm

1. Extract topics from conversation
   - Use embeddings to find representative sentences
   - Cluster similar messages
   - Pick one per cluster as "topic"

2. Calculate similarity
   - Pairwise cosine similarity between topics
   - Keep edges with similarity > threshold

3. Detect clusters
   - Use community detection (Louvain or simpler)
   - Group highly connected nodes

4. Visualize
   - ASCII art for CLI
   - JSON for web/programmatic use
   - Optional: web UI with D3.js

## Implementation

### Code Structure

```python
class SemanticConversationMap:
    def __init__(self, conversation_id: str, vector_store):
        self.conv_id = conversation_id
        self.vs = vector_store
    
    def build_map(self) -> dict:
        # Extract topics
        topics = self._extract_topics()
        
        # Calculate edges
        edges = self._calculate_similarity()
        
        # Detect clusters
        clusters = self._detect_clusters(topics, edges)
        
        return {
            "topics": topics,
            "edges": edges,
            "clusters": clusters,
            "visualization": self._render_ascii(topics, edges, clusters)
        }
    
    def _extract_topics(self) -> list[dict]:
        # Get all messages
        # Cluster by embedding similarity
        # Return representative sentence per cluster
        pass
    
    def _calculate_similarity(self) -> list[dict]:
        # Pairwise similarity
        # Keep only > threshold
        pass
    
    def _detect_clusters(self, topics, edges) -> list:
        # Community detection
        # Return cluster assignments
        pass
    
    def _render_ascii(self, topics, edges, clusters) -> str:
        # ASCII visualization
        pass
```

## API Endpoints

```
GET /api/v1/conversation/{conv_id}/semantic-map

Response:
{
  "conversation_id": "conv-abc",
  "topics": [
    {
      "id": 0,
      "text": "What is Docker?",
      "cluster": 0,
      "embedding_id": "chroma-123"
    },
    ...
  ],
  "edges": [
    {"from": 0, "to": 1, "similarity": 0.94},
    ...
  ],
  "clusters": [
    {
      "id": 0,
      "name": "Containerization",
      "topics": [0, 1, 2],
      "size": 3,
      "cohesion": 0.89
    },
    ...
  ],
  "visualization": "ASCII art string"
}
```

## Configuration

```yaml
semantic_map:
  enabled: false
  similarity_threshold: 0.5
  max_topics: 50
```

## Usage

```bash
# CLI
beigebox semantic-map conv-abc123

# Web
GET /api/v1/conversation/conv-abc123/semantic-map

# Output:
#
# SEMANTIC CONVERSATION MAP: conv-abc123
#
# [Docker] ──(0.94)── [Kubernetes]
#    │                    │
#    └──[Containerization]─┘
#
# Clusters: 2
# Topics: 5
# Edges: 4
# Avg Similarity: 0.87
```

## Testing

- [ ] Extract topics from messages
- [ ] Calculate pairwise similarity
- [ ] Filter by threshold
- [ ] Detect clusters
- [ ] Render ASCII
- [ ] Export JSON
- [ ] Handle single-topic conversations
- [ ] Handle disconnected graphs

## Future Enhancements

- Interactive web visualization (D3.js, Cytoscape)
- Topic naming (LLM-generated names for clusters)
- Evolution over time (how map changes as conversation progresses)
- Comparison (compare topic maps across conversations)
- Anomaly detection (topics that don't fit anywhere)
