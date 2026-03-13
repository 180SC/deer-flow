"""
DeerFlow Claude Code Docker Dispatcher (Proof of Concept)

Dispatches tasks to Claude Code running inside Docker containers.
All inference is billed to the Anthropic Max plan via CLAUDE_CODE_OAUTH_TOKEN.

Usage:
    from dispatcher import Dispatcher

    dispatcher = Dispatcher()
    result = await dispatcher.run("Summarize the latest AI news")
    print(result.text)
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path

DOCKER_IMAGE = os.getenv("DEERFLOW_WORKER_IMAGE", "deerflow-claude-worker:latest")
DEFAULT_MAX_TURNS = int(os.getenv("DEERFLOW_MAX_TURNS", "15"))
DEFAULT_TIMEOUT = int(os.getenv("DEERFLOW_TIMEOUT", "300"))  # seconds
DEFAULT_ALLOWED_TOOLS = "Read,Write,Edit,Bash,Glob,Grep,WebSearch,WebFetch"


@dataclass
class TaskResult:
    """Result from a Claude Code worker container."""

    task_id: str
    success: bool
    text: str = ""
    session_id: str | None = None
    raw_json: dict = field(default_factory=dict)
    error: str | None = None
    exit_code: int = 0

    @classmethod
    def from_json_output(cls, task_id: str, stdout: str, exit_code: int) -> TaskResult:
        """Parse Claude Code JSON output into a TaskResult."""
        if exit_code != 0:
            return cls(
                task_id=task_id,
                success=False,
                error=stdout,
                exit_code=exit_code,
            )
        try:
            data = json.loads(stdout)
            return cls(
                task_id=task_id,
                success=True,
                text=data.get("result", ""),
                session_id=data.get("session_id"),
                raw_json=data,
            )
        except json.JSONDecodeError:
            # Fallback: treat as plain text
            return cls(task_id=task_id, success=True, text=stdout)


@dataclass
class Task:
    """A task to dispatch to a Claude Code worker."""

    prompt: str
    task_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    allowed_tools: str = DEFAULT_ALLOWED_TOOLS
    max_turns: int = DEFAULT_MAX_TURNS
    timeout: int = DEFAULT_TIMEOUT
    system_prompt: str | None = None
    workspace_path: str | None = None
    mcp_config_path: str | None = None


class Dispatcher:
    """
    Dispatches tasks to Claude Code running inside Docker containers.

    Each task runs in an isolated container with:
    - CLAUDE_CODE_OAUTH_TOKEN for Max plan authentication
    - Configurable tool access via --allowedTools
    - JSON output for structured result parsing
    - Optional workspace volume mount
    - Optional MCP server configuration
    """

    def __init__(
        self,
        image: str = DOCKER_IMAGE,
        oauth_token: str | None = None,
        max_concurrent: int = 3,
    ):
        self.image = image
        self.oauth_token = oauth_token or os.getenv("CLAUDE_CODE_OAUTH_TOKEN", "")
        self._semaphore = asyncio.Semaphore(max_concurrent)

        if not self.oauth_token:
            raise ValueError(
                "CLAUDE_CODE_OAUTH_TOKEN is required. "
                "Generate one with: claude setup-token"
            )

    def _build_docker_cmd(self, task: Task) -> list[str]:
        """Build the docker run command for a task."""
        cmd = [
            "docker", "run", "--rm",
            # Auth
            "-e", f"CLAUDE_CODE_OAUTH_TOKEN={self.oauth_token}",
            "-e", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1",
            # Resource limits
            "--memory=2g",
            "--cpus=2.0",
        ]

        # Workspace volume mount
        if task.workspace_path:
            workspace = Path(task.workspace_path).resolve()
            cmd.extend(["-v", f"{workspace}:/workspace"])

        # MCP config mount
        if task.mcp_config_path:
            mcp_path = Path(task.mcp_config_path).resolve()
            cmd.extend([
                "-v", f"{mcp_path}:/tmp/mcp-config.json:ro",
            ])

        # Image
        cmd.append(self.image)

        # Claude Code arguments
        cmd.extend([
            "-p", task.prompt,
            "--output-format", "json",
            "--allowedTools", task.allowed_tools,
            "--max-turns", str(task.max_turns),
        ])

        # Optional system prompt
        if task.system_prompt:
            cmd.extend(["--append-system-prompt", task.system_prompt])

        # Optional MCP config
        if task.mcp_config_path:
            cmd.extend(["--mcp-config", "/tmp/mcp-config.json"])

        return cmd

    async def run(self, prompt: str, **kwargs) -> TaskResult:
        """Run a single task and return the result."""
        task = Task(prompt=prompt, **kwargs)
        return await self._execute(task)

    async def run_many(self, tasks: list[Task]) -> list[TaskResult]:
        """Run multiple tasks concurrently (respecting max_concurrent)."""
        coros = [self._execute(task) for task in tasks]
        return await asyncio.gather(*coros)

    async def run_parallel(self, prompts: list[str], **kwargs) -> list[TaskResult]:
        """Convenience: run multiple prompts in parallel with shared settings."""
        tasks = [Task(prompt=p, **kwargs) for p in prompts]
        return await self.run_many(tasks)

    async def _execute(self, task: Task) -> TaskResult:
        """Execute a single task in a Docker container."""
        async with self._semaphore:
            cmd = self._build_docker_cmd(task)

            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )

                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=task.timeout,
                )

                stdout = stdout_bytes.decode("utf-8", errors="replace")
                stderr = stderr_bytes.decode("utf-8", errors="replace")
                exit_code = proc.returncode or 0

                if exit_code != 0 and stderr:
                    return TaskResult(
                        task_id=task.task_id,
                        success=False,
                        error=f"Exit {exit_code}: {stderr}",
                        exit_code=exit_code,
                    )

                return TaskResult.from_json_output(task.task_id, stdout, exit_code)

            except asyncio.TimeoutError:
                # Kill the container if it times out
                proc.kill()
                return TaskResult(
                    task_id=task.task_id,
                    success=False,
                    error=f"Timed out after {task.timeout}s",
                    exit_code=-1,
                )
            except Exception as e:
                return TaskResult(
                    task_id=task.task_id,
                    success=False,
                    error=str(e),
                    exit_code=-1,
                )


class StreamingDispatcher(Dispatcher):
    """
    Extended dispatcher that supports streaming output from Claude Code.

    Uses --output-format stream-json for real-time token streaming.
    """

    async def run_stream(self, prompt: str, **kwargs):
        """
        Yield streaming events from a Claude Code worker.

        Yields dicts with event data as they arrive.
        """
        task = Task(prompt=prompt, **kwargs)
        cmd = self._build_docker_cmd(task)

        # Replace json with stream-json for streaming
        json_idx = cmd.index("json")
        cmd[json_idx] = "stream-json"

        async with self._semaphore:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                async for line in proc.stdout:
                    line_str = line.decode("utf-8", errors="replace").strip()
                    if not line_str:
                        continue
                    try:
                        event = json.loads(line_str)
                        yield event
                    except json.JSONDecodeError:
                        yield {"type": "raw", "text": line_str}

                await proc.wait()

            except Exception as e:
                proc.kill()
                yield {"type": "error", "error": str(e)}


# ---------------------------------------------------------------------------
# Workflow helpers — simple patterns for multi-step task orchestration
# ---------------------------------------------------------------------------

async def sequential(dispatcher: Dispatcher, prompts: list[str], **kwargs) -> list[TaskResult]:
    """Run prompts sequentially, passing each result as context to the next."""
    results = []
    context = ""
    for prompt in prompts:
        full_prompt = f"{prompt}\n\nPrevious context:\n{context}" if context else prompt
        result = await dispatcher.run(full_prompt, **kwargs)
        results.append(result)
        context = result.text if result.success else f"[Error: {result.error}]"
    return results


async def fan_out_fan_in(
    dispatcher: Dispatcher,
    subtasks: list[str],
    aggregation_prompt: str,
    **kwargs,
) -> TaskResult:
    """
    Fan-out: run subtasks in parallel.
    Fan-in: aggregate results with a final prompt.
    """
    # Fan out
    sub_results = await dispatcher.run_parallel(subtasks, **kwargs)

    # Build aggregation context
    parts = []
    for i, r in enumerate(sub_results):
        status = "SUCCESS" if r.success else "FAILED"
        parts.append(f"--- Subtask {i + 1} [{status}] ---\n{r.text or r.error}\n")

    context = "\n".join(parts)
    full_prompt = f"{aggregation_prompt}\n\nSubtask results:\n{context}"

    # Fan in
    return await dispatcher.run(full_prompt, **kwargs)
