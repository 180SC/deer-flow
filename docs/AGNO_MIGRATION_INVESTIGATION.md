# DeerFlow Architecture Investigation: Agno, Claude Code, and Max Plan

> **Date**: 2026-03-13
> **Status**: Investigation / Analysis
> **Scope**: Feasibility study for porting DeerFlow from LangGraph to Agno, with focus on Max plan integration via Claude Code as inference engine

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Current DeerFlow Architecture](#current-deerflow-architecture)
3. [Agno Framework Overview](#agno-framework-overview)
4. [The Max Plan Problem](#the-max-plan-problem)
5. [Architecture Option A: Agno + Claude Code Docker Workers](#architecture-option-a-agno--claude-code-docker-workers)
6. [Architecture Option B: Claude Agent SDK Orchestrator](#architecture-option-b-claude-agent-sdk-orchestrator)
7. [Architecture Option C: Lightweight Custom Orchestrator + Claude Code](#architecture-option-c-lightweight-custom-orchestrator--claude-code)
8. [Claude Code Capabilities Reference](#claude-code-capabilities-reference)
9. [Component-by-Component Migration Analysis (Agno)](#component-by-component-migration-analysis-agno)
10. [Pros and Cons](#pros-and-cons)
11. [Recommendation](#recommendation)
12. [Sources](#sources)

---

## Executive Summary

DeerFlow is currently built on **LangGraph + LangChain**. The primary goal is to leverage the **Anthropic Max plan** ($100-$200/month) for inference rather than paying per-API-call, while maintaining a highly customizable multi-agent system.

**Key finding**: No agent framework (Agno, LangGraph, CrewAI, etc.) can use the Max plan directly — they all require `ANTHROPIC_API_KEY` which bills separately. However, **Claude Code** (the CLI) authenticates via the Max plan and can run in headless mode (`-p` flag) with full tool access.

**The most promising architecture**: Use a lightweight orchestrator (Agno, custom Python, or the Claude Agent SDK) to manage workflow and task decomposition, then **dispatch Docker containers running Claude Code** for actual inference and code execution. Each container authenticates against the Max plan, so all LLM costs are covered by the subscription.

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
| Tools | Config-driven tool loading + MCP tools + built-in tools + sandbox tools (bash, ls, read, write, str_replace) | `src/tools/tools.py` |
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
Agno (formerly PhiData) is an open-source Python framework for building multi-agent AI systems. Three-layer architecture:

1. **SDK Layer**: `Agent`, `Team`, `Workflow`, `Memory`, `Knowledge`, `Tools`
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

### Performance
- Agent instantiation: ~2 microseconds (~529x faster than LangGraph)
- Memory per agent: ~3.75 KiB
- 80+ built-in tool integrations, 40+ models across 20+ providers

### Customizability
Agno is highly customizable:
- **Custom tools**: `@tool` decorator on any Python function
- **Custom workflows**: `Step`, `Parallel`, `Router`, `Loop`, `Condition` primitives
- **Custom memory**: Pluggable database backends (SQLite, Postgres, etc.)
- **MCP integration**: Both client and server, all transport types
- **Model-agnostic**: Swap models with a single line change
- **Pre/post hooks**: On every tool via `Function(pre_hooks=[], post_hooks=[])`

---

## The Max Plan Problem

### Why This Matters
The Anthropic Max plan ($100-$200/month) provides generous Claude usage (hundreds of hours of Sonnet, tens of hours of Opus per week) via **OAuth-based authentication**. But every agent framework (Agno, LangGraph, CrewAI, etc.) requires `ANTHROPIC_API_KEY` which uses **separate pay-as-you-go billing**.

| Access Method | Auth Type | Billing | Works With |
|---|---|---|---|
| claude.ai | OAuth (Max plan) | Subscription | Web UI only |
| Claude Code CLI | OAuth (Max plan) | Subscription | CLI, headless mode, Agent SDK |
| Anthropic API | API key | Pay-per-token | All frameworks (Agno, LangGraph, etc.) |

### The Insight
**Claude Code is the bridge.** It authenticates via Max plan OAuth and can run in headless mode with full tool access (Bash, file I/O, web search, etc.). If we use Claude Code as the "inference engine" inside Docker containers, the orchestrator doesn't need API keys at all — it just dispatches work to Claude Code instances.

---

## Architecture Option A: Agno + Claude Code Docker Workers

### Concept
Use Agno as the **orchestrator** (workflow management, task decomposition, state) but replace direct LLM API calls with **Claude Code running inside Docker containers**. Agno handles the "what" (task planning, routing, state management), Claude Code handles the "how" (inference, code execution, tool use).

### Architecture Diagram
```
┌──────────────────────────────────────────────────┐
│                  DeerFlow Frontend                │
│                   (Next.js)                       │
└─────────────────────┬────────────────────────────┘
                      │
┌─────────────────────▼────────────────────────────┐
│              Gateway API (FastAPI)                 │
│     Models │ MCP │ Skills │ Memory │ Uploads      │
└─────────────────────┬────────────────────────────┘
                      │
┌─────────────────────▼────────────────────────────┐
│           Agno Orchestrator Layer                  │
│                                                    │
│  ┌─────────┐  ┌──────────┐  ┌─────────────────┐  │
│  │Workflow  │  │  Team    │  │  State/Memory   │  │
│  │(Steps,  │  │(Route to │  │  (session_state  │  │
│  │ Parallel,│  │ right    │  │   + SqliteDb)   │  │
│  │ Router) │  │ worker)  │  │                  │  │
│  └────┬────┘  └────┬─────┘  └─────────────────┘  │
│       │            │                               │
│  ┌────▼────────────▼──────────────────────────┐   │
│  │        Docker Dispatch Manager              │   │
│  │  • Spawns Claude Code containers            │   │
│  │  • Passes task prompt + allowed tools       │   │
│  │  • Collects results (JSON output)           │   │
│  │  • Manages container lifecycle              │   │
│  └────┬───────────┬──────────────┬────────────┘   │
└───────│───────────│──────────────│─────────────────┘
        │           │              │
   ┌────▼───┐  ┌───▼────┐  ┌─────▼──────┐
   │Docker  │  │Docker  │  │Docker      │
   │Worker 1│  │Worker 2│  │Worker N    │
   │        │  │        │  │            │
   │Claude  │  │Claude  │  │Claude      │
   │Code    │  │Code    │  │Code        │
   │(Max $$)│  │(Max $$)│  │(Max $$)    │
   │        │  │        │  │            │
   │-p mode │  │-p mode │  │-p mode     │
   │+tools  │  │+tools  │  │+tools      │
   └────────┘  └────────┘  └────────────┘
        All inference billed to Max plan
```

### How It Works

1. **Request arrives** at Gateway API
2. **Agno orchestrator** decomposes the task using its Workflow/Team primitives
   - Agno itself does NOT call any LLM — it uses predefined routing logic
   - Or, optionally, uses a cheap local model (Ollama) for task decomposition
3. **Docker Dispatch Manager** spawns a container for each task:
   ```bash
   docker run --rm \
     -v ~/.claude:/home/user/.claude \
     -v /workspace:/workspace \
     deerflow-worker \
     claude -p "Research the latest AI news and summarize findings" \
       --allowedTools "Read,Write,Bash,WebSearch,WebFetch" \
       --output-format json \
       --max-turns 10
   ```
4. **Claude Code** inside the container does the actual work (inference + tool use), authenticated via Max plan
5. **Results** returned as JSON to Agno, which aggregates and manages state

### Auth in Docker
- **Mount `~/.claude/`** into containers for OAuth token persistence
- Claude Code stores tokens in `~/.claude/.credentials.json` (Linux)
- **Known issue**: OAuth token refresh race condition when multiple containers share the same token file
- **Mitigation**: Use a token proxy/mutex, or serialize container launches, or use separate sessions

### Docker Worker Image
```dockerfile
FROM ubuntu:24.04

# Install Claude Code
RUN curl -o /tmp/install.sh \
      https://storage.googleapis.com/claude-code-releases/latest/install.sh && \
    bash /tmp/install.sh && rm /tmp/install.sh

# Install common tools
RUN apt-get update && apt-get install -y git python3 nodejs npm

# Pre-configure for headless use
ENV CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1

ENTRYPOINT ["claude"]
```

### Pros
- All inference on Max plan — no API costs
- Agno provides clean workflow orchestration, customizable routing
- Claude Code gets full tool access (Bash, files, web search) inside each container
- Each container is isolated (sandboxed by Docker)
- Highly customizable — Agno workflows can be arbitrarily complex

### Cons
- Container startup latency (~2-5 seconds per Docker container)
- OAuth token sharing across containers is fragile (race conditions on refresh)
- Max plan rate limits shared across all concurrent containers
- More complex infrastructure (Docker daemon, container management)
- Agno's LLM-based Team routing wouldn't work (no direct API access) — routing must be rule-based or use a local model

---

## Architecture Option B: Claude Agent SDK Orchestrator

### Concept
Skip both Agno and LangGraph entirely. Use the **Claude Agent SDK** (`claude-agent-sdk` Python package) as the orchestrator. It provides the same tools and agent loop as Claude Code, callable programmatically from Python.

### Architecture Diagram
```
┌──────────────────────────────────────────────────┐
│                  DeerFlow Frontend                │
│                   (Next.js)                       │
└─────────────────────┬────────────────────────────┘
                      │
┌─────────────────────▼────────────────────────────┐
│              Gateway API (FastAPI)                 │
└─────────────────────┬────────────────────────────┘
                      │
┌─────────────────────▼────────────────────────────┐
│          Claude Agent SDK Orchestrator             │
│                                                    │
│  from claude_agent_sdk import query,               │
│       ClaudeAgentOptions                           │
│                                                    │
│  async for msg in query(                           │
│      prompt="Research AI news",                    │
│      options=ClaudeAgentOptions(                   │
│          allowed_tools=["Read","Bash","WebSearch"],│
│          mcp_servers={                             │
│              "deerflow": {                         │
│                  "type": "stdio",                  │
│                  "command": "python",              │
│                  "args": ["-m", "deerflow.mcp"]    │
│              }                                     │
│          },                                        │
│          max_turns=10,                             │
│      ),                                            │
│  ):                                                │
│      handle(msg)                                   │
│                                                    │
│  ┌──────────────────────────────────────────┐     │
│  │         DeerFlow MCP Servers              │     │
│  │  • Sandbox tools (bash, file I/O)         │     │
│  │  • Memory (read/write facts)              │     │
│  │  • Skills (load/invoke)                   │     │
│  │  • Web tools (search, fetch, scrape)      │     │
│  └──────────────────────────────────────────┘     │
└───────────────────────────────────────────────────┘
        All inference billed to Max plan
          (Agent SDK uses Claude Code auth)
```

### How It Works
1. The Claude Agent SDK spawns Claude Code as a subprocess
2. It authenticates via Max plan OAuth (same as `claude` CLI)
3. DeerFlow's tools are exposed as MCP servers that Claude Code connects to
4. The SDK provides streaming, structured output, session management

### Key Agent SDK Features
```python
from claude_agent_sdk import query, ClaudeAgentOptions

# One-off query
async for message in query(
    prompt="Analyze the codebase and fix auth bug",
    options=ClaudeAgentOptions(
        allowed_tools=["Read", "Edit", "Bash", "mcp__deerflow__*"],
        mcp_servers={
            "deerflow": {"type": "stdio", "command": "python", "args": ["-m", "deerflow_mcp"]}
        },
        max_turns=20,
        output_format="stream-json",
    ),
):
    if hasattr(message, "result"):
        print(message.result)
```

### Pros
- **Simplest architecture** — Agent SDK handles the agent loop natively
- All inference on Max plan (SDK uses Claude Code auth)
- Claude Code's built-in tools (Read, Edit, Bash, Glob, Grep, WebSearch) are free
- DeerFlow-specific capabilities exposed via MCP servers
- Streaming, sessions, structured output all handled by SDK
- No Docker overhead for the main agent loop

### Cons
- **Less customizable** — you're locked into Claude Code's agent loop and tool set
- No multi-model support (Claude only, no GPT-4o/Gemini fallbacks)
- Task decomposition/routing relies on Claude's own reasoning (not explicit workflow)
- Agent SDK is relatively new — less mature than Agno/LangGraph
- **Requires `ANTHROPIC_API_KEY` for programmatic use** — the Agent SDK documentation indicates API key auth for headless, which may bill separately from Max plan (needs verification)
- DeerFlow's middleware pattern doesn't map cleanly to MCP servers

---

## Architecture Option C: Lightweight Custom Orchestrator + Claude Code

### Concept
Build a minimal custom Python orchestrator (no framework) that manages task queuing, state, and Docker lifecycle. Dispatch all LLM work to Claude Code in Docker containers. Maximum customizability, minimum framework lock-in.

### Architecture Diagram
```
┌──────────────────────────────────────────────────┐
│                  DeerFlow Frontend                │
│                   (Next.js)                       │
└─────────────────────┬────────────────────────────┘
                      │
┌─────────────────────▼────────────────────────────┐
│              Gateway API (FastAPI)                 │
└─────────────────────┬────────────────────────────┘
                      │
┌─────────────────────▼────────────────────────────┐
│        Custom Python Orchestrator                  │
│                                                    │
│  • Task queue (Redis/in-memory)                    │
│  • State management (SQLite/Postgres)              │
│  • Docker container lifecycle                      │
│  • Result aggregation                              │
│  • MCP server registry                             │
│  • Memory system (reuse from DeerFlow)             │
│  • Middleware pipeline (reuse from DeerFlow)        │
│                                                    │
│  class TaskDispatcher:                             │
│      async def dispatch(self, task) -> Result:     │
│          container = await self.docker.run(         │
│              image="deerflow-worker",              │
│              cmd=["claude", "-p", task.prompt,      │
│                   "--allowedTools", task.tools,     │
│                   "--output-format", "json"],       │
│              volumes={...},                         │
│              mcp_config=task.mcp_servers,           │
│          )                                         │
│          return await container.wait_for_result()   │
│                                                    │
│  class WorkflowEngine:                             │
│      async def run(self, workflow_def):             │
│          for step in workflow_def.steps:            │
│              if step.parallel:                      │
│                  results = await gather(            │
│                      *[self.dispatch(t)             │
│                        for t in step.tasks])        │
│              else:                                  │
│                  result = await self.dispatch(      │
│                      step.task)                     │
└───────────────────────────────────────────────────┘
        │               │               │
   ┌────▼───┐      ┌───▼────┐     ┌───▼────────┐
   │Docker  │      │Docker  │     │Docker      │
   │Claude  │      │Claude  │     │Claude      │
   │Code    │      │Code    │     │Code        │
   │Worker  │      │Worker  │     │Worker      │
   └────────┘      └────────┘     └────────────┘
```

### Pros
- **Maximum customizability** — every aspect is under your control
- All inference on Max plan
- Can reuse DeerFlow's existing middleware, memory, and sandbox code
- No framework lock-in — swap any component independently
- Docker provides natural sandboxing for code execution
- Can add any workflow pattern without framework constraints

### Cons
- More code to write and maintain
- No built-in multi-agent patterns (must build your own)
- Same Docker/auth challenges as Option A
- Need to implement your own streaming, session management, etc.

---

## Claude Code Capabilities Reference

### Headless Mode (`-p` flag)

| Flag | Purpose | Example |
|---|---|---|
| `-p "prompt"` | Run in headless/non-interactive mode | `claude -p "Fix auth bug"` |
| `--output-format` | Output: `text`, `json`, `stream-json` | `--output-format json` |
| `--allowedTools` | Pre-approve tools | `--allowedTools "Read,Edit,Bash"` |
| `--disallowedTools` | Block specific tools | `--disallowedTools "Bash(rm *)"` |
| `--max-turns` | Limit agentic iterations | `--max-turns 10` |
| `--max-budget-usd` | Set spending limit | `--max-budget-usd 5.00` |
| `--mcp-config` | MCP server config file | `--mcp-config ./mcp.json` |
| `--json-schema` | Validate output against schema | `--json-schema '{"type":"object",...}'` |
| `--append-system-prompt` | Add custom instructions | `--append-system-prompt "Always be concise"` |
| `--continue` / `-c` | Continue most recent conversation | `claude -p "next step" -c` |
| `--resume` / `-r` | Resume specific session | `claude -p "continue" -r <session-id>` |
| `--verbose` | Full turn-by-turn output | Shows tool calls and reasoning |

### Built-in Tools (Available in Headless Mode)

| Tool | Description |
|---|---|
| Read | Read any file |
| Write | Create new files |
| Edit | Precise edits to existing files |
| Bash | Run terminal commands |
| Glob | Find files by pattern |
| Grep | Search file contents with regex |
| WebSearch | Search the web |
| WebFetch | Fetch and parse web pages |
| Agent | Spawn subagents for specialized tasks |

### Rate Limits (Max Plan)

| Plan | 5-Hour Burst (Sonnet) | Weekly Cap |
|---|---|---|
| Max $100/month | ~225 messages | ~140-280 hours Sonnet, ~15-35 hours Opus |
| Max $200/month | ~900 messages | ~240-480 hours Sonnet, ~24-40 hours Opus |

### Concurrency Considerations
- Multiple Claude Code instances **share the same Max plan rate limits**
- OAuth token refresh has **no coordination** between instances — known race condition
- Best practices: Use git worktrees for isolation, or serialize Docker container launches
- Agent Teams feature (experimental) coordinates multiple Claude Code instances

---

## Component-by-Component Migration Analysis (Agno)

For completeness, here's the Agno migration analysis if using Option A:

### Components

| Component | Agno Equivalent | Effort | Notes |
|---|---|---|---|
| Lead Agent | `Agent` class | 1-2 days | Straightforward, but LLM calls go through Docker/Claude Code instead of direct API |
| Middleware chain | No equivalent — custom code | 1-2 weeks | **Hardest part.** Must reimplement as pre/post hooks or custom wrappers |
| Subagents | `Team(mode=HIERARCHICAL)` | 2-3 days | Per-member tool restrictions supported |
| Thread State | `session_state` + `SqliteDb` | 2-3 days | Custom reducers need manual impl |
| Tools | Agno built-in + `@tool` decorator | 3-5 days | Most have equivalents (Tavily, DuckDuckGo, Firecrawl, Shell, File) |
| Model Factory | Agno model classes | 1 day | `Claude()`, `OpenAIChat()`, etc. |
| MCP | `MCPTools` / `MultiMCPTools` | 1-2 days | Known bug with HTTP streaming + Claude |
| Memory | Agno `Memory` | 3-5 days | Partial overlap; custom logic still needed |
| Gateway API | Keep existing + adapt | 1 week | AgentOS doesn't cover all 7 routers |
| Sandbox | Custom (no Agno equivalent) | 3-5 days | Must preserve existing code |
| IM Channels | Replace LangGraph SDK calls | 2-3 days | Channels are framework-independent |
| Embedded Client | Adapt to Agno | 1-2 days | |
| **Total** | | **~4-8 weeks** | **High risk** |

---

## Pros and Cons

### Option A: Agno + Claude Code Docker Workers

| Pros | Cons |
|---|---|
| All inference on Max plan | Container startup latency (~2-5s) |
| Agno's clean workflow primitives | OAuth token sharing race condition |
| 80+ built-in tool integrations | Rate limits shared across containers |
| Highly customizable workflows | More complex infrastructure |
| Model-agnostic (Agno can route to different models) | Agno's LLM-based routing needs local model or rules |
| Bidirectional MCP support | 4-8 week migration from current LangGraph |
| Active community (15k+ GitHub stars) | Less mature than LangGraph |

### Option B: Claude Agent SDK Orchestrator

| Pros | Cons |
|---|---|
| Simplest architecture | Least customizable — locked into Claude Code's loop |
| Native Max plan auth | Claude-only (no multi-model) |
| All Claude Code tools built-in | Agent SDK is new, less mature |
| Streaming + sessions handled | DeerFlow middleware pattern doesn't map to MCP |
| No Docker overhead for main loop | May still require API key for programmatic use |
| MCP servers for extensibility | Task routing relies on Claude's reasoning |

### Option C: Custom Orchestrator + Claude Code Docker

| Pros | Cons |
|---|---|
| **Maximum customizability** | More code to write/maintain |
| All inference on Max plan | No built-in multi-agent patterns |
| Reuse existing DeerFlow code | Must build streaming, sessions yourself |
| No framework lock-in | Same Docker/auth challenges as Option A |
| Swap any component independently | |
| Docker = natural sandbox | |

### Cross-Cutting Concerns

| Concern | Impact on All Options |
|---|---|
| **Max plan rate limits** | All concurrent Claude Code instances share the same weekly cap. Heavy use could exhaust limits. |
| **OAuth token race condition** | Multiple containers refreshing the same token simultaneously causes auth failures. Need a token proxy or serialization. |
| **Container cold start** | Docker containers add ~2-5s latency per task dispatch. Can be mitigated with warm pools. |
| **No API key fallback on Max plan** | If Max plan limits are hit, you can't seamlessly fall back to API billing without reconfiguring auth. |
| **Experimental features** | Claude Code Agent Teams and some Agent SDK features are experimental. |

---

## Recommendation

### For Maximum Customizability + Max Plan: Option C (or A)

Given the requirements of:
1. **High customizability** — ability to control every aspect of the workflow
2. **Max plan integration** — all inference billed to the subscription
3. **Docker-based execution** — containers with Claude Code for inference

**Option C (Custom Orchestrator)** is the most flexible. You can:
- Reuse DeerFlow's existing middleware, memory, and sandbox code
- Build exactly the workflow patterns you need
- Add Docker container pooling for performance
- Handle the OAuth token race condition with a custom token proxy
- Add any model/framework later without constraints

**Option A (Agno)** is a good middle ground if you want pre-built workflow primitives (Step, Parallel, Router, Loop, Condition) and team orchestration patterns without building them from scratch. Agno handles state, memory, and tool management, while Docker workers handle inference.

### Suggested Implementation Path

1. **Start with a proof-of-concept**: Build a minimal Docker worker that runs `claude -p` and returns JSON results
2. **Solve the auth problem**: Build a token proxy that serializes OAuth token refreshes across containers
3. **Build the dispatcher**: Python class that manages Docker container lifecycle and result collection
4. **Choose orchestrator**: Try Agno's Workflow primitives; if too constraining, fall back to custom Python
5. **Migrate incrementally**: Port one DeerFlow capability at a time (e.g., web search first, then code execution, then memory)
6. **Keep the frontend/Gateway**: The Next.js frontend and FastAPI gateway can remain largely unchanged

### What NOT to Do
- Don't migrate to Agno just for the framework — it doesn't solve Max plan billing
- Don't run many concurrent Docker containers without rate limit awareness
- Don't share OAuth tokens across containers without a coordination mechanism

---

## Sources

### Agno
- [Agno Official Documentation](https://docs.agno.com)
- [Agno GitHub Repository](https://github.com/agno-agi/agno)
- [Agno Agent Framework](https://www.agno.com/agent-framework)
- [Agno AgentOS](https://www.agno.com/agentos)
- [Agno Anthropic Claude Integration](https://docs.agno.com/models/providers/native/anthropic/overview)
- [Agno MCP Support](https://docs.agno.com/tools/mcp)
- [Agno vs LangGraph - ZenML](https://www.zenml.io/blog/agno-vs-langgraph)
- [Best AI Agent Frameworks 2025 - LangWatch](https://langwatch.ai/blog/best-ai-agent-frameworks-in-2025-comparing-langgraph-dspy-crewai-agno-and-more)
- [Agno Bug: Claude Structured Outputs](https://github.com/agno-agi/agno/issues/2288)
- [Agno Bug: Remote MCP with Claude](https://github.com/agno-agi/agno/issues/4384)

### Claude Code & Max Plan
- [Claude Code CLI Reference](https://code.claude.com/docs/en/cli-reference.md)
- [Claude Code Headless Mode](https://code.claude.com/docs/en/headless.md)
- [Claude Code Authentication](https://code.claude.com/docs/en/authentication.md)
- [Claude Code Permissions](https://code.claude.com/docs/en/permissions.md)
- [Claude Code Dev Containers](https://code.claude.com/docs/en/devcontainer.md)
- [Claude Code on the Web](https://code.claude.com/docs/en/claude-code-on-the-web.md)
- [Claude Code Agent Teams](https://code.claude.com/docs/en/agent-teams.md)
- [Docker Sandboxes for Claude Code](https://www.docker.com/blog/docker-sandboxes-run-claude-code-and-other-coding-agents-unsupervised-but-safely/)

### Claude Agent SDK
- [Agent SDK Overview](https://platform.claude.com/docs/en/agent-sdk/overview)
- [Agent SDK Python Reference](https://platform.claude.com/docs/en/agent-sdk/python)
- [Agent SDK MCP Integration](https://platform.claude.com/docs/en/agent-sdk/mcp)
- [Claude Agent SDK GitHub](https://github.com/anthropics/claude-agent-sdk-python)

### Anthropic
- [Claude Pro/Max Plan](https://support.claude.com/en/articles/11145838-using-claude-code-with-your-pro-or-max-plan)
- [Anthropic API Rate Limits](https://platform.claude.com/docs/en/api/rate-limits)
- [Anthropic API Pricing](https://www.nops.io/blog/anthropic-api-pricing/)
