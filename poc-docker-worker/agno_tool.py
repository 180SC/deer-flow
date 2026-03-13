"""
Agno tool wrapper for ClaudeSandbox.

Exposes Claude Code sandboxed execution as an Agno @tool so any Agno Agent
can invoke it naturally. The agent just says "use claude_sandbox to analyze X"
and the framework handles the rest.

Usage:
    from agno.agent import Agent
    from agno.models.anthropic import Claude
    from agno_tool import claude_sandbox, claude_sandbox_with_files

    agent = Agent(
        model=Claude(id="claude-sonnet-4-20250514"),
        tools=[claude_sandbox, claude_sandbox_with_files],
        instructions=["Use claude_sandbox for tasks requiring code execution or deep analysis."],
    )
    agent.print_response("Analyze the performance of quicksort vs mergesort with benchmarks")
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Optional

# ---------------------------------------------------------------------------
# Agno tool definitions
# ---------------------------------------------------------------------------

# We lazy-init the sandbox so the module can be imported without Docker/token
_sandbox_instance = None


def _get_sandbox():
    """Lazy-init the shared ClaudeSandbox instance."""
    global _sandbox_instance
    if _sandbox_instance is None:
        from sandbox import ClaudeSandbox

        _sandbox_instance = ClaudeSandbox(
            max_concurrent=int(os.getenv("CLAUDE_SANDBOX_MAX_CONCURRENT", "3")),
        )
    return _sandbox_instance


def _run_async(coro):
    """Run an async coroutine from sync context (Agno tools are sync)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # We're inside an existing event loop — use a thread
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor() as pool:
            return pool.submit(asyncio.run, coro).result()
    else:
        return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Tool 1: Simple prompt execution
# ---------------------------------------------------------------------------


def claude_sandbox(
    prompt: str,
    tools: str = "Read,Write,Edit,Bash,Glob,Grep",
    max_turns: int = 15,
    system_prompt: Optional[str] = None,
) -> str:
    """
    Execute a task in an isolated Claude Code sandbox.

    Use this tool when you need to:
    - Run code, scripts, or shell commands safely
    - Analyze files or codebases in depth
    - Perform complex multi-step reasoning with tool access
    - Generate, test, and iterate on code
    - Do web research (add WebSearch,WebFetch to tools)

    The sandbox is a fresh Docker container with Claude Code inside.
    It can read/write files, run bash commands, search code, and more.
    The container is destroyed after execution — fully isolated.

    Args:
        prompt: The task to perform. Be specific about what you want.
        tools: Comma-separated tools to allow. Defaults to safe file/code tools.
                Add "WebSearch,WebFetch" for internet access.
                Use "Read,Glob,Grep" for read-only analysis.
        max_turns: Maximum agentic steps (default 15).
        system_prompt: Optional role/instructions for the sandbox agent.

    Returns:
        The text result from the sandbox execution.
    """
    sandbox = _get_sandbox()
    result = _run_async(
        sandbox.run(
            prompt,
            tools=tools,
            max_turns=max_turns,
            system_prompt=system_prompt,
        )
    )
    if result.success:
        return result.text
    return f"Sandbox error: {result.error}"


# ---------------------------------------------------------------------------
# Tool 2: Execute with file injection
# ---------------------------------------------------------------------------


def claude_sandbox_with_files(
    prompt: str,
    files_json: str,
    tools: str = "Read,Write,Edit,Bash,Glob,Grep",
    max_turns: int = 15,
    system_prompt: Optional[str] = None,
) -> str:
    """
    Execute a task in a Claude Code sandbox with files pre-loaded.

    Use this when you need to pass data or code into the sandbox for analysis.
    Files are written to /workspace/ before execution begins.

    Args:
        prompt: The task to perform.
        files_json: JSON string mapping filenames to content.
                    Example: '{"data.csv": "a,b,c\\n1,2,3", "script.py": "print(42)"}'
        tools: Comma-separated allowed tools.
        max_turns: Maximum agentic steps.
        system_prompt: Optional instructions.

    Returns:
        The text result from the sandbox execution.
    """
    try:
        files = json.loads(files_json)
    except json.JSONDecodeError as e:
        return f"Invalid files_json: {e}"

    if not isinstance(files, dict):
        return "files_json must be a JSON object mapping filenames to content strings."

    sandbox = _get_sandbox()
    result = _run_async(
        sandbox.run(
            prompt,
            tools=tools,
            max_turns=max_turns,
            system_prompt=system_prompt,
            files=files,
        )
    )
    if result.success:
        return result.text
    return f"Sandbox error: {result.error}"


# ---------------------------------------------------------------------------
# Tool 3: Analyze a workspace / codebase
# ---------------------------------------------------------------------------


def claude_sandbox_analyze(
    prompt: str,
    workspace_path: str,
    tools: str = "Read,Glob,Grep",
    max_turns: int = 15,
    system_prompt: Optional[str] = None,
) -> str:
    """
    Analyze an existing codebase or directory in a Claude Code sandbox.

    The workspace directory is mounted read-only into the container at /workspace.
    Use this for code review, security audits, architecture analysis, etc.

    Args:
        prompt: What to analyze. Be specific.
        workspace_path: Absolute path to the directory to mount.
        tools: Comma-separated allowed tools. Defaults to read-only.
        max_turns: Maximum agentic steps.
        system_prompt: Optional role (e.g. "You are a security auditor").

    Returns:
        The analysis result.
    """
    from sandbox import ClaudeSandbox

    sandbox = ClaudeSandbox(
        workspace=workspace_path,
        max_concurrent=1,
    )
    result = _run_async(
        sandbox.run(
            prompt,
            tools=tools,
            max_turns=max_turns,
            system_prompt=system_prompt,
        )
    )
    if result.success:
        return result.text
    return f"Sandbox error: {result.error}"
