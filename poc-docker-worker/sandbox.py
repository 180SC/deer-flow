"""
ClaudeSandbox — A clean abstraction for running Claude Code in isolated Docker containers.

This is the core primitive: send a prompt in, get a result back. The container
lifecycle (create → run → collect → destroy) is fully managed.

Usage:
    from sandbox import ClaudeSandbox

    async with ClaudeSandbox() as sandbox:
        result = await sandbox.run("Analyze this CSV and find anomalies",
                                   files={"data.csv": csv_content})
        print(result.text)

For Agno integration, see `agno_tool.py` which wraps this as an @tool.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DOCKER_IMAGE = os.getenv("CLAUDE_SANDBOX_IMAGE", "deerflow-claude-worker:latest")
DEFAULT_MAX_TURNS = int(os.getenv("CLAUDE_SANDBOX_MAX_TURNS", "15"))
DEFAULT_TIMEOUT = int(os.getenv("CLAUDE_SANDBOX_TIMEOUT", "300"))
DEFAULT_MEMORY = os.getenv("CLAUDE_SANDBOX_MEMORY", "2g")
DEFAULT_CPUS = float(os.getenv("CLAUDE_SANDBOX_CPUS", "2.0"))

# Tools that are safe for sandboxed execution
SAFE_TOOLS = "Read,Write,Edit,Bash,Glob,Grep"
# Tools that include network access
NETWORK_TOOLS = "Read,Write,Edit,Bash,Glob,Grep,WebSearch,WebFetch"
# Minimal — no shell, no file writes
READONLY_TOOLS = "Read,Glob,Grep"


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class SandboxResult:
    """The output from a sandboxed Claude Code execution."""

    text: str
    success: bool
    session_id: str | None = None
    cost_usd: float | None = None
    duration_seconds: float = 0.0
    exit_code: int = 0
    error: str | None = None
    raw: dict = field(default_factory=dict)

    def __bool__(self) -> bool:
        return self.success

    def __str__(self) -> str:
        return self.text if self.success else f"[Error] {self.error}"


@dataclass
class StreamEvent:
    """A single streaming event from the sandbox."""

    type: str  # "text", "tool_use", "tool_result", "error", "done"
    content: str = ""
    data: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Core sandbox
# ---------------------------------------------------------------------------


class ClaudeSandbox:
    """
    Run Claude Code inside an isolated Docker container.

    Each invocation gets a fresh container with:
    - Isolated filesystem (optionally pre-loaded with files)
    - Configurable tool access
    - Resource limits (memory, CPU)
    - Automatic cleanup on completion

    Can be used as a context manager or directly.

    Examples:
        # Simple
        sandbox = ClaudeSandbox()
        result = await sandbox.run("What is 2+2?")

        # Context manager (recommended for file injection)
        async with ClaudeSandbox() as sandbox:
            result = await sandbox.run(
                "Analyze this data",
                files={"data.json": '{"key": "value"}'},
            )

        # With workspace mount (for analyzing existing code)
        sandbox = ClaudeSandbox(workspace="/path/to/project")
        result = await sandbox.run("Find security issues in this codebase")
    """

    def __init__(
        self,
        *,
        image: str = DOCKER_IMAGE,
        oauth_token: str | None = None,
        max_concurrent: int = 3,
        workspace: str | Path | None = None,
        memory_limit: str = DEFAULT_MEMORY,
        cpu_limit: float = DEFAULT_CPUS,
        network: bool = True,
    ):
        self.image = image
        self.oauth_token = oauth_token or os.getenv("CLAUDE_CODE_OAUTH_TOKEN", "")
        self.workspace = Path(workspace).resolve() if workspace else None
        self.memory_limit = memory_limit
        self.cpu_limit = cpu_limit
        self.network = network
        self._semaphore = asyncio.Semaphore(max_concurrent)

        if not self.oauth_token:
            raise ValueError(
                "CLAUDE_CODE_OAUTH_TOKEN required. Generate with: claude setup-token"
            )

    async def __aenter__(self) -> ClaudeSandbox:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        pass  # Containers are --rm, so cleanup is automatic

    # -------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------

    async def run(
        self,
        prompt: str,
        *,
        tools: str = SAFE_TOOLS,
        max_turns: int = DEFAULT_MAX_TURNS,
        timeout: int = DEFAULT_TIMEOUT,
        system_prompt: str | None = None,
        files: dict[str, str | bytes] | None = None,
        mcp_config: dict | None = None,
    ) -> SandboxResult:
        """
        Run a prompt in an isolated container and return the result.

        Args:
            prompt: The task for Claude Code to perform.
            tools: Comma-separated allowed tools. Use SAFE_TOOLS, NETWORK_TOOLS,
                   READONLY_TOOLS, or a custom string.
            max_turns: Max agentic turns before stopping.
            timeout: Seconds before killing the container.
            system_prompt: Optional system prompt to append.
            files: Dict of {filename: content} to inject into /workspace.
            mcp_config: Optional MCP server configuration dict.

        Returns:
            SandboxResult with .text, .success, .error, etc.
        """
        import time

        start = time.monotonic()
        task_id = uuid.uuid4().hex[:8]

        async with self._semaphore:
            with tempfile.TemporaryDirectory(prefix=f"sandbox-{task_id}-") as tmpdir:
                tmppath = Path(tmpdir)

                # Inject files into the temp workspace
                if files:
                    for filename, content in files.items():
                        filepath = tmppath / filename
                        filepath.parent.mkdir(parents=True, exist_ok=True)
                        if isinstance(content, bytes):
                            filepath.write_bytes(content)
                        else:
                            filepath.write_text(content)

                # Build command
                cmd = self._build_cmd(
                    prompt=prompt,
                    tools=tools,
                    max_turns=max_turns,
                    system_prompt=system_prompt,
                    mcp_config=mcp_config,
                    inject_dir=tmppath if files else None,
                )

                try:
                    proc = await asyncio.create_subprocess_exec(
                        *cmd,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    stdout_b, stderr_b = await asyncio.wait_for(
                        proc.communicate(), timeout=timeout
                    )
                    elapsed = time.monotonic() - start
                    stdout = stdout_b.decode("utf-8", errors="replace")
                    stderr = stderr_b.decode("utf-8", errors="replace")
                    exit_code = proc.returncode or 0

                    return self._parse_result(
                        stdout, stderr, exit_code, elapsed
                    )

                except asyncio.TimeoutError:
                    proc.kill()
                    return SandboxResult(
                        text="",
                        success=False,
                        error=f"Timed out after {timeout}s",
                        exit_code=-1,
                        duration_seconds=time.monotonic() - start,
                    )
                except Exception as e:
                    return SandboxResult(
                        text="",
                        success=False,
                        error=str(e),
                        exit_code=-1,
                        duration_seconds=time.monotonic() - start,
                    )

    async def run_many(
        self, prompts: list[str], **kwargs: Any
    ) -> list[SandboxResult]:
        """Run multiple prompts concurrently."""
        return await asyncio.gather(
            *(self.run(p, **kwargs) for p in prompts)
        )

    async def stream(
        self,
        prompt: str,
        *,
        tools: str = SAFE_TOOLS,
        max_turns: int = DEFAULT_MAX_TURNS,
        timeout: int = DEFAULT_TIMEOUT,
        system_prompt: str | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """
        Stream events from a sandboxed Claude Code execution.

        Yields StreamEvent objects as they arrive.
        """
        cmd = self._build_cmd(
            prompt=prompt,
            tools=tools,
            max_turns=max_turns,
            system_prompt=system_prompt,
            output_format="stream-json",
        )

        async with self._semaphore:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                async for line in proc.stdout:
                    text = line.decode("utf-8", errors="replace").strip()
                    if not text:
                        continue
                    try:
                        data = json.loads(text)
                        event_type = data.get("type", "unknown")

                        if event_type == "assistant" and "message" in data:
                            msg = data["message"]
                            for block in msg.get("content", []):
                                if block.get("type") == "text":
                                    yield StreamEvent(
                                        type="text", content=block["text"], data=data
                                    )
                                elif block.get("type") == "tool_use":
                                    yield StreamEvent(
                                        type="tool_use",
                                        content=block.get("name", ""),
                                        data=block,
                                    )
                        elif event_type == "result":
                            yield StreamEvent(
                                type="done",
                                content=data.get("result", ""),
                                data=data,
                            )
                        else:
                            yield StreamEvent(type=event_type, data=data)

                    except json.JSONDecodeError:
                        yield StreamEvent(type="text", content=text)

                await proc.wait()

            except Exception as e:
                proc.kill()
                yield StreamEvent(type="error", content=str(e))

    # -------------------------------------------------------------------
    # Internals
    # -------------------------------------------------------------------

    def _build_cmd(
        self,
        prompt: str,
        tools: str,
        max_turns: int,
        system_prompt: str | None = None,
        mcp_config: dict | None = None,
        inject_dir: Path | None = None,
        output_format: str = "json",
    ) -> list[str]:
        cmd = [
            "docker", "run", "--rm",
            "-e", f"CLAUDE_CODE_OAUTH_TOKEN={self.oauth_token}",
            "-e", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1",
            f"--memory={self.memory_limit}",
            f"--cpus={self.cpu_limit}",
        ]

        if not self.network:
            cmd.append("--network=none")

        # Mount workspace
        if self.workspace:
            cmd.extend(["-v", f"{self.workspace}:/workspace"])
        elif inject_dir:
            cmd.extend(["-v", f"{inject_dir}:/workspace"])

        # MCP config
        mcp_tmpfile = None
        if mcp_config:
            mcp_tmpfile = tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False, prefix="mcp-"
            )
            json.dump(mcp_config, mcp_tmpfile)
            mcp_tmpfile.close()
            cmd.extend(["-v", f"{mcp_tmpfile.name}:/tmp/mcp-config.json:ro"])

        cmd.append(self.image)

        # Claude Code args
        cmd.extend([
            "-p", prompt,
            "--output-format", output_format,
            "--allowedTools", tools,
            "--max-turns", str(max_turns),
        ])

        if system_prompt:
            cmd.extend(["--append-system-prompt", system_prompt])

        if mcp_config:
            cmd.extend(["--mcp-config", "/tmp/mcp-config.json"])

        return cmd

    @staticmethod
    def _parse_result(
        stdout: str, stderr: str, exit_code: int, elapsed: float
    ) -> SandboxResult:
        if exit_code != 0:
            return SandboxResult(
                text="",
                success=False,
                error=stderr or stdout,
                exit_code=exit_code,
                duration_seconds=elapsed,
            )
        try:
            data = json.loads(stdout)
            return SandboxResult(
                text=data.get("result", ""),
                success=True,
                session_id=data.get("session_id"),
                raw=data,
                exit_code=0,
                duration_seconds=elapsed,
            )
        except json.JSONDecodeError:
            return SandboxResult(
                text=stdout,
                success=True,
                duration_seconds=elapsed,
            )
