# DeerFlow Docker Worker — Proof of Concept

Dispatches tasks to Claude Code running inside Docker containers. All inference
is billed to your Anthropic Max plan via `CLAUDE_CODE_OAUTH_TOKEN`.

## Quick Start

### 1. Build the worker image

```bash
cd poc-docker-worker
docker compose build
```

### 2. Generate an OAuth token

In a separate terminal (requires browser):

```bash
claude setup-token
# Copy the token: sk-ant-oat01-...
```

### 3. Configure

```bash
cp .env.example .env
# Edit .env and paste your token
```

### 4. Test manually

```bash
# Quick sanity check
docker compose run worker -p "What is 2+2?" --output-format json --max-turns 1

# With tools
docker compose run worker -p "Run 'uname -a'" --output-format json --allowedTools Bash --max-turns 3
```

### 5. Run the demo

```bash
pip install python-dotenv  # optional, for .env loading
python demo.py
```

## Architecture

```
┌──────────────────────────┐
│    dispatcher.py          │  Python orchestrator
│    (Dispatcher class)     │  Manages Docker lifecycle
└────────┬─────────────────┘
         │ docker run
    ┌────▼────┐  ┌─────────┐  ┌─────────┐
    │Container│  │Container│  │Container│  Claude Code workers
    │claude -p│  │claude -p│  │claude -p│  (Max plan auth)
    └─────────┘  └─────────┘  └─────────┘
```

## Dispatcher API

```python
from dispatcher import Dispatcher, fan_out_fan_in, sequential

dispatcher = Dispatcher(max_concurrent=3)

# Single task
result = await dispatcher.run("Summarize this code", allowed_tools="Read,Bash")

# Parallel tasks
results = await dispatcher.run_parallel(["task 1", "task 2", "task 3"])

# Fan-out / fan-in
result = await fan_out_fan_in(dispatcher, subtasks=[...], aggregation_prompt="...")

# Sequential chain
results = await sequential(dispatcher, ["step 1", "step 2", "step 3"])

# Streaming
streaming = StreamingDispatcher()
async for event in streaming.run_stream("your prompt"):
    print(event)
```

## Files

| File | Purpose |
|---|---|
| `Dockerfile` | Claude Code worker image |
| `docker-compose.yml` | Service definition with auth and resource limits |
| `dispatcher.py` | Python dispatcher — spawns and manages Docker containers |
| `demo.py` | Demo script showing all dispatch patterns |
| `.env.example` | Template for OAuth token configuration |
