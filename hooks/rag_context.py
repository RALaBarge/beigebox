"""
Example hook: RAG context injection.

When the decision LLM (or a keyword heuristic) determines a request
would benefit from past conversation context, this hook queries ChromaDB
and injects relevant history into the system prompt.

To use: copy to hooks/ directory and enable in config.yaml.
Requires the vector store to be initialized.
"""

# To enable this hook, uncomment the code below and configure:
# MAX_RAG_RESULTS = 3
# MAX_RAG_CHARS = 2000
#
# def pre_request(body: dict, context: dict) -> dict:
#     """Inject relevant conversation history into the request."""
#     decision = context.get("decision")
#     vector_store = context.get("vector_store")
#
#     # Only inject if decision LLM says we need RAG, or if disabled, skip
#     if not decision or not decision.needs_rag or not vector_store:
#         return body
#
#     user_message = context.get("user_message", "")
#     if not user_message:
#         return body
#
#     # Search for relevant past messages
#     results = vector_store.search(user_message, n_results=MAX_RAG_RESULTS)
#     if not results:
#         return body
#
#     # Build context block
#     context_lines = ["Relevant context from past conversations:"]
#     total_chars = 0
#     for hit in results:
#         score = 1 - hit["distance"]
#         if score < 0.3:  # Skip low-relevance results
#             continue
#         snippet = hit["content"][:500]
#         role = hit["metadata"].get("role", "?")
#         context_lines.append(f"[{role}] {snippet}")
#         total_chars += len(snippet)
#         if total_chars >= MAX_RAG_CHARS:
#             break
#
#     if len(context_lines) <= 1:
#         return body
#
#     rag_block = "\n".join(context_lines)
#
#     # Inject as system message
#     messages = body.get("messages", [])
#     if messages and messages[0].get("role") == "system":
#         messages[0]["content"] += f"\n\n{rag_block}"
#     else:
#         messages.insert(0, {"role": "system", "content": rag_block})
#     body["messages"] = messages
#
#     return body
