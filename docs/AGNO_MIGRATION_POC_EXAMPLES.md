# DeerFlow Migration — POC Examples & Patterns

> Reference examples for the Claude Code Docker Worker architecture and Agno integration patterns. See `poc-docker-worker/` for runnable code.

---

## 1. Docker Worker — Basic Setup

### Dockerfile

```dockerfile
FROM node:20-slim

RUN npm install -g @anthropic-ai/claude-code

RUN apt-get update && apt-get install -y git python3 curl && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

RUN useradd -m worker
USER worker
WORKDIR /workspace

ENV CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
ENTRYPOINT ["claude"]
```

### Authentication

```bash
# One-time: generate a 1-year OAuth token (requires browser)
claude setup-token
# → sk-ant-oat01-...

# Pass to containers via environment variable
docker run --rm \
  -e CLAUDE_CODE_OAUTH_TOKEN="sk-ant-oat01-..." \
  -v $(pwd):/workspace \
  deerflow-claude-worker:latest \
  -p "What is 2+2?" --output-format json --max-turns 1
```

---

## 2. Dispatcher — Single Task

```python
from dispatcher import Dispatcher

dispatcher = Dispatcher(max_concurrent=3)

# Simple inference
result = await dispatcher.run(
    "Explain the difference between REST and GraphQL. Be concise.",
    allowed_tools="",
    max_turns=1,
)
print(result.text)
```

---

## 3. Dispatcher — Parallel Tasks

```python
results = await dispatcher.run_parallel(
    [
        "Summarize the latest Python 3.13 features.",
        "Summarize the latest Node.js 22 features.",
        "Summarize the latest Rust 1.80 features.",
    ],
    allowed_tools="WebSearch",
    max_turns=5,
)

for i, r in enumerate(results):
    print(f"Task {i+1}: {r.text[:200]}")
```

---

## 4. Dispatcher — Fan-Out / Fan-In (Research Pattern)

```python
from dispatcher import Dispatcher, fan_out_fan_in

dispatcher = Dispatcher(max_concurrent=5)

result = await fan_out_fan_in(
    dispatcher,
    subtasks=[
        "Research Agno framework strengths for multi-agent systems.",
        "Research LangGraph strengths for stateful workflows.",
        "Research CrewAI strengths for team-based agents.",
    ],
    aggregation_prompt=(
        "You are a technical architect. Based on the research below, "
        "write a 5-bullet comparison recommending which framework to "
        "use for a production AI agent platform."
    ),
    allowed_tools="WebSearch,WebFetch",
    max_turns=5,
)

print(result.text)
```

---

## 5. Dispatcher — Sequential Chain (Pipeline)

```python
from dispatcher import Dispatcher, sequential

dispatcher = Dispatcher(max_concurrent=1)

results = await sequential(
    dispatcher,
    [
        "Analyze the DeerFlow codebase at /workspace and list the top 5 files by complexity.",
        "For each of those files, suggest one refactoring improvement.",
        "Write a summary report of all suggested refactorings in markdown format.",
    ],
    allowed_tools="Read,Glob,Grep,Bash",
    max_turns=10,
    workspace_path="/path/to/deer-flow/backend",
)

# Final result has the aggregated report
print(results[-1].text)
```

---

## 6. Dispatcher — Streaming Output

```python
from dispatcher import StreamingDispatcher

streaming = StreamingDispatcher(max_concurrent=1)

async for event in streaming.run_stream(
    "Write a haiku about Docker containers.",
    allowed_tools="",
    max_turns=1,
):
    event_type = event.get("type", "unknown")
    if event_type == "stream_event":
        delta = event.get("event", {}).get("delta", {})
        if delta.get("type") == "text_delta":
            print(delta.get("text", ""), end="", flush=True)
    elif event_type == "result":
        print(f"\n[Done. Session: {event.get('session_id', 'N/A')}]")
```

---

## 7. Dispatcher — With MCP Servers

```python
import json
import tempfile

# Create an MCP config for the worker container
mcp_config = {
    "mcpServers": {
        "filesystem": {
            "type": "stdio",
            "command": "npx",
            "args": ["@modelcontextprotocol/server-filesystem", "/workspace"]
        },
        "github": {
            "type": "stdio",
            "command": "npx",
            "args": ["@modelcontextprotocol/server-github"],
            "env": {"GITHUB_TOKEN": "ghp_..."}
        }
    }
}

# Write config to a temp file, pass to dispatcher
with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
    json.dump(mcp_config, f)
    mcp_path = f.name

result = await dispatcher.run(
    "List all open issues in the deer-flow repo.",
    mcp_config_path=mcp_path,
    allowed_tools="mcp__github__*",
    max_turns=5,
)
```

---

## 8. Dispatcher — With Custom System Prompt

```python
result = await dispatcher.run(
    "Review the authentication module for security vulnerabilities.",
    system_prompt=(
        "You are a senior security engineer specializing in Python web applications. "
        "Focus on OWASP Top 10 vulnerabilities. Be thorough but concise."
    ),
    allowed_tools="Read,Glob,Grep",
    max_turns=15,
    workspace_path="/path/to/deer-flow/backend",
)
```

---

## 9. Agno Orchestrator — Workflow with Docker Workers

This shows how Agno's Workflow primitives can orchestrate Docker workers:

```python
from agno.agent import Agent
from agno.workflow import Workflow, Step, Parallel
from agno.tools import tool
from agno.models.openai import OpenAIChat  # Use cheap model for routing
from dispatcher import Dispatcher

dispatcher = Dispatcher(max_concurrent=3)

# Custom Agno tool that dispatches to Claude Code Docker worker
@tool
def claude_worker(prompt: str, tools: str = "Read,Bash", max_turns: int = 10) -> str:
    """Dispatch a task to a Claude Code Docker worker (Max plan)."""
    import asyncio
    result = asyncio.run(dispatcher.run(
        prompt, allowed_tools=tools, max_turns=max_turns
    ))
    if result.success:
        return result.text
    return f"Error: {result.error}"

# Agno agent that routes to Docker workers
router = Agent(
    model=OpenAIChat(id="gpt-4o-mini"),  # Cheap model for routing decisions
    tools=[claude_worker],
    instructions=[
        "You are a task router. Break complex requests into subtasks.",
        "Use claude_worker to dispatch each subtask.",
        "Aggregate results into a final answer.",
    ],
)

# Or use explicit Agno Workflows for deterministic orchestration
research_workflow = Workflow(
    name="Research Pipeline",
    steps=[
        Parallel(steps=[
            Step(function=lambda: asyncio.run(dispatcher.run("Research topic A"))),
            Step(function=lambda: asyncio.run(dispatcher.run("Research topic B"))),
        ]),
        Step(function=lambda ctx: asyncio.run(dispatcher.run(
            f"Synthesize these findings: {ctx.results}"
        ))),
    ],
)
```

---

## 10. Claude Agent SDK — Direct Integration (No Docker)

For when you don't need Docker isolation:

```python
from claude_agent_sdk import query, ClaudeAgentOptions, tool, create_sdk_mcp_server
import json

# DeerFlow memory as an in-process MCP tool
@tool("search_memory", "Search DeerFlow agent memory", {"query": str})
async def search_memory(args):
    from src.agents.memory.memory_manager import MemoryManager
    manager = MemoryManager()
    facts = manager.search(args["query"], limit=5)
    return {"content": [{"type": "text", "text": json.dumps(facts)}]}

@tool("save_memory", "Save a fact to DeerFlow memory", {"fact": str, "confidence": float})
async def save_memory(args):
    from src.agents.memory.memory_manager import MemoryManager
    manager = MemoryManager()
    manager.add_fact(args["fact"], confidence=args["confidence"])
    return {"content": [{"type": "text", "text": "Fact saved."}]}

memory_server = create_sdk_mcp_server(
    name="deerflow_memory",
    tools=[search_memory, save_memory],
)

# Run with Max plan auth — no API key needed
async for message in query(
    prompt="What do you remember about the user's preferences?",
    options=ClaudeAgentOptions(
        allowed_tools=[
            "Read", "Bash",
            "mcp__deerflow_memory__search_memory",
            "mcp__deerflow_memory__save_memory",
        ],
        mcp_servers={"deerflow_memory": memory_server},
        max_turns=5,
    ),
):
    if hasattr(message, "result"):
        print(message.result)
```

---

## 11. Gateway API Integration

How the existing FastAPI gateway would call the dispatcher:

```python
from fastapi import FastAPI, BackgroundTasks
from dispatcher import Dispatcher, Task

app = FastAPI()
dispatcher = Dispatcher(max_concurrent=5)

@app.post("/api/chat")
async def chat(request: ChatRequest):
    """Send a message, get a response from Claude Code worker."""
    result = await dispatcher.run(
        prompt=request.message,
        allowed_tools=request.tools or "Read,Bash,WebSearch",
        max_turns=request.max_turns or 15,
        workspace_path=f"/workspaces/{request.thread_id}",
        system_prompt=request.system_prompt,
    )
    return {"response": result.text, "session_id": result.session_id}

@app.post("/api/chat/stream")
async def chat_stream(request: ChatRequest):
    """Stream a response from Claude Code worker."""
    from sse_starlette.sse import EventSourceResponse
    from dispatcher import StreamingDispatcher

    streaming = StreamingDispatcher(max_concurrent=5)

    async def event_generator():
        async for event in streaming.run_stream(
            prompt=request.message,
            allowed_tools=request.tools or "Read,Bash,WebSearch",
        ):
            yield {"data": json.dumps(event)}

    return EventSourceResponse(event_generator())

@app.post("/api/research")
async def research(request: ResearchRequest):
    """Fan-out/fan-in research pattern."""
    from dispatcher import fan_out_fan_in

    result = await fan_out_fan_in(
        dispatcher,
        subtasks=request.subtasks,
        aggregation_prompt=request.aggregation_prompt,
        allowed_tools="WebSearch,WebFetch,Read",
    )
    return {"response": result.text}
```

---

## File Reference

| File | Location | Purpose |
|---|---|---|
| `Dockerfile` | `poc-docker-worker/Dockerfile` | Claude Code worker image |
| `docker-compose.yml` | `poc-docker-worker/docker-compose.yml` | Service config with auth and limits |
| `dispatcher.py` | `poc-docker-worker/dispatcher.py` | Async dispatcher (Dispatcher, StreamingDispatcher) |
| `demo.py` | `poc-docker-worker/demo.py` | 6 runnable demo scenarios |
| `.env.example` | `poc-docker-worker/.env.example` | Token config template |
