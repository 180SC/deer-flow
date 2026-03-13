# DeerFlow → Agno Migration Plan

> **Purpose**: Phased migration schedule for porting DeerFlow from LangGraph to Agno + Claude Code Docker Workers.
> **Format**: Designed for Linear import (CSV below) and LLM consumption.
> **Total Duration**: ~8 weeks (can compress to 6 with parallel work)

---

## Phase Overview

| Phase | Name | Duration | Dependencies |
|-------|------|----------|--------------|
| 0 | Foundation & Infrastructure | Week 1–2 | None |
| 1 | Core Agent Migration | Week 3–4 | Phase 0 |
| 2 | Orchestration & Workflows | Week 5–6 | Phase 1 |
| 3 | Integration, Testing & Cutover | Week 7–8 | Phase 2 |

---

## Phase 0: Foundation & Infrastructure (Week 1–2)

**Goal**: Docker worker infrastructure running, Agno installed, CI green.

| # | Task | Priority | Estimate | Labels |
|---|------|----------|----------|--------|
| 0.1 | Finalize Claude Code Docker image (Dockerfile, entrypoint, health check) | Urgent | 2d | infra, docker |
| 0.2 | Set up OAuth token management (generate, rotate, vault storage) | Urgent | 1d | infra, auth |
| 0.3 | Build production Dispatcher (async pool, retries, timeout, error handling) | Urgent | 3d | infra, core |
| 0.4 | Build StreamingDispatcher (SSE/WebSocket relay from Docker workers) | High | 2d | infra, streaming |
| 0.5 | Add Agno as dependency, verify it installs alongside existing stack | High | 0.5d | infra, agno |
| 0.6 | Set up docker-compose for local dev (worker pool, resource limits, volumes) | High | 1d | infra, docker |
| 0.7 | Write integration tests for Dispatcher (mock + live worker) | Medium | 1d | testing |
| 0.8 | Add CI pipeline step for building/testing Docker worker image | Medium | 1d | ci |

---

## Phase 1: Core Agent Migration (Week 3–4)

**Goal**: Individual DeerFlow agents ported to Agno Agents backed by Claude Code workers.

| # | Task | Priority | Estimate | Labels |
|---|------|----------|----------|--------|
| 1.1 | Port `researcher` agent → Agno Agent with `claude_worker` tool | Urgent | 2d | agent, migration |
| 1.2 | Port `coder` agent → Agno Agent with `claude_worker` tool | Urgent | 2d | agent, migration |
| 1.3 | Port `reporter` agent → Agno Agent (writing/synthesis) | High | 1d | agent, migration |
| 1.4 | Port `planner` agent → Agno Agent (task decomposition) | High | 1d | agent, migration |
| 1.5 | Port tool definitions (web search, code exec, file ops) → Agno `@tool` or MCP | High | 2d | tools, migration |
| 1.6 | Migrate memory/knowledge system → Agno Knowledge or MCP server | High | 2d | memory, migration |
| 1.7 | Write per-agent integration tests (input→output contract tests) | Medium | 2d | testing |
| 1.8 | Benchmark agent latency & cost: Agno+Docker vs current LangGraph | Medium | 1d | perf |

---

## Phase 2: Orchestration & Workflows (Week 5–6)

**Goal**: Multi-agent coordination via Agno Teams/Workflows replacing LangGraph graphs.

| # | Task | Priority | Estimate | Labels |
|---|------|----------|----------|--------|
| 2.1 | Build research workflow (fan-out/fan-in) using Agno Workflow | Urgent | 2d | workflow, migration |
| 2.2 | Build coding workflow (plan→code→review→test) using Agno Workflow | Urgent | 2d | workflow, migration |
| 2.3 | Implement Agno Team for dynamic multi-agent routing | High | 2d | orchestration |
| 2.4 | Port human-in-the-loop / approval gates to Agno sessions | High | 1d | workflow, ux |
| 2.5 | Wire Agno workflows into existing FastAPI gateway (`/api/chat`, `/api/research`) | High | 2d | api, integration |
| 2.6 | Implement streaming relay (worker → gateway → frontend SSE) | High | 2d | streaming, api |
| 2.7 | Add session/state persistence (Agno sessions or external store) | Medium | 2d | state, infra |
| 2.8 | End-to-end workflow tests (research, coding, report generation) | Medium | 2d | testing |

---

## Phase 3: Integration, Testing & Cutover (Week 7–8)

**Goal**: Feature parity with current system, frontend connected, production-ready.

| # | Task | Priority | Estimate | Labels |
|---|------|----------|----------|--------|
| 3.1 | Connect Next.js frontend to new Agno-backed API endpoints | Urgent | 2d | frontend, integration |
| 3.2 | Feature-parity smoke tests (compare old vs new on 10 reference queries) | Urgent | 1d | testing, qa |
| 3.3 | Implement graceful fallback: if Docker worker fails, retry or degrade | High | 1d | reliability |
| 3.4 | Add observability (structured logs, metrics, trace IDs per worker) | High | 2d | observability |
| 3.5 | Load test: concurrent users → worker pool scaling | High | 1d | perf |
| 3.6 | Security audit: container isolation, token scoping, network policies | High | 1d | security |
| 3.7 | Write migration runbook (rollback plan, feature flags, traffic shifting) | Medium | 1d | docs |
| 3.8 | Remove LangGraph dependency and dead code | Medium | 1d | cleanup |
| 3.9 | Final cutover: switch default backend, monitor for 48h | Medium | 1d | release |

---

## What Migrates vs. What Gets Rebuilt

| Current Component | Migration Strategy |
|---|---|
| LangGraph state graphs | **Replace** → Agno Workflows + Teams |
| LangChain tool wrappers | **Replace** → Agno `@tool` decorators or MCP servers |
| Agent prompt templates | **Port** → Agno Agent `instructions` (minimal changes) |
| Memory / RAG | **Port** → Agno Knowledge or custom MCP memory server |
| FastAPI gateway | **Keep** → Update route handlers to call Dispatcher/Agno |
| Next.js frontend | **Keep** → Update API contract if needed |
| Streaming (SSE) | **Adapt** → Relay from Docker worker `--output-format stream-json` |
| Configuration (YAML) | **Keep** → Map to Agno agent/workflow config |

---

## What Stays on LangGraph (Not Worth Migrating)

- Nothing critical — full migration is feasible
- LangGraph can remain as a fallback during Phase 3 via feature flag

---

## Risk Register

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Max plan rate limits under load | Medium | High | Worker pool sizing, queue backpressure, caching |
| Docker worker cold-start latency | Medium | Medium | Pre-warm pool, keep-alive containers |
| Agno breaking changes (pre-1.0) | Low | Medium | Pin version, vendor if needed |
| OAuth token expiry in production | Low | High | Rotation automation, monitoring alerts |
| Feature parity gaps discovered late | Medium | High | Phase 1 contract tests catch this early |

---

## Linear CSV Import

Copy the block below into a `.csv` file and import into Linear via **Settings → Import → CSV**.

```csv
Title,Description,Priority,Estimate,Labels,Project,Status
"[P0] Finalize Claude Code Docker image","Dockerfile, entrypoint, health check for Claude Code worker container",Urgent,2,infra;docker,DeerFlow Agno Migration,Backlog
"[P0] Set up OAuth token management","Generate Max plan OAuth token, configure vault storage, rotation policy",Urgent,1,infra;auth,DeerFlow Agno Migration,Backlog
"[P0] Build production Dispatcher","Async worker pool with retries, timeout, error handling, concurrency limits",Urgent,3,infra;core,DeerFlow Agno Migration,Backlog
"[P0] Build StreamingDispatcher","SSE/WebSocket relay from Docker worker stream-json output",High,2,infra;streaming,DeerFlow Agno Migration,Backlog
"[P0] Add Agno dependency","Install agno, verify compatibility with existing DeerFlow dependencies",High,1,infra;agno,DeerFlow Agno Migration,Backlog
"[P0] Docker-compose local dev setup","Worker pool, resource limits, volume mounts, env config",High,1,infra;docker,DeerFlow Agno Migration,Backlog
"[P0] Dispatcher integration tests","Mock and live worker tests for Dispatcher and StreamingDispatcher",Medium,1,testing,DeerFlow Agno Migration,Backlog
"[P0] CI pipeline for Docker worker","Build, test, and cache Docker worker image in CI",Medium,1,ci,DeerFlow Agno Migration,Backlog
"[P1] Port researcher agent to Agno","Migrate researcher agent to Agno Agent with claude_worker tool dispatch",Urgent,2,agent;migration,DeerFlow Agno Migration,Backlog
"[P1] Port coder agent to Agno","Migrate coder agent to Agno Agent with claude_worker tool dispatch",Urgent,2,agent;migration,DeerFlow Agno Migration,Backlog
"[P1] Port reporter agent to Agno","Migrate reporter/writing agent to Agno Agent",High,1,agent;migration,DeerFlow Agno Migration,Backlog
"[P1] Port planner agent to Agno","Migrate planner/decomposition agent to Agno Agent",High,1,agent;migration,DeerFlow Agno Migration,Backlog
"[P1] Port tools to Agno/@tool or MCP","Migrate web search, code exec, file ops tools to Agno @tool decorators or MCP",High,2,tools;migration,DeerFlow Agno Migration,Backlog
"[P1] Migrate memory/knowledge system","Port memory/RAG to Agno Knowledge or custom MCP memory server",High,2,memory;migration,DeerFlow Agno Migration,Backlog
"[P1] Per-agent integration tests","Contract tests for each migrated agent (input→output validation)",Medium,2,testing,DeerFlow Agno Migration,Backlog
"[P1] Benchmark agent latency and cost","Compare Agno+Docker vs current LangGraph on latency, token cost, reliability",Medium,1,perf,DeerFlow Agno Migration,Backlog
"[P2] Build research workflow","Fan-out/fan-in research pattern using Agno Workflow",Urgent,2,workflow;migration,DeerFlow Agno Migration,Backlog
"[P2] Build coding workflow","Plan→code→review→test pipeline using Agno Workflow",Urgent,2,workflow;migration,DeerFlow Agno Migration,Backlog
"[P2] Implement Agno Team routing","Dynamic multi-agent coordination via Agno Team",High,2,orchestration,DeerFlow Agno Migration,Backlog
"[P2] Port human-in-the-loop gates","Approval gates and human checkpoints in Agno sessions",High,1,workflow;ux,DeerFlow Agno Migration,Backlog
"[P2] Wire workflows into FastAPI gateway","Connect Agno workflows to /api/chat and /api/research endpoints",High,2,api;integration,DeerFlow Agno Migration,Backlog
"[P2] Implement streaming relay","End-to-end streaming: Docker worker → gateway → frontend SSE",High,2,streaming;api,DeerFlow Agno Migration,Backlog
"[P2] Session/state persistence","Persist workflow state via Agno sessions or external store",Medium,2,state;infra,DeerFlow Agno Migration,Backlog
"[P2] End-to-end workflow tests","Full workflow tests for research, coding, and report generation",Medium,2,testing,DeerFlow Agno Migration,Backlog
"[P3] Connect frontend to new API","Wire Next.js frontend to Agno-backed API endpoints",Urgent,2,frontend;integration,DeerFlow Agno Migration,Backlog
"[P3] Feature-parity smoke tests","Compare old vs new system on 10 reference queries",Urgent,1,testing;qa,DeerFlow Agno Migration,Backlog
"[P3] Graceful fallback on worker failure","Retry logic, degradation path if Docker worker fails",High,1,reliability,DeerFlow Agno Migration,Backlog
"[P3] Add observability","Structured logs, metrics, trace IDs per worker invocation",High,2,observability,DeerFlow Agno Migration,Backlog
"[P3] Load test worker pool","Concurrent user simulation, pool scaling validation",High,1,perf,DeerFlow Agno Migration,Backlog
"[P3] Security audit","Container isolation, token scoping, network policies review",High,1,security,DeerFlow Agno Migration,Backlog
"[P3] Write migration runbook","Rollback plan, feature flags, traffic shifting documentation",Medium,1,docs,DeerFlow Agno Migration,Backlog
"[P3] Remove LangGraph dependency","Clean up LangGraph imports, dead code, unused configs",Medium,1,cleanup,DeerFlow Agno Migration,Backlog
"[P3] Final cutover","Switch default backend to Agno, monitor for 48 hours",Medium,1,release,DeerFlow Agno Migration,Backlog
```

---

## How to Import into Linear

1. Copy the CSV block above into a file called `deerflow-migration.csv`
2. In Linear: **Settings → Import/Export → Import → CSV**
3. Map columns: Title, Description, Priority, Estimate (points), Labels, Project
4. All tasks will appear in **Backlog** — triage into sprints by phase prefix (`[P0]`, `[P1]`, etc.)
5. Create cycles/sprints matching the 2-week phases

Alternatively, paste this entire markdown document into an LLM and ask it to generate Linear API calls or adjust the schedule to your team size.
