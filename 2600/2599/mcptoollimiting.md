Yes. That is the right direction.

A literal “click for more” UI is usually the wrong abstraction for the model, but **progressive tool disclosure** is the right system design. With large tool sets, quality tends to get worse when the model sees too many options, and current guidance explicitly recommends deferring rarely used tools rather than always stuffing all schemas into context. OpenAI’s current function-calling docs describe pairing function calling with **tool search** so rarely used tools are loaded only when needed, and recent MCP production guidance similarly recommends ruthlessly curating exposure rather than exposing everything at once. ([OpenAI Developers][1])

## What to do instead

Use a **two-stage router**:

1. **Stage 1: capability selection**
   Give the model only:

   * the 10–15 most common tools
   * a small **meta-tool** like `discover_tools` or `search_capabilities`
   * optionally a few category tools such as `filesystem`, `web`, `calendar`, `admin`

2. **Stage 2: expansion**
   If the model thinks it needs something outside the common set, it calls the meta-tool, which returns only a **small candidate set** of relevant tools with short summaries. Then you inject just those detailed schemas for the next turn.

That preserves context, improves tool choice, and keeps latency/token use under control. This matches current advice around large tool inventories and progressive loading. ([OpenAI Developers][1])

## The core idea

Do **not** make the model ask MCP for the full list of agents/tools every time.

Instead, maintain three layers:

### Layer A — resident tools

These are always present.

Examples:

* search web
* read file
* write file
* run shell
* query logs
* ask user / confirm action
* search_capabilities
* handoff_to_agent
* memory_lookup
* memory_write
* fetch_url
* inspect_code
* plan_step
* summarize_state
* maybe one or two domain-specific heavy hitters

Keep these tool descriptions very short and clean. Tool description quality matters; sloppy descriptions can degrade performance. ([arXiv][2])

### Layer B — capability index

A compact catalog, not full schemas.

For each hidden tool/agent, store:

* name
* one-sentence purpose
* tags
* input keywords
* risk level
* typical prerequisites
* whether it is read-only or mutating

Example:

```json
{
  "name": "jira_create_ticket",
  "summary": "Create a Jira issue in a specified project",
  "tags": ["jira", "ticket", "issue", "project-management"],
  "requires": ["project_key"],
  "risk": "write"
}
```

This index can live outside the main prompt and be searched via:

* embeddings
* BM25/keyword
* hand-written categories
* or a small routing model

### Layer C — on-demand schema load

Once the model selects a hidden tool, inject:

* only that tool
* or that tool plus 2–4 close alternatives

Not the whole catalog.

## Best pattern for your harness

Your current setup sounds like:

* agent asks MCP which agents are available
* MCP returns the catalog
* model reasons over it

That works at small scale, but it does not scale well. Every full listing costs tokens and makes selection noisier. Recent MCP commentary calls this one of the practical pain points: tool definitions and payloads compete directly with the working context window. ([DEV Community][3])

A better shape is:

```text
User request
   ↓
Primary agent sees:
  - system prompt
  - conversation state
  - top 15 common tools
  - discover_tools meta-tool
   ↓
If needed, agent calls discover_tools(query="need to update Jira sprint ticket")
   ↓
Harness/tool index returns top 3-8 relevant tools
   ↓
Harness injects full schemas for those tools only
   ↓
Agent chooses one and calls it
```

## What `discover_tools` should return

Not raw MCP output. Return a **curated shortlist**.

Good return shape:

```json
{
  "candidates": [
    {
      "tool": "jira_search_issues",
      "when_to_use": "Find existing issues by project, sprint, assignee, or text",
      "why_selected": "User asked about a sprint ticket and may need to locate it first"
    },
    {
      "tool": "jira_create_ticket",
      "when_to_use": "Create a new issue in a Jira project",
      "why_selected": "Relevant if the requested ticket does not exist"
    },
    {
      "tool": "jira_update_ticket",
      "when_to_use": "Modify fields on an existing issue",
      "why_selected": "Relevant if a known ticket needs status or content changes"
    }
  ]
}
```

Then your harness decides which schemas to reveal.

## Treat “click for more” as a tool, not a UI widget

For the model, the equivalent of “click for more” should be one of these:

* `discover_tools(task_description)`
* `expand_tool_category(category)`
* `find_agent_for_task(goal, constraints)`
* `load_tool_schema(tool_name)`

That is much more reliable than inventing a fake UI metaphor inside the prompt.

## Stronger architecture than “ask MCP what exists”

You can go one step further and stop making the model responsible for first-pass discovery.

Use **server-side preselection** before the LLM even sees tools:

* take user request
* run cheap routing over your capability index
* select top N candidate tools
* expose only those plus commons
* let the model decide among them

This usually beats forcing the model to browse a huge live registry. It reduces both token burn and tool confusion. OpenAI’s docs now point toward deferred loading for large function sets, and multiple recent sources note that too many visible tools harms selection quality. ([OpenAI Developers][1])

## Recommended concrete policy

For a harness like yours, I would use:

### Always visible

* 8–15 common tools
* 1 discovery meta-tool
* 1 handoff/meta-agent tool
* 1 memory/context tool

### Hidden behind discovery

* all niche integrations
* all write/destructive admin tools
* all domain-specific tools
* long-tail agent specializations

### Returned by discovery

* maximum 5 tools at once
* short summaries only
* then full schema for selected candidates only

### Hard rules

* never inject 100 tools
* never return the entire MCP registry unless debugging
* never expose destructive tools by default unless the current role needs them

## Split by persona

This is important. A lot of MCP guidance now recommends “one server, one job” and splitting tools by role/persona. ([philschmid.de][4])

So instead of one giant universal catalog, expose different defaults for:

* coding agent
* infra/admin agent
* research agent
* personal productivity agent
* planner/router agent

That matters more than the raw count.

A coding agent should not see:

* calendar write tools
* billing/admin tools
* personal CRM tools

Unless the task actually crosses domains.

## The best compression trick

Do not store giant human-readable tool descriptions in the hot path.

Keep two versions:

* **index description**: 1 line for discovery
* **execution description**: fuller schema only when loaded

That gives you a compact search layer and a richer invoke layer.

## The risk angle

Progressive disclosure also helps with safety. If every powerful tool is always visible, prompt injection and accidental misuse get easier. MCP/agent discussions increasingly call out tool impersonation, prompt injection, and server compromise as real concerns in production systems. Narrowing what is visible and callable per turn is a real security improvement, not just a token optimization. ([finops.org][5])

## What I would build in BeigeBox-style terms

Given how you think about operators/routing, I would make this:

### 1. Capability registry

A local database of all tools/agents:

* tool id
* category
* tags
* summary
* risk
* cost
* latency
* examples
* schema location

### 2. Capability search endpoint

Something like:

```python
POST /capabilities/search
{
  "query": "Need to update a Jira sprint issue and maybe create one",
  "top_k": 5,
  "role": "operator",
  "allow_write": true
}
```

### 3. Schema loader

```python
POST /capabilities/load
{
  "tool_ids": ["jira_search_issues", "jira_update_ticket"]
}
```

### 4. Policy layer

Filters by:

* agent role
* tenancy
* auth
* read/write permission
* environment
* confidence threshold

### 5. Tool-use transcript compression

After tool execution, do not keep the full schema around. Keep only:

* tool called
* arguments summary
* result summary
* artifact pointer if needed

That prevents tool metadata from polluting later turns.

## The simplest possible version

If you want the cheap version first:

* keep 15 common tools always visible
* add one tool called `discover_more_tools`
* back it with a static JSON file of hidden tools
* keyword-rank top 5
* inject only those 5 if called

Even that is already much better than full-catalog exposure.

## My recommendation

Use this pattern:

**common tools + discovery meta-tool + on-demand schema injection**

Not:

**full MCP registry query per turn**

That is the cleanest way to get your “click for more options” behavior in a form the model can actually use.

A compact prompt rule for the agent would be:

```text
You have a default set of common tools.
If no visible tool clearly fits, call discover_tools with the task you are trying to perform.
Do not guess hidden capabilities.
After discover_tools returns candidates, choose among those tools only.
```

And for the harness:

```text
Never reveal the full catalog unless explicitly in debug mode.
Return at most 5 discovered tools.
Prefer read-only tools before write tools when both could answer the question.
```

If useful, I can turn this into:

1. a concrete JSON schema for `discover_tools`, or
2. a routing design for BeigeBox with registry/search/load endpoints.

[1]: https://developers.openai.com/api/docs/guides/function-calling/?utm_source=chatgpt.com "Function calling | OpenAI API"
[2]: https://arxiv.org/html/2602.14878v2?utm_source=chatgpt.com "Model Context Protocol (MCP) Tool Descriptions Are ..."
[3]: https://dev.to/apideck/your-mcp-server-is-eating-your-context-window-theres-a-simpler-way-315b?utm_source=chatgpt.com "Your MCP Server Is Eating Your Context Window. There's ..."
[4]: https://www.philschmid.de/mcp-best-practices?utm_source=chatgpt.com "MCP is Not the Problem, It's your Server: Best Practices for ..."
[5]: https://www.finops.org/wg/model-context-protocol-mcp-ai-for-finops-use-case/?utm_source=chatgpt.com "Model Context Protocol (MCP): An AI for FinOps Use Case"
