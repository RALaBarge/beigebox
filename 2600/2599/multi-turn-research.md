# ✅ COMPLETE — Reference research. LangChain multi-agent patterns documented and digested. No further action required.

# LangChain Multi-Agent Systems: Patterns, Follow-up Request Design & Model Sizing

I've ingested the LangChain multi-agent documentation and related resources. Here's a comprehensive guide to help you design your system.

---

## 🧩 Multi-Agent Patterns Overview

LangChain defines **five core patterns** for multi-agent coordination:

| Pattern | Best For | Key Tradeoff |
|---------|----------|-------------|
| **Subagents** | Multiple distinct domains, centralized control, parallel execution | +1 model call per request (results flow through main agent) |
| **Handoffs** | Sequential workflows, state-driven transitions, direct user interaction | Requires careful state management; context must be explicitly passed |
| **Skills** | Single agent with many specializations, lightweight composition | Context accumulates in conversation → token bloat on repeat calls |
| **Router** | Distinct verticals, parallel queries, result synthesis | Stateless by default; wrapping as a tool adds statefulness |
| **Custom Workflow** | Complex logic mixing deterministic + agentic steps | Maximum flexibility, but higher implementation complexity |

### Quick Decision Framework
```
Need parallel execution? → Subagents or Router
Need sequential state transitions? → Handoffs
Need lightweight specializations? → Skills
Need bespoke control flow? → Custom Workflow with LangGraph
```

---

## 🔄 Structuring Follow-up Requests for Multi-Step Problems

### Core Principle: Context Engineering
> "The quality of your system depends on ensuring each agent has access to the right data for its task." [[docs]]

### Three Strategies for Follow-up Handling

#### 1. **Stateful Agent Patterns** (Handoffs, Skills)
```python
# Handoffs: State persists across turns via Commands
@tool
def transfer_to_specialist(runtime) -> Command:
    return Command(
        update={
            "current_step": "specialist",  # Triggers behavior change
            "messages": [ToolMessage(...)]  # Complete tool-call cycle
        }
    )
```
✅ Saves 40-50% of model calls on repeat requests [[performance docs]]  
✅ Natural conversation flow with context carryover  
⚠️ Requires explicit context filtering to avoid bloat

#### 2. **Stateless with Tool Wrapping** (Router, Subagents)
```python
# Wrap stateless router as a tool for a conversational agent
@tool
def search_docs(query: str) -> str:
    result = workflow.invoke({"query": query})  # Stateless router
    return result["final_answer"]

conversational_agent = create_agent(
    model, 
    tools=[search_docs],  # Router becomes a tool
    prompt="You are helpful. Use search_docs when needed."
)
```
✅ Strong context isolation per subagent invocation  
✅ Easier debugging (each call is independent)  
⚠️ Repeat requests re-execute full flow (no call savings)

#### 3. **Hybrid: Checkpointing + Interrupts** (LangGraph)
```python
# Use interrupts for human-in-the-loop follow-ups
def human_review(state: State) -> Command:
    decision = interrupt({
        "draft": state["draft_response"],
        "action": "Approve or edit?"
    })
    return Command(
        update={"approved": decision["approved"]},
        goto="send_reply" if decision["approved"] else "revise"
    )
```
✅ Durable execution: pause/resume across days  
✅ Explicit state boundaries for debugging  
✅ Natural fit for approval workflows, clarifications

### Follow-up Request Design Checklist

| Question | Design Implication |
|----------|-------------------|
| Does the next step depend on prior output? | Use **stateful patterns** (Handoffs/Skills) or pass results via `Command(update={...})` |
| Do you need to retry/rollback mid-flow? | Use **LangGraph checkpoints** + `interrupt()` for pause points |
| Are subagents independent tasks? | Use **async execution** with job IDs + status polling |
| Will users ask clarifying questions? | Keep conversation history in main agent; use `ToolMessage` pairs for valid LLM history |
| Do you need parallel subtasks? | Use `Send` for fan-out (Router) or parallel tool calls (Subagents) |

---

## 🤖 Model Size: What Do You Actually Need?

### Short Answer
> **Model size is less about "problem size" and more about:**
> 1. **Reasoning complexity** of the *coordination logic* (main agent)
> 2. **Domain expertise** required by *specialized agents*
> 3. **Prompt quality** and context engineering

### Practical Guidelines

#### For the **Main/Supervisor Agent** (coordination layer):
```
✅ Start with mid-tier models (Claude Sonnet 4, GPT-4.1, Llama 3.1 70B)
✅ Prioritize strong instruction-following and tool-calling reliability
✅ Optimize prompts for routing decisions before scaling up
```
*Why?* The main agent's job is orchestration, not deep domain reasoning. A well-prompted mid-tier model often outperforms a larger model with poor prompting.

#### For **Specialized Subagents**:
```
✅ Match model capability to task complexity:
   - Simple retrieval/formatting → smaller models (GPT-4o-mini, Claude Haiku)
   - Complex reasoning/analysis → larger models (Opus, GPT-4.5)
✅ Use context isolation: each subagent gets only relevant docs → smaller context = cheaper/faster
```
*Key insight from Anthropic research*: A multi-agent system with Claude Opus 4 (lead) + Sonnet 4 (subagents) outperformed single-agent Opus 4 by 90% on research tasks [[blog]]. The win came from **distributed context windows**, not just model size.

#### Prompt Quality > Model Size
```python
# Bad: Vague subagent description
@tool("research", description="Do research")  # ❌ Main agent won't know when to use it

# Good: Specific, action-oriented spec
@tool("research", description="Use for factual queries requiring web search or academic sources. Returns summarized findings with citations.")  # ✅
```

### Token Efficiency by Pattern (Multi-Domain Query Example)
| Pattern | Model Calls | Total Tokens | Why |
|---------|-------------|--------------|-----|
| **Subagents** | 5 | ~9K | Each subagent works in isolation with only relevant context |
| **Router** | 5 | ~9K | Parallel execution + isolated contexts |
| **Skills** | 3 | ~15K | Fewer calls, but loaded skills accumulate in context |
| **Handoffs** | 7+ | ~14K+ | Sequential execution + growing conversation history |

→ For large-context domains, **Subagents/Router win on token efficiency** due to context isolation [[performance docs]].

---

## 🚀 Recommended Starting Approach

1. **Start simple**: Single agent + good tools + strong prompts
2. **Add subagents only when**: 
   - You hit context window limits with domain knowledge
   - Different teams own different capabilities
   - You need parallel execution for latency
3. **Choose model sizes pragmatically**:
   ```yaml
   main_agent: claude-sonnet-4  # or gpt-4.1 - reliable orchestration
   subagents: 
     - simple_tasks: claude-haiku  # or gpt-4o-mini
     - complex_reasoning: claude-opus-4  # only where needed
   ```
4. **Structure follow-ups with LangGraph**:
   - Use `Command` for state transitions
   - Use `interrupt()` for human-in-the-loop checkpoints
   - Keep state raw; format prompts on-demand in nodes

### Quick Reference: Follow-up Request Patterns
```python
# Pattern A: Stateful handoff (for sequential workflows)
return Command(update={"step": "next"}, goto="next_agent")

# Pattern B: Stateless subagent call (for parallel/isolated tasks)
result = subagent.invoke({"query": refined_query})
return result["messages"][-1].content

# Pattern C: Async job for long-running tasks
job_id = start_background_job(agent="analyst", task=complex_task)
return f"Started analysis (job: {job_id}). Use check_status('{job_id}') for updates."

# Pattern D: Interrupt for human clarification
clarification = interrupt({"question": "Which timeframe should I analyze?"})
return Command(update={"timeframe": clarification["answer"]}, goto="continue_analysis")
```

---

**Bottom line**: Model size matters less than *how you structure context flow*. A well-designed multi-agent system with mid-tier models and strong context engineering will outperform a monolithic large-model agent with poor prompting. Start with the simplest pattern that solves your constraint, then iterate.

Want me to dive deeper into any specific pattern or help you sketch a architecture for your use case?
