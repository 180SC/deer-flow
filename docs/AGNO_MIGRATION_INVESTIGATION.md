# DeerFlow to Agno Migration Investigation

> **Date**: 2026-03-13
> **Status**: Investigation / Analysis
> **Scope**: Feasibility study for porting DeerFlow from LangGraph to Agno

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Current DeerFlow Architecture](#current-deerflow-architecture)
3. [Agno Framework Overview](#agno-framework-overview)
4. [Component-by-Component Migration Analysis](#component-by-component-migration-analysis)
5. [Claude Code & Max Plan Integration](#claude-code--max-plan-integration)
6. [Pros and Cons](#pros-and-cons)
7. [Migration Effort Estimate](#migration-effort-estimate)
8. [Recommendation](#recommendation)
9. [Sources](#sources)

---

## Executive Summary

DeerFlow is currently built on **LangGraph + LangChain**, using an explicit graph/state-machine architecture with 11 custom middlewares, subagents, MCP support, sandbox execution, persistent memory, and a full Gateway API. Porting to **Agno** (formerly PhiData) would mean replacing the core orchestration layer while preserving the surrounding infrastructure (frontend, gateway API, sandbox, channels).

**The critical finding**: Agno agents require an `ANTHROPIC_API_KEY` for Claude models and use Anthropic's pay-as-you-go API billing. **The Anthropic Max plan ($100-$200/month) cannot be used to authenticate Agno agents** — Max plan authentication is OAuth-based and only works with claude.ai and Claude Code CLI. This is the single biggest consideration for this migration if the goal is to leverage a Max subscription.

---

## Current DeerFlow Architecture

### Framework Stack
- **Orchestration**: LangGraph (graph-based agent runtime)
- **LLM Interactions**: LangChain (chains, tools, model adapters)
- **API**: FastAPI (Gateway on port 8001) + LangGraph Server (port 2024)
- **Frontend**: Next.js
- **Proxy**: Nginx (port 2026, unified routing)

### Key Components

| Component | Implementation | Files |
|---|---|---|
| Lead Agent | Single agent with dynamic LLM selection, 11 middlewares | `src/agents/lead_agent/agent.py` |
| Subagents | general-purpose (50 turns), bash specialist | `src/subagents/builtins/` |
| Thread State | Custom `ThreadState` extending `AgentState` with sandbox, artifacts, todos, memory | `src/agents/thread_state.py` |
| Middleware Chain | 11 middlewares in strict order (thread data, uploads, sandbox, dangling tool calls, summarization, todos, titles, memory, images, subagent limits, clarification) | `src/agents/middlewares/` |
| Tools | Config-driven tool loading + MCP tools + built-in tools (present_files, ask_clarification, view_image) + sandbox tools (bash, ls, read, write, str_replace) | `src/tools/tools.py` |
| Model Factory | Dynamic model instantiation via reflection, supports thinking/vision/reasoning | `src/models/factory.py` |
| MCP | `langchain-mcp-adapters`, lazy init, cache invalidation, OAuth support | `src/mcp/` |
| Memory | LLM-powered fact extraction, debounced updates, JSON storage | `src/agents/memory/` |
| Sandbox | Pluggable provider (local, Kubernetes), path translation | `src/sandbox/` |
| Gateway API | 7 routers: models, MCP, skills, memory, uploads, artifacts, suggestions | `src/gateway/` |
| IM Channels | Telegram, Slack, Feishu/Lark bridges | `src/channels/` |
| Embedded Client | `DeerFlowClient` for in-process access without HTTP | `src/client.py` |

### Key Dependencies
- `langgraph>=1.0.6`, `langgraph-api>=0.7.0`, `langgraph-checkpoint-sqlite>=3.0.3`
- `langchain>=1.2.3`, `langchain-anthropic>=1.3.4`, `langchain-openai>=1.1.7`
- `langchain-mcp-adapters>=0.1.0`
- `fastapi>=0.115.0`, `tavily-python`, `firecrawl-py`, etc.

---

## Agno Framework Overview

### What is Agno?
Agno (formerly PhiData) is an open-source Python framework for building multi-agent AI systems. It uses a three-layer architecture:

1. **SDK Layer**: High-level abstractions — `Agent`, `Team`, `Workflow`, `Memory`, `Knowledge`, `Tools`
2. **Engine Layer**: Model calls, tool orchestration, structured output handling
3. **AgentOS Layer**: Production-ready FastAPI backend with streaming APIs, auth, tracing

### Core Abstractions

| Concept | LangGraph Equivalent | Description |
|---|---|---|
| `Agent` | Node/subgraph | Atomic unit: model + instructions + tools |
| `Team` | Multi-agent graph | Coordinates agents (hierarchical, collaborative, sequential) |
| `Workflow` | Graph with edges | Deterministic orchestration with Step, Parallel, Router, Loop, Condition |
| `Memory` | Custom state + checkpointer | Session state + long-term user memories + knowledge |
| `MCPTools` | `langchain-mcp-adapters` | MCP client with stdio/HTTP/SSE transports |

### Performance Claims
- Agent instantiation: ~2 microseconds (~529x faster than LangGraph)
- Memory per agent: ~3.75 KiB

### Model Support
40+ models across 20+ providers, including native Claude support:
```python
from agno.models.anthropic import Claude
agent = Agent(model=Claude(id="claude-sonnet-4-6"))
```

---

## Component-by-Component Migration Analysis

### 1. Lead Agent → Agno Agent
**Difficulty: Medium**

DeerFlow's lead agent would map to an Agno `Agent`. The core agent creation is straightforward:
```python
# Current (LangGraph)
agent = create_agent(model, tools, prompt)

# Agno equivalent
agent = Agent(model=Claude(), tools=[...], instructions=[...])
```

**Challenge**: DeerFlow's 11-middleware chain has no direct Agno equivalent. Each middleware would need to be reimplemented as either:
- Pre/post hooks on tools (`Function(pre_hooks=[], post_hooks=[])`)
- Custom logic wrapping agent execution
- Agno's built-in features (where they overlap, e.g., memory)

### 2. Middleware Chain → Custom Orchestration
**Difficulty: High**

This is the hardest part of the migration. DeerFlow's middleware chain provides cross-cutting concerns that Agno doesn't have a native pattern for:

| Middleware | Agno Equivalent | Effort |
|---|---|---|
| ThreadDataMiddleware | `session_state` + custom setup | Medium |
| UploadsMiddleware | Custom pre-processing | Medium |
| SandboxMiddleware | Custom lifecycle management | High |
| DanglingToolCallMiddleware | May not be needed (different runtime) | Low |
| SummarizationMiddleware | `add_history_to_context` + `num_history_runs` | Low |
| TodoListMiddleware | `session_state` | Medium |
| TitleMiddleware | Custom post-processing | Low |
| MemoryMiddleware | Agno `Memory` (partial overlap) | Medium |
| ViewImageMiddleware | Custom pre-processing | Medium |
| SubagentLimitMiddleware | Custom wrapper around Team | Medium |
| ClarificationMiddleware | Custom tool + interrupt logic | High |

### 3. Subagents → Agno Team
**Difficulty: Medium**

DeerFlow's subagent system (general-purpose + bash) maps to Agno's `Team` with `TeamMode.HIERARCHICAL`:
```python
team = Team(
    name="DeerFlow",
    members=[general_purpose_agent, bash_agent],
    mode=TeamMode.HIERARCHICAL,
)
```

**Challenge**: DeerFlow has fine-grained control over subagent tool restrictions (e.g., no `task`, `ask_clarification` for subagents). Agno Teams allow per-member tool lists, so this is achievable.

### 4. Thread State → session_state + Database
**Difficulty: Medium**

DeerFlow's `ThreadState` with custom reducers would map to Agno's `session_state` dict + a `db` backend:
```python
agent = Agent(
    db=SqliteDb(db_file="deerflow.db"),
    session_state={"sandbox": None, "artifacts": [], "todos": []},
)
```

**Challenge**: DeerFlow's custom reducers (artifact deduplication, image merge/clear) would need manual implementation.

### 5. Tools → Agno Tools
**Difficulty: Low-Medium**

Most DeerFlow tools have direct Agno equivalents:

| DeerFlow Tool | Agno Equivalent |
|---|---|
| Tavily search/fetch | `TavilyTools` (built-in) |
| DuckDuckGo image search | `DuckDuckGoTools` (built-in) |
| Firecrawl | `FirecrawlTools` (built-in) |
| Bash execution | `ShellTools` (built-in) |
| File read/write | `FileTools` (built-in) |
| Jina reader | Custom tool (wrap existing code) |
| MCP tools | `MCPTools` / `MultiMCPTools` |

Custom tools (present_files, ask_clarification, view_image) would need reimplementation using Agno's `@tool` decorator.

### 6. Model Factory → Agno Model Classes
**Difficulty: Low**

DeerFlow's reflection-based model factory maps directly to Agno's model classes:
```python
# DeerFlow: dynamic via config
model = create_chat_model(name="claude-sonnet-4-6")

# Agno: explicit model classes
from agno.models.anthropic import Claude
from agno.models.openai import OpenAIChat
model = Claude(id="claude-sonnet-4-6")
```

Agno natively supports OpenAI, Anthropic, Google, DeepSeek, Ollama, Groq, AWS Bedrock, Azure, and more.

### 7. MCP Integration → MCPTools
**Difficulty: Low**

Both frameworks support MCP well:
```python
# DeerFlow: langchain-mcp-adapters
from langchain_mcp_adapters import MultiServerMCPClient

# Agno: native MCPTools
from agno.tools.mcp import MCPTools, MultiMCPTools
agent = Agent(tools=[MCPTools(url="http://localhost:8080/mcp")])
```

**Caveat**: There are reported bugs with Agno's remote MCP HTTP streaming + Claude models (works with OpenAI). The stdio transport works reliably.

### 8. Memory System → Agno Memory
**Difficulty: Medium**

Agno has a built-in `Memory` system with `user_memories`, `cultural_knowledge`, and session summaries. However, DeerFlow's memory is more customized (confidence scores, debouncing, specific fact structure). Partial overlap exists — some custom logic would still be needed.

### 9. Gateway API → Custom FastAPI (or AgentOS)
**Difficulty: Medium-High**

Two options:
1. **Keep the existing Gateway API** and adapt it to call Agno agents instead of LangGraph
2. **Use AgentOS** (Agno's built-in FastAPI backend) and extend it with DeerFlow-specific endpoints

Option 1 is likely easier since DeerFlow's Gateway has 7 specialized routers that AgentOS doesn't replicate.

### 10. Sandbox System → Custom Integration
**Difficulty: High**

Agno has no built-in sandbox abstraction. DeerFlow's entire sandbox system (provider interface, local/K8s providers, path translation, lifecycle management) would need to be preserved as custom code wrapping Agno agents.

### 11. IM Channels → Custom Integration
**Difficulty: Low**

The channel bridges (Telegram, Slack, Feishu) are largely independent of the agent runtime. They communicate via the LangGraph SDK client, which would be replaced with direct Agno agent calls.

---

## Claude Code & Max Plan Integration

This is the most critical consideration raised. Here's the detailed analysis:

### How the Max Plan Works
- **Max $100/month**: ~140-280 hours of Sonnet 4, ~15-35 hours of Opus 4 per week
- **Max $200/month**: ~240-480 hours of Sonnet 4, ~24-40 hours of Opus 4 per week
- **Authentication**: OAuth-based login (not API keys)
- **Access surface**: claude.ai web interface and Claude Code CLI only

### The Problem with Agno + Max Plan
**Agno agents require `ANTHROPIC_API_KEY`** to use Claude models. This key comes from the Anthropic Console (console.anthropic.com) and uses **pay-as-you-go API billing** — completely separate from the Max subscription.

There is **no way to route Agno agent API calls through your Max plan subscription**. Setting `ANTHROPIC_API_KEY` in Claude Code actually switches Claude Code itself from Max plan billing to API billing.

### The Problem with LangGraph + Max Plan (Current State)
The same limitation applies to the **current** LangGraph-based DeerFlow: it uses `langchain-anthropic` which also requires `ANTHROPIC_API_KEY`. So **neither framework can use the Max plan** for agent API calls.

### Possible Workarounds

1. **Use Claude Code as the orchestrator directly**: Instead of running DeerFlow/Agno as a separate service, use Claude Code (which runs on Max plan) as the agent runtime, with DeerFlow tools exposed as MCP servers. This fundamentally changes the architecture but lets you leverage the Max plan.

2. **Expose Agno agents as MCP servers for Claude Code**: Agno supports `enable_mcp_server=True` on AgentOS, which exposes agents as MCP tools. Claude Code could connect to these MCP servers. However, the Agno agents themselves would still need API keys for their LLM calls — this only helps if the Agno agents use non-Claude models (e.g., GPT-4o, Gemini) while Claude Code (on Max) does the primary reasoning.

3. **Hybrid architecture**: Use Claude Code (Max plan) for the main agent loop, delegate specialized tasks to Agno agents running cheaper models (GPT-4o-mini, Haiku via API, open-source via Ollama).

4. **Wait for Anthropic to offer API access in Max plan**: This is speculative — Anthropic may eventually bridge subscription and API billing, but there's no indication of this.

### Bottom Line on Max Plan

| Scenario | Max Plan Usable? |
|---|---|
| DeerFlow (current LangGraph) calling Claude API | No — requires API key |
| DeerFlow ported to Agno calling Claude API | No — requires API key |
| Claude Code as orchestrator + DeerFlow tools as MCP | Yes — Claude Code uses Max |
| Agno agents exposed as MCP servers for Claude Code | Partially — Claude Code uses Max, but Agno sub-agents need API keys |

**Migrating to Agno does not solve the Max plan integration problem.** The Max plan limitation is at the Anthropic authentication/billing layer, not the framework layer.

---

## Pros and Cons

### Pros of Migrating to Agno

| Pro | Details |
|---|---|
| **Simpler API surface** | Declarative `Agent`, `Team`, `Workflow` classes vs. explicit graph/state-machine construction. Less boilerplate. |
| **Faster agent instantiation** | ~2 microseconds vs. LangGraph's heavier initialization. Could matter for high-throughput scenarios. |
| **Lower memory footprint** | ~3.75 KiB per agent vs. LangGraph's larger footprint. |
| **Native multi-agent patterns** | `Team` with hierarchical/collaborative/sequential modes built-in, vs. manually composing LangGraph subgraphs. |
| **Rich built-in toolkit** | 80+ tool integrations out of the box (DuckDuckGo, Tavily, Firecrawl, Slack, Shell, File, etc.). Many DeerFlow community tools have Agno equivalents. |
| **Model-agnostic by design** | Clean one-line model swapping. Native support for 40+ models across 20+ providers. |
| **Built-in memory system** | User memories, cultural knowledge, session summaries — some overlap with DeerFlow's custom memory. |
| **MCP bidirectional support** | Can act as both MCP client and MCP server. AgentOS exposes agents as MCP tools natively. |
| **AgentOS production runtime** | Built-in FastAPI backend with streaming, auth, tracing, and 50+ APIs. Could replace some Gateway API functionality. |
| **Active development** | Rapidly evolving framework with strong community (15k+ GitHub stars). |
| **Workflow primitives** | `Step`, `Parallel`, `Router`, `Loop`, `Condition` for deterministic orchestration. |

### Cons of Migrating to Agno

| Con | Details |
|---|---|
| **Does NOT solve Max plan integration** | Agno still requires `ANTHROPIC_API_KEY` for Claude — same as LangGraph. No framework can use the Max subscription for API calls. |
| **Massive middleware rewrite** | DeerFlow's 11-middleware chain has no Agno equivalent. Each middleware needs reimplementation as custom code, losing the clean middleware pattern. |
| **Loss of fine-grained control** | LangGraph's explicit graph model gives precise control over execution flow, conditional branching, and state transitions. Agno's higher-level abstractions trade control for simplicity. |
| **Sandbox system is custom** | Agno has no sandbox abstraction. The entire sandbox lifecycle (acquire, path translation, tool wrapping, K8s provider) must be preserved as custom code. |
| **Known Claude-specific bugs** | Reported issues with structured outputs returning empty responses, and remote MCP HTTP streaming not working with Claude models. |
| **Less mature than LangGraph** | LangGraph has a more established production track record and deeper LangChain ecosystem integration. |
| **Breaking changes risk** | Agno recently rebranded from PhiData and is evolving rapidly. API stability is less proven than LangGraph. |
| **Custom state reducers lost** | DeerFlow's custom reducers (artifact dedup, image merge/clear) have no Agno equivalent — need manual reimplementation. |
| **Gateway API rework** | DeerFlow's 7-router Gateway API would need significant adaptation. AgentOS doesn't cover all the same endpoints (uploads, artifacts, suggestions, skills management). |
| **Checkpointing differences** | LangGraph's `langgraph-checkpoint-sqlite` provides robust multi-turn persistence. Agno's `session_state` + `db` is simpler but less integrated. |
| **LangSmith observability lost** | DeerFlow uses LangSmith tracing. Agno has its own tracing but it's a different ecosystem. |
| **Large migration effort** | Estimated 4-8 weeks for a competent team, with high regression risk during the transition. |

---

## Migration Effort Estimate

| Component | Effort | Risk |
|---|---|---|
| Core agent (Lead Agent → Agno Agent) | 1-2 days | Low |
| Middleware chain reimplementation | 1-2 weeks | **High** |
| Subagent system → Team | 2-3 days | Medium |
| Thread state → session_state | 2-3 days | Medium |
| Tool migration | 3-5 days | Low |
| Model factory → Agno models | 1 day | Low |
| MCP integration | 1-2 days | Low-Medium |
| Memory system | 3-5 days | Medium |
| Gateway API adaptation | 1 week | Medium-High |
| Sandbox system preservation | 3-5 days | High |
| IM channel bridges | 2-3 days | Low |
| Embedded client update | 1-2 days | Low |
| Testing & regression fixes | 1-2 weeks | **High** |
| **Total** | **~4-8 weeks** | **High overall** |

---

## Recommendation

### Should you migrate?

**Not if the primary motivation is Max plan integration.** Neither Agno nor LangGraph can use the Anthropic Max subscription for API calls. This is an Anthropic billing/authentication limitation, not a framework limitation.

### If you want Max plan integration specifically:

The most viable path is **restructuring DeerFlow as a set of MCP servers that Claude Code connects to**, rather than a standalone agent system. This lets Claude Code (running on Max plan) be the orchestrator, while DeerFlow provides specialized tools (sandbox, file management, web search, etc.) via MCP. This approach works with **either** framework.

### If you still want to evaluate Agno for other reasons:

Consider Agno if you want:
- Simpler codebase with less boilerplate
- Faster agent instantiation for high-throughput use cases
- Built-in multi-agent team patterns
- The ability to expose agents as MCP servers natively
- A lighter-weight framework overall

Stick with LangGraph if you need:
- Fine-grained control over complex stateful workflows
- The existing middleware pattern
- Proven production stability
- LangChain ecosystem integration (LangSmith, etc.)
- Minimal migration risk

### Suggested hybrid approach:

Rather than a full port, consider:
1. **Keep the current LangGraph core** for the main agent orchestration
2. **Expose DeerFlow capabilities as MCP servers** for Claude Code integration
3. **Experiment with Agno** for new, isolated features or subagents where its simpler API is beneficial
4. **Re-evaluate** when Anthropic potentially bridges Max plan and API billing

---

## Sources

- [Agno Official Documentation](https://docs.agno.com)
- [Agno GitHub Repository](https://github.com/agno-agi/agno)
- [Agno Agent Framework](https://www.agno.com/agent-framework)
- [Agno AgentOS](https://www.agno.com/agentos)
- [Agno Anthropic Claude Integration](https://docs.agno.com/models/providers/native/anthropic/overview)
- [Agno MCP Support](https://docs.agno.com/tools/mcp)
- [Agno vs LangGraph - ZenML](https://www.zenml.io/blog/agno-vs-langgraph)
- [Best AI Agent Frameworks 2025 - LangWatch](https://langwatch.ai/blog/best-ai-agent-frameworks-in-2025-comparing-langgraph-dspy-crewai-agno-and-more)
- [Claude Code with Pro/Max Plan](https://support.claude.com/en/articles/11145838-using-claude-code-with-your-pro-or-max-plan)
- [Anthropic API Pricing](https://www.nops.io/blog/anthropic-api-pricing/)
- [Claude Agent SDK Overview](https://platform.claude.com/docs/en/agent-sdk/overview)
- [Agno Bug: Claude Structured Outputs](https://github.com/agno-agi/agno/issues/2288)
- [Agno Bug: Remote MCP with Claude](https://github.com/agno-agi/agno/issues/4384)
