# DeerFlow Architecture Blueprint — What to Learn & Reuse

> **Purpose**: Distill the key architectural patterns, design decisions, and reusable ideas from the DeerFlow codebase. Use this as a reference when building your own AI agent platform on Agno + ClaudeSandbox.

---

## 1. What DeerFlow Is

DeerFlow is a **LangGraph-based AI super agent** with a full-stack architecture:

- **Backend**: Python, LangGraph server (port 2024), FastAPI gateway (port 8001)
- **Frontend**: Next.js (port 3000)
- **Proxy**: Nginx (port 2026) unifies everything behind one URL
- **Agent model**: Single "lead agent" that delegates to subagents, uses middleware for cross-cutting concerns, and operates in per-thread isolated environments

The key insight: **DeerFlow solved most of the hard problems** (sandboxing, memory, middleware, tool management, streaming, subagent delegation). We want to lift these patterns and reimplement them on Agno + ClaudeSandbox instead of LangGraph.

---

## 2. Architecture Patterns Worth Stealing

### Pattern 1: Lead Agent + Subagent Delegation

**How DeerFlow does it:**
- One "lead agent" handles all user conversations
- For complex tasks, it delegates via a `task()` tool to background subagents
- Subagents are typed: `general-purpose` (all tools), `bash` (command specialist)
- Dual thread pool: scheduler (3 workers) + execution (3 workers)
- 15-minute timeout, `MAX_CONCURRENT_SUBAGENTS = 3`

**How to reuse with Agno:**
```
User → Agno Lead Agent → claude_sandbox tool → Docker container
                       → claude_sandbox tool → Docker container (parallel)
                       → claude_sandbox tool → Docker container (parallel)
```
The `ClaudeSandbox` abstraction replaces subagents entirely. Each sandbox IS a subagent — it's a full Claude Code instance with tool access. Agno Teams can coordinate multiple sandboxes.

---

### Pattern 2: Middleware Chain for Cross-Cutting Concerns

**DeerFlow's middleware stack (in order):**

| # | Middleware | What it does |
|---|-----------|--------------|
| 1 | ThreadDataMiddleware | Creates per-thread directories for workspace/uploads/outputs |
| 2 | UploadsMiddleware | Tracks uploaded files, injects into conversation |
| 3 | SandboxMiddleware | Acquires sandbox, stores ID in state |
| 4 | DanglingToolCallMiddleware | Handles interrupted tool calls gracefully |
| 5 | SummarizationMiddleware | Compresses context when approaching token limits |
| 6 | TodoListMiddleware | Task tracking for plan mode |
| 7 | TitleMiddleware | Auto-generates thread title after first exchange |
| 8 | MemoryMiddleware | Queues conversations for async memory extraction |
| 9 | ViewImageMiddleware | Injects base64 images before LLM call |
| 10 | SubagentLimitMiddleware | Caps concurrent subagent calls |
| 11 | ClarificationMiddleware | Interrupts flow to ask user for input |

**How to reuse with Agno:**
Agno doesn't have a middleware system, but you can replicate this with:
- **Pre/post hooks** on Agno Agent `instructions` or custom wrapper functions
- **Agno Sessions** for thread-level state (title, memory, uploads)
- **ClaudeSandbox** replaces SandboxMiddleware entirely (container IS the sandbox)
- **Memory** can be an MCP server that the sandbox calls

---

### Pattern 3: Virtual Path System

**The problem:** Agents need file access, but you don't want them touching real paths.

**DeerFlow's solution:**
- Agent sees: `/mnt/user-data/{workspace,uploads,outputs}`, `/mnt/skills`
- Physical: `backend/.deer-flow/threads/{thread_id}/user-data/...`
- Translation functions: `replace_virtual_path()` / `replace_virtual_paths_in_command()`

**How to reuse with ClaudeSandbox:**
Docker already solves this. The container only sees `/workspace`. You control what gets mounted:
```python
sandbox = ClaudeSandbox(workspace="/real/path/to/thread/data")
# Agent inside sees /workspace, can't escape
```

---

### Pattern 4: Memory System with Fact Extraction

**DeerFlow's memory architecture:**
- Stored in `memory.json` with structure:
  - **User Context**: `workContext`, `personalContext`, `topOfMind`
  - **History**: `recentMonths`, `earlierContext`, `longTermBackground`
  - **Facts**: Array of `{id, content, category, confidence, createdAt, source}`
- Categories: `preference`, `knowledge`, `context`, `behavior`, `goal`
- **Debounced**: Waits 30s, batches, deduplicates per-thread before LLM extraction
- **Injection**: Top 15 facts + context injected into system prompt via `<memory>` tags
- **Atomic writes**: temp file + rename for crash safety

**How to reuse:**
Build this as an **MCP server** that ClaudeSandbox can call:
```python
# The sandbox gets these tools:
# - mcp__memory__search_facts(query) → relevant facts
# - mcp__memory__save_fact(content, category, confidence)
# - mcp__memory__get_context() → user context summary
```
Or implement as an Agno Knowledge/Storage backend.

---

### Pattern 5: Tool System with Dynamic Loading

**DeerFlow assembles tools from 5 sources:**
1. **Config-defined** — resolved from `config.yaml` via `resolve_variable(module:var)`
2. **MCP tools** — from enabled MCP servers (lazy init, cached with mtime invalidation)
3. **Built-in tools** — `present_files`, `ask_clarification`, `view_image`
4. **Community tools** — Tavily search, Jina reader, Firecrawl, DuckDuckGo images
5. **Subagent tool** — `task()` for delegation

**How to reuse with Agno:**
- Agno has `@tool` decorators — use these for built-in tools
- MCP servers work natively with ClaudeSandbox (`--mcp-config`)
- Community tools → just pass `WebSearch,WebFetch` to the sandbox
- The `claude_sandbox` Agno tool IS the subagent tool

---

### Pattern 6: Extensions Config (MCP + Skills)

**DeerFlow's `extensions_config.json`:**
```json
{
  "mcpServers": {
    "github": {
      "enabled": true,
      "type": "stdio",
      "command": "npx",
      "args": ["@modelcontextprotocol/server-github"],
      "env": {"GITHUB_TOKEN": "$GITHUB_TOKEN"}
    }
  },
  "skills": {
    "web-research": {"enabled": true}
  }
}
```
- Hot-reloadable via Gateway API
- Cache invalidation via file mtime
- OAuth support for HTTP/SSE MCP servers

**How to reuse:**
Pass MCP configs directly to ClaudeSandbox:
```python
result = await sandbox.run(
    "List open issues",
    mcp_config={"mcpServers": {...}},
    tools="mcp__github__*",
)
```

---

### Pattern 7: Streaming Architecture

**DeerFlow's streaming flow:**
```
LangGraph Agent → SSE events → Nginx proxy → Frontend EventSource
```
Event types:
- `values` — full state snapshot (title, messages, artifacts)
- `messages-tuple` — per-message update (AI text, tool calls, results)
- `end` — stream finished

**How to reuse with ClaudeSandbox:**
```
ClaudeSandbox (--output-format stream-json)
  → line-delimited JSON on stdout
  → StreamingDispatcher yields events
  → FastAPI SSE endpoint
  → Frontend EventSource
```

---

### Pattern 8: Gateway API as Separate Service

**DeerFlow separates concerns:**
- LangGraph server = agent runtime (stateful, long-running)
- Gateway API = everything else (models, MCP, skills, memory, uploads, artifacts)
- Nginx routes: `/api/langgraph/*` → LangGraph, `/api/*` → Gateway

**How to reuse:**
Keep this pattern. Your gateway handles:
- Model management
- MCP server configuration
- Memory CRUD
- File uploads
- Artifact serving
- Sandbox pool status

The Agno agents + ClaudeSandbox replace the LangGraph server.

---

### Pattern 9: Thread Isolation

**DeerFlow creates per-thread directories:**
```
backend/.deer-flow/threads/{thread_id}/
  ├── user-data/
  │   ├── workspace/   # Agent working directory
  │   ├── uploads/     # User-uploaded files
  │   └── outputs/     # Agent-generated artifacts
```

**How to reuse:**
Each ClaudeSandbox invocation mounts the thread's workspace:
```python
sandbox = ClaudeSandbox(
    workspace=f"/data/threads/{thread_id}/workspace"
)
```
Thread state lives on disk. Containers are ephemeral.

---

### Pattern 10: Embedded Client

**DeerFlow's `DeerFlowClient`:**
- Same code as the server, but callable in-process (no HTTP)
- `chat()`, `stream()`, plus all gateway methods
- Useful for testing, scripting, and embedding

**How to reuse:**
Build your `AgnoClient` that wraps the sandbox:
```python
client = AgnoClient()
result = client.chat("Analyze my data", thread_id="abc123")
# Under the hood: creates sandbox, mounts thread workspace, runs, returns
```

---

## 3. What NOT to Carry Over

| DeerFlow Component | Why Skip It |
|---|---|
| LangGraph dependency | Replaced by Agno Workflows + ClaudeSandbox |
| LangChain tool wrappers | Replaced by Agno `@tool` + MCP |
| Custom middleware framework | Overkill; use Agno hooks + pre/post functions |
| Local sandbox provider | Docker containers are the sandbox now |
| Subagent executor thread pools | ClaudeSandbox handles this (Docker manages concurrency) |
| Custom checkpointer | Use Agno sessions or a simple KV store |

---

## 4. Minimal Viable Architecture

```
┌──────────────────────────────────────────────────────┐
│                     Frontend (Next.js)                │
│                    (reuse as-is or rebuild)           │
└──────────────────────┬───────────────────────────────┘
                       │ SSE / REST
┌──────────────────────┴───────────────────────────────┐
│                  Gateway API (FastAPI)                │
│  /api/chat  /api/research  /api/memory  /api/models  │
└──────┬───────────┬──────────────┬────────────────────┘
       │           │              │
       ▼           ▼              ▼
┌────────────┐ ┌────────┐ ┌─────────────────┐
│ Agno Agent │ │ Memory │ │ Config/Models   │
│ (router)   │ │ (MCP)  │ │ (YAML + DB)     │
└─────┬──────┘ └────────┘ └─────────────────┘
      │
      │ claude_sandbox tool
      ▼
┌─────────────────────────────────┐
│      ClaudeSandbox Pool         │
│  ┌─────────┐ ┌─────────┐       │
│  │Container│ │Container│  ...   │
│  │Claude   │ │Claude   │       │
│  │Code     │ │Code     │       │
│  └─────────┘ └─────────┘       │
│  (Max plan auth, isolated fs)   │
└─────────────────────────────────┘
```

---

## 5. File-by-File Reference

Key DeerFlow files to study when building your own:

| What to Learn | DeerFlow File | Key Takeaway |
|---|---|---|
| Agent creation | `backend/src/agents/lead_agent/agent.py` | How to compose model + tools + middleware |
| System prompts | `backend/src/agents/lead_agent/prompt.py` | Dynamic prompt with skills, memory, subagent instructions |
| Thread state | `backend/src/agents/thread_state.py` | State schema with custom reducers |
| Memory extraction | `backend/src/agents/memory/updater.py` | LLM-based fact extraction + atomic writes |
| Memory prompts | `backend/src/agents/memory/prompt.py` | How to prompt for memory updates |
| Sandbox interface | `backend/src/sandbox/sandbox.py` | Abstract sandbox contract |
| Sandbox tools | `backend/src/sandbox/tools.py` | bash, ls, read/write/str_replace |
| Tool assembly | `backend/src/tools/tools.py` | Dynamic tool loading from 5 sources |
| Gateway app | `backend/src/gateway/app.py` | FastAPI lifespan, router registration |
| Gateway routes | `backend/src/gateway/routers/` | REST API patterns for all features |
| MCP integration | `backend/src/mcp/` | Multi-server MCP with caching |
| Subagent executor | `backend/src/subagents/executor.py` | Background task execution pattern |
| Config system | `backend/src/config/` | YAML + env var resolution |
| Embedded client | `backend/src/client.py` | In-process API matching HTTP API |
| Middleware examples | `backend/src/agents/middlewares/` | Each middleware is a pattern to learn from |

---

## 6. Recommended Build Order

If building from scratch using these patterns:

1. **ClaudeSandbox** (done — `poc-docker-worker/sandbox.py`)
2. **Agno tool wrapper** (done — `poc-docker-worker/agno_tool.py`)
3. **Gateway API** skeleton (FastAPI, model/config routes)
4. **Memory MCP server** (port DeerFlow's fact extraction)
5. **Agno Lead Agent** with `claude_sandbox` tool
6. **Thread workspace management** (per-thread dirs, mount to sandbox)
7. **Streaming relay** (sandbox → gateway → frontend SSE)
8. **Extensions config** (MCP servers, skills, hot-reload)
9. **Frontend** (reuse DeerFlow's or rebuild)
10. **Observability** (logging, metrics, trace IDs)
