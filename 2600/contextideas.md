You can modify an LLM’s “context” at many layers: system prompts, message selection/summarization, external memory, tools, and even retrieval and safety filters.  Most of the obvious patterns have been explored, but there is still room for novel combinations and more adaptive, agentic schemes.[1][2][3]

## What “context” means in practice

When people talk about context in a conversation with an LLM, they’re usually touching several separable things:  

- The **immediate context window**: the actual tokens the model sees this turn (system + dev + user + assistant messages, tool results).[4][1]
- The **conversation state** managed by the app: which past turns you include, how you truncate or summarize, and any persistent “profile” for the user.[2][3]
- **External memory / knowledge**: vector stores, databases, documents you retrieve and inject.[3][2]
- **Hidden scaffolding**: routing logic, tools, guards, or summaries that never appear in the UI but shape prompts heavily.[5][3]

Modifying context means intervening in any of these before each model call.

## Established techniques for modifying context

These are widely used or at least well‑documented patterns:

- **Truncation and rolling windows**  
  - Keep only the N most recent messages or a rolling window of tokens; drop or middle‑truncate long tool outputs or documents.[6][3]
  - Often combined with token accounting and automatic compaction once a percentage of the window is used.[3]

- **Summarization‑based history**  
  - Periodically replace older turns with natural‑language summaries, sometimes in multiple layers (hierarchical summarization).[2][6]
  - Recent research compares LLM summarization vs “observation masking” and similar tricks for keeping recent reasoning intact while compressing past details.[2]

- **Observation masking / placeholder tokens**  
  - Keep the *structure* of the past (steps, actions) but replace older content with placeholders like “details omitted for brevity,” so the model knows there *was* context but cannot see it in full.[2]
  - This changes how the model attends to past steps without exceeding limits.[2]

- **Instruction injection and system message updates**  
  - Dynamically rewrite the system or developer instruction at topic changes (“The user is now asking about cooking; ignore prior task”).[1]
  - More sophisticated orchestrators maintain a long‑term “profile summary” of user goals and agent behavior and inject that as system‑level context each session.[7]

- **Routing and tool‑driven context changes**  
  - Route some requests to larger‑context models, or to specialized models with different system prompts.[5][6]
  - Use tool calls to retrieve fresh context (RAG) instead of carrying long histories directly; the retrieved snippets effectively replace older context.[3][5]

- **External memories and long‑term profiles**  
  - Store user goals, preferences, and key facts outside the LLM and re‑inject them on demand as condensed “memory.”[7][2]
  - Commercial agents now persist these summaries across sessions and automatically merge them into orchestration prompts.[7]

- **Context‑aware safety and filtering**  
  - Inject safety and policy reminders contextually (e.g., when sensitive topics appear) rather than as a static preamble.[8][5]
  - Security research also shows “embedded prompts” in documents/web pages manipulating long‑term memory, which is a kind of adversarial context modification.[8][7]

- **Context reset and topic bucketing**  
  - Detect topic switches and start a fresh context window, optionally carrying only a high‑level user profile (“same user, new task”).[9][1]

## Less common but explored or emerging ideas

These are not as ubiquitous in basic chat UIs but are being actively experimented with in agents and research:

- **Structured, multi‑buffer context**  
  - Separate buffers for: current task, long‑term user profile, tools state, and meta‑reasoning, each with its own compaction rules.[3][2]
  - For example, never drop the profile, aggressively summarize tool logs, and keep raw recent dialog intact.

- **Context‑rot‑aware scheduling**  
  - “Context rot” describes how adding more tokens can degrade output even if they’re relevant, due to how attention scales.[4]
  - Some propose choosing *fewer* but more salient past messages (via learned salience scoring) instead of “everything recent.”[4][2]

- **Adversarial / security‑aware context control**  
  - Studies of prompt injection and “memory poisoning” show how an attacker can get instructions embedded into summaries or long‑term memory and thereby change future behavior.[10][8][7]
  - Defenses include tagging trust levels on content, segmenting memory by origin, and sanitizing before summarization.[8][7]

- **Environment‑linked context**  
  - Agents that work on codebases or documents keep only a thin conversational history and instead use tools like “grep” and “read_file” to re‑pull relevant context on demand.[3]
  - Here, context is modified by controlling what the tools are allowed to retrieve (limits on file size, search results, etc.).[3]

## What *hasn’t* really been tried (or is very early)

There are some directions that are under‑explored in production systems, even if they’re discussed conceptually:

- **Learned context policies (meta‑LLMs choosing context)**  
  - Today, most context management is rule‑based (truncate X tokens, summarize every Y turns).[6][2][3]
  - A more radical direction is having a separate model that *decides which exact sentences, fields, and memories to include* each turn, trained end‑to‑end for task success and cost. This is still relatively rare in real products compared to hand‑written heuristics.[5][4]

- **Fine‑grained attention “masks” at the API level**  
  - Current APIs don’t let you directly weight tokens differently, so people approximate this by summaries and masking text with “details omitted.”[4][2]
  - If APIs evolved to accept structured segments with priority weights or visibility flags (e.g., “these tokens are low‑priority context”), we’d likely see new patterns that aren’t feasible yet.

- **User‑controllable, explainable context views**  
  - Most systems hide how they’re editing context. Users rarely see which past turns or memories are active.[9][3]
  - There is room for UIs where users adjust “memory sliders” (e.g., emphasize technical details vs. personal preferences) or explicitly pin/unpin messages or memories that must/ must not affect future turns.

- **Multi‑agent “context markets”**  
  - Instead of one context pipeline, multiple specialized agents could propose context bundles (e.g., “safety bundle,” “user‑preference bundle,” “retrieval bundle”) and a coordinator chooses among them per turn.[5]
  - This is hinted at in agent frameworks but is far from a standard pattern in typical LLM chat apps.[5]

- **Strong provenance and compartmentalization**  
  - Tag every memory or context snippet with source, timestamp, trust level, and task, then let policies decide which tags are allowed into the current prompt.[7][8]
  - Some security research points this way for defense against poisoning, but mature, general‑purpose “provenance‑aware prompts” are not widely deployed.[8][7]

- **Systematic, user‑level “context therapy”**  
  - There is early community discussion around whether continuous context makes people feel “over‑remembered” or “watched,” and how much control they have.[9]
  - We’ve barely explored deliberate designs where the agent negotiates what to remember, explains why something is being summarized, and allows retroactive deletion of memories at a granular level.

## If you want to explore genuinely new ideas

If your goal is to do research or build something novel, promising directions include:

- Implement a **learned context selector** model that chooses context snippets and compare it against fixed heuristics across tasks and users.[4][5]
- Design an interface where users can **see and edit active context and long‑term memory**, including reasons why each item is included.[9]
- Experiment with **provenance‑ and trust‑tagged memories**, especially under adversarial settings (prompt injection, recommendation poisoning).[7][8]
- Build a **multi‑buffer, multi‑agent orchestrator** and measure how different context “bundles” affect performance, safety, and user satisfaction.[2][5][3]

If you share your specific domain (e.g., coding assistants, therapy, search, tutoring), I can suggest more concrete context‑modification schemes and where the open research gaps likely are.

Sources
[1] How do LLMs handle context switching in conversations? - Milvus https://milvus.io/ai-quick-reference/how-do-llms-handle-context-switching-in-conversations
[2] Cutting Through the Noise: Smarter Context Management for LLM ... https://blog.jetbrains.com/research/2025/12/efficient-context-management/
[3] Best practices for cost-efficient, high-quality context management in ... https://community.openai.com/t/best-practices-for-cost-efficient-high-quality-context-management-in-long-ai-chats/1373996
[4] Context rot: the emerging challenge that could hold back LLM ... https://www.understandingai.org/p/context-rot-the-emerging-challenge
[5] The State Of LLMs 2025: Progress, Problems, and Predictions https://magazine.sebastianraschka.com/p/state-of-llms-2025
[6] 6 Techniques You Should Know to Manage Context Lengths in LLM ... https://www.reddit.com/r/LLMDevs/comments/1mviv2a/6_techniques_you_should_know_to_manage_context/
[7] When AI Remembers Too Much – Persistent Behaviors in Agents ... https://unit42.paloaltonetworks.com/indirect-prompt-injection-poisons-ai-longterm-memory/
[8] Manipulating AI memory for profit: The rise of AI Recommendation ... https://www.microsoft.com/en-us/security/blog/2026/02/10/ai-recommendation-poisoning/
[9] Continuous context with the LLM - am I in control..? : r/therapyGPT https://www.reddit.com/r/therapyGPT/comments/1pizwjh/continuous_context_with_the_llm_am_i_in_control/
[10] A Real-World Case Study of Attacking ChatGPT via Lightweight ... https://arxiv.org/html/2504.16125v1
