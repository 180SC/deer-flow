#!/usr/bin/env python3
"""
DeerFlow Docker Worker — Proof of Concept Demo

Demonstrates dispatching tasks to Claude Code running inside Docker containers,
with all inference billed to the Anthropic Max plan.

Prerequisites:
    1. Build the worker image:
       cd poc-docker-worker && docker compose build

    2. Generate an OAuth token (run in a separate terminal):
       claude setup-token

    3. Create .env file:
       echo "CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-your-token" > .env

    4. Run this demo:
       python demo.py
"""

from __future__ import annotations

import asyncio
import os
import sys

# Load .env if python-dotenv is available
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from dispatcher import Dispatcher, StreamingDispatcher, Task, fan_out_fan_in, sequential


async def demo_single_task(dispatcher: Dispatcher):
    """Demo 1: Run a single task."""
    print("=" * 60)
    print("DEMO 1: Single Task")
    print("=" * 60)

    result = await dispatcher.run(
        "What is 2 + 2? Reply with just the number.",
        allowed_tools="",  # No tools needed for this
        max_turns=1,
    )

    print(f"  Success: {result.success}")
    print(f"  Result:  {result.text[:200]}")
    print()


async def demo_parallel_tasks(dispatcher: Dispatcher):
    """Demo 2: Run multiple tasks in parallel."""
    print("=" * 60)
    print("DEMO 2: Parallel Tasks")
    print("=" * 60)

    results = await dispatcher.run_parallel(
        [
            "List 3 popular Python web frameworks. Be brief.",
            "List 3 popular JavaScript runtimes. Be brief.",
            "List 3 popular database systems. Be brief.",
        ],
        allowed_tools="",
        max_turns=1,
    )

    for i, r in enumerate(results):
        print(f"  Task {i + 1}: {'OK' if r.success else 'FAIL'}")
        print(f"    {r.text[:150]}")
    print()


async def demo_fan_out_fan_in(dispatcher: Dispatcher):
    """Demo 3: Fan-out/fan-in pattern."""
    print("=" * 60)
    print("DEMO 3: Fan-Out / Fan-In")
    print("=" * 60)

    result = await fan_out_fan_in(
        dispatcher,
        subtasks=[
            "Research the pros of Python for web development. 2 sentences.",
            "Research the pros of Rust for web development. 2 sentences.",
            "Research the pros of Go for web development. 2 sentences.",
        ],
        aggregation_prompt=(
            "Based on the research below, write a brief comparison "
            "of Python, Rust, and Go for web development. 3-4 sentences."
        ),
        allowed_tools="",
        max_turns=1,
    )

    print(f"  Success: {result.success}")
    print(f"  Result:  {result.text[:300]}")
    print()


async def demo_sequential(dispatcher: Dispatcher):
    """Demo 4: Sequential chain (each step builds on previous)."""
    print("=" * 60)
    print("DEMO 4: Sequential Chain")
    print("=" * 60)

    results = await sequential(
        dispatcher,
        [
            "Generate a Python function name for calculating compound interest. Reply with just the function name.",
            "Write a Python function with that name that calculates compound interest. Include a docstring.",
        ],
        allowed_tools="",
        max_turns=1,
    )

    for i, r in enumerate(results):
        print(f"  Step {i + 1}: {'OK' if r.success else 'FAIL'}")
        print(f"    {r.text[:200]}")
    print()


async def demo_with_tools(dispatcher: Dispatcher):
    """Demo 5: Task with tool access (Bash)."""
    print("=" * 60)
    print("DEMO 5: Task with Tools (Bash)")
    print("=" * 60)

    result = await dispatcher.run(
        "Run 'uname -a' and tell me what OS this container is running.",
        allowed_tools="Bash",
        max_turns=3,
    )

    print(f"  Success: {result.success}")
    print(f"  Result:  {result.text[:200]}")
    print()


async def demo_streaming(token: str):
    """Demo 6: Streaming output."""
    print("=" * 60)
    print("DEMO 6: Streaming Output")
    print("=" * 60)

    streaming = StreamingDispatcher(oauth_token=token, max_concurrent=1)

    async for event in streaming.run_stream(
        "Count from 1 to 5, one number per line.",
        allowed_tools="",
        max_turns=1,
    ):
        event_type = event.get("type", "unknown")
        if event_type == "stream_event":
            delta = event.get("event", {}).get("delta", {})
            if delta.get("type") == "text_delta":
                print(delta.get("text", ""), end="", flush=True)
        elif event_type == "result":
            print(f"\n  [Done. Session: {event.get('session_id', 'N/A')}]")
        elif event_type == "error":
            print(f"\n  [Error: {event.get('error')}]")
    print()


async def main():
    token = os.getenv("CLAUDE_CODE_OAUTH_TOKEN", "")
    if not token:
        print("ERROR: CLAUDE_CODE_OAUTH_TOKEN not set.")
        print("Generate one with: claude setup-token")
        print("Then: export CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-...")
        sys.exit(1)

    dispatcher = Dispatcher(oauth_token=token, max_concurrent=3)

    print()
    print("DeerFlow Docker Worker — Proof of Concept")
    print("All inference billed to your Anthropic Max plan")
    print()

    # Run demos sequentially
    await demo_single_task(dispatcher)
    await demo_parallel_tasks(dispatcher)
    await demo_fan_out_fan_in(dispatcher)
    await demo_sequential(dispatcher)
    await demo_with_tools(dispatcher)
    await demo_streaming(token)

    print("=" * 60)
    print("All demos complete!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
