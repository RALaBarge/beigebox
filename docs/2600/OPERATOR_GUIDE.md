# BeigeBox Operator — Interactive Agent

## Overview

The **Operator** is a LangChain ReAct agent that gives you interactive access to BeigeBox's data stores, web search, and system commands. It's like having a local AI assistant that understands BeigeBox's internals.

Ask it questions about:
- **Conversations**: "Show me all discussions about Docker"
- **Routing decisions**: Query stats on model usage, routing errors
- **System state**: Disk usage, Ollama status, running processes
- **Web info**: Search for current documentation, news
- **Data analysis**: Run named SQL queries, semantic searches

## Usage

### CLI — Single Query

```bash
beigebox operator "What have we discussed about async programming?"
```

Output:
```
[agent reasoning and tool invocation]
Final Answer: You discussed async/await patterns in Python, 
event loops in Node.js, and Go's goroutines across 3 conversations...
```

### CLI — Interactive REPL

```bash
beigebox operator
```

You'll get an `op>` prompt where you can ask multiple questions:

```
op> Show me recent conversations
[agent runs conversation_search tool]

op> How many tokens used today?
[agent runs database_query tool]

op> What's the CPU usage?
[agent runs shell tool with allowlisted commands]

op> exit
[disconnected]
```

### TUI — Integrated Panel

Launch the full TUI console:

```bash
beigebox jack
```

Switch to the **Operator** tab (press `3`) and type questions directly. Results stream in real-time with reasoning and tool invocations.

## Available Tools

### 1. Web Search

Search the internet using DuckDuckGo (no API key needed).

```
"What's the latest on LLM safety?"
→ [web_search tool finds articles]
→ [format results with links and snippets]
```

### 2. Web Scraper

Fetch and extract text content from URLs.

```
"Get the content from https://ollama.ai/docs"
→ [web_scrape tool extracts page content]
→ [return clean text for LLM analysis]
```

### 3. Conversation Search

Semantic search over your stored conversations using ChromaDB embeddings.

```
"What did we talk about regarding authentication?"
→ [conversation_search tool finds semantically similar messages]
→ [return top 5 matches with scores and context]
```

### 4. Database Queries

Run named SQL query templates against the conversation database.

```
"How many conversations happened today?"
→ [database_query tool runs: SELECT COUNT(*) FROM conversations WHERE date(...)]
→ [return formatted results]
```

Available queries (from config):
- `recent_conversations` — last 10
- `token_usage_by_model` — tokens per model
- `todays_conversations` — today's chats
- `search_messages` — search by keyword

### 5. Shell Commands

Execute allowlisted system commands (with safety guards).

```
"Check Ollama status"
→ [shell tool runs: ollama list]
→ [return running models and memory usage]
```

**Safety**:
- Only pre-allowlisted commands execute
- Blocked patterns prevent dangerous operations
- 15-second timeout per command
- No `shell=True` (safer subprocess)

Configured in `config.yaml`:

```yaml
operator:
  shell:
    enabled: true
    allowed_commands:
      - ls
      - cat
      - grep
      - ps
      - df
      - free
      - ollama
      - beigebox
    blocked_patterns:
      - "rm -rf"
      - "sudo"
      - "> /etc"
```

## Configuration

In `config.yaml`:

```yaml
operator:
  model: "llama3.2:2b"              # Small, fast model for reasoning
  max_iterations: 10                # Max tool invocations per query
  
  shell:
    enabled: true                   # Enable shell commands
    allowed_commands:               # Only these can run
      - ls
      - cat
      - grep
      - ps
      - df
      - free
      - ollama
      - beigebox
    blocked_patterns:               # Reject if command contains these
      - "rm -rf"
      - "sudo"
      - "> /etc"
  
  data:
    sqlite_queries:                 # Named query templates
      recent_conversations: >
        SELECT c.id, c.created_at, COUNT(m.id) as msg_count
        FROM conversations c JOIN messages m ON m.conversation_id = c.id
        GROUP BY c.id ORDER BY c.created_at DESC LIMIT 10
      token_usage_by_model: >
        SELECT model, SUM(token_count) as tokens, COUNT(*) as messages
        FROM messages GROUP BY model ORDER BY tokens DESC
      search_messages: >
        SELECT role, content, model, timestamp FROM messages
        WHERE content LIKE ? ORDER BY timestamp DESC LIMIT 20
      todays_conversations: >
        SELECT c.id, c.created_at, COUNT(m.id) as msg_count
        FROM conversations c JOIN messages m ON m.conversation_id = c.id
        WHERE date(c.created_at) = date('now')
        GROUP BY c.id ORDER BY c.created_at DESC
```

## Example Queries

### Understanding Your Data

```
"How much have I been using the code model?"
→ Runs token_usage_by_model query
→ Shows token count and message count per model

"Show me all conversations about deployment"
→ Semantic search for "deployment"
→ Returns matching conversations with similarity scores

"What's the most active hour of the day?"
→ Could run shell + custom SQL to analyze timestamp patterns
→ Shows trends in your usage
```

### System Health

```
"Is Ollama still running?"
→ Shell: ollama list
→ Shows loaded models and RAM usage

"How much disk space do I have left?"
→ Shell: df -h
→ Shows partition usage

"What processes are using the most CPU?"
→ Shell: ps aux --sort=-%cpu
→ Top processes by CPU usage
```

### Debugging & Analysis

```
"Did we test that feature?"
→ Conversation search
→ Returns relevant discussions

"Show me the longest conversation"
→ Database query: todays_conversations sorted by msg_count
→ Identify heavy-lift chats

"What topics came up most?"
→ Semantic search on aggregated keywords
→ Topic frequency analysis
```

## How It Works

1. **You ask a question** (CLI or TUI)
2. **Agent reads the question** and thinks about which tool(s) to use
3. **Agent invokes tools** in sequence:
   - Parse tool name and input
   - Execute tool (search, query, shell, etc.)
   - Get result back
4. **Agent reasons over results** and refines
5. **Agent returns final answer**

The agent uses ReAct (Reasoning + Acting) — it's transparent about its reasoning:

```
Question: What models am I using most?
Thought: I need to query token usage by model
Action: database_query
Action Input: token_usage_by_model
Observation: [table with results]
Thought: I now have the data
Final Answer: Based on token usage, you favor [model] with X tokens...
```

## Limitations & Safety

**What it can't do**:
- Modify conversations (read-only access)
- Delete data
- Execute unsafe commands
- Make external API calls (except DuckDuckGo)

**Safety features**:
- Shell allowlist (only pre-approved commands)
- Blocked pattern matching (prevents `rm -rf`, `sudo`, etc.)
- Command timeout (15 seconds max)
- No shell=True (safer subprocess)
- Named SQL queries only (no raw SQL injection)

## Extending the Operator

### Add a New Named Query

Edit `config.yaml` and add to `operator.data.sqlite_queries`:

```yaml
my_custom_query: >
  SELECT * FROM messages WHERE model = ? ORDER BY timestamp DESC LIMIT 5
```

Then ask the agent:

```
"Run my_custom_query for llama3.2"
```

### Add an Allowlisted Command

Edit `config.yaml` and add to `operator.shell.allowed_commands`:

```yaml
allowed_commands:
  - docker          # Can now run: docker ps, docker logs, etc.
  - nvidia-smi      # Can check GPU usage
```

### Custom Tools (Advanced)

You can extend the Operator by adding new LangChain Tool objects in the `_build_tools` method of `operator.py`. Follow the pattern:

```python
tools.append(Tool(
    name="my_tool",
    func=my_function,
    description="What this tool does"
))
```

## TUI Keyboard Shortcuts

When in the Operator screen (`beigebox jack`):

| Key | Action |
|-----|--------|
| `3` | Switch to Operator tab |
| `Enter` | Send query |
| `r` | Refresh screen |
| `q` | Quit |

## Recommended Model

The operator uses a configured model from `config.yaml`. Recommended:
- **llama3.2:2b** — Fast, good reasoning, low VRAM (~2GB)
- **qwen2:0.5b** — Tiny, even faster
- **phi2** — Surprising quality for size

Avoid large models (slow, overkill for this task).

---

**Status**: ✓ Ready to use (CLI and TUI both implemented)  
**Safety**: ✓ Allowlisted commands, no dangerous defaults  
**Testing**: ✓ Works with existing BeigeBox data  
**Extensibility**: ✓ Add queries and commands via config
