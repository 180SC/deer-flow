"""
Usage examples for ClaudeSandbox + Agno integration.

These show how clean the abstraction is from the caller's perspective.
Run: python examples.py [example_number]
"""

from __future__ import annotations

import asyncio
import sys


# -----------------------------------------------------------------------
# Example 1: Simplest possible usage
# -----------------------------------------------------------------------
async def example_1_simple():
    """One line to get Claude Code analysis in a sandbox."""
    from sandbox import ClaudeSandbox

    sandbox = ClaudeSandbox()
    result = await sandbox.run("What are the top 3 Python web frameworks and why?")
    print(result.text)
    print(f"\n[Duration: {result.duration_seconds:.1f}s]")


# -----------------------------------------------------------------------
# Example 2: Inject files and analyze them
# -----------------------------------------------------------------------
async def example_2_file_injection():
    """Pass data into the sandbox for analysis."""
    from sandbox import ClaudeSandbox

    sandbox = ClaudeSandbox()
    result = await sandbox.run(
        "Analyze data.csv — find outliers, compute summary statistics, "
        "and suggest data quality improvements.",
        files={
            "data.csv": (
                "name,age,salary,department\n"
                "Alice,29,85000,Engineering\n"
                "Bob,34,92000,Engineering\n"
                "Charlie,45,150000,Sales\n"
                "Diana,28,78000,Marketing\n"
                "Eve,31,-5000,Engineering\n"
                "Frank,99,45000,Sales\n"
            ),
        },
        tools="Read,Bash",
        max_turns=10,
    )
    print(result.text)


# -----------------------------------------------------------------------
# Example 3: Code generation + testing in sandbox
# -----------------------------------------------------------------------
async def example_3_code_gen():
    """Generate code and run tests — all inside the sandbox."""
    from sandbox import ClaudeSandbox

    sandbox = ClaudeSandbox()
    result = await sandbox.run(
        "Write a Python function that implements a least-recently-used (LRU) cache "
        "with O(1) get and put operations. Include comprehensive tests using pytest. "
        "Run the tests and report the results.",
        tools="Read,Write,Edit,Bash",
        max_turns=15,
        system_prompt="You are an expert Python developer. Write clean, typed code.",
    )
    print(result.text)


# -----------------------------------------------------------------------
# Example 4: Analyze an existing codebase
# -----------------------------------------------------------------------
async def example_4_codebase_analysis():
    """Mount a real project and analyze it."""
    from sandbox import ClaudeSandbox

    sandbox = ClaudeSandbox(workspace="/path/to/your/project")
    result = await sandbox.run(
        "Analyze this codebase. Identify: "
        "1) The overall architecture pattern "
        "2) Key entry points "
        "3) Potential security issues "
        "4) Test coverage gaps",
        tools="Read,Glob,Grep,Bash",
        max_turns=15,
        system_prompt="You are a senior software architect doing a code review.",
    )
    print(result.text)


# -----------------------------------------------------------------------
# Example 5: Parallel sandbox execution
# -----------------------------------------------------------------------
async def example_5_parallel():
    """Run multiple sandboxes concurrently."""
    from sandbox import ClaudeSandbox

    sandbox = ClaudeSandbox(max_concurrent=5)

    prompts = [
        "Write a Python implementation of binary search. Return just the code.",
        "Write a Python implementation of merge sort. Return just the code.",
        "Write a Python implementation of a trie data structure. Return just the code.",
    ]

    results = await sandbox.run_many(prompts, tools="", max_turns=1)

    for i, r in enumerate(results):
        print(f"=== Result {i + 1} ({'OK' if r.success else 'FAIL'}) ===")
        print(r.text[:500])
        print()


# -----------------------------------------------------------------------
# Example 6: Streaming output
# -----------------------------------------------------------------------
async def example_6_streaming():
    """Watch Claude Code work in real-time."""
    from sandbox import ClaudeSandbox

    sandbox = ClaudeSandbox()
    async for event in sandbox.stream(
        "Explain how Docker container networking works. Be thorough.",
        tools="",
        max_turns=1,
    ):
        if event.type == "text":
            print(event.content, end="", flush=True)
        elif event.type == "tool_use":
            print(f"\n[Using tool: {event.content}]")
        elif event.type == "done":
            print(f"\n\n[Done]")


# -----------------------------------------------------------------------
# Example 7: Agno Agent with sandbox tool
# -----------------------------------------------------------------------
def example_7_agno_agent():
    """An Agno agent that delegates heavy work to Claude Code sandboxes."""
    from agno.agent import Agent
    from agno.models.anthropic import Claude
    from agno_tool import claude_sandbox, claude_sandbox_with_files, claude_sandbox_analyze

    agent = Agent(
        model=Claude(id="claude-sonnet-4-20250514"),
        tools=[claude_sandbox, claude_sandbox_with_files, claude_sandbox_analyze],
        instructions=[
            "You are a technical lead. You can delegate complex tasks to "
            "Claude Code sandboxes for execution.",
            "Use claude_sandbox for general tasks that need code execution.",
            "Use claude_sandbox_with_files when you need to pass data in.",
            "Use claude_sandbox_analyze to review existing codebases.",
            "Synthesize the sandbox results into a clear answer.",
        ],
        show_tool_calls=True,
    )

    agent.print_response(
        "I have a CSV with columns: date, revenue, expenses. "
        "Generate a Python script that creates a financial dashboard "
        "with matplotlib showing revenue vs expenses over time, "
        "profit margins, and a trend line. Test it with sample data."
    )


# -----------------------------------------------------------------------
# Example 8: Agno Team with sandbox-powered specialists
# -----------------------------------------------------------------------
def example_8_agno_team():
    """Multiple Agno agents, each using sandboxes for their specialty."""
    from agno.agent import Agent
    from agno.models.anthropic import Claude
    from agno_tool import claude_sandbox, claude_sandbox_analyze

    # Security auditor agent
    security_agent = Agent(
        name="SecurityAuditor",
        model=Claude(id="claude-sonnet-4-20250514"),
        tools=[claude_sandbox_analyze],
        instructions=[
            "You are a security auditor. Use claude_sandbox_analyze to "
            "review code for OWASP Top 10 vulnerabilities.",
        ],
    )

    # Performance analyst agent
    perf_agent = Agent(
        name="PerformanceAnalyst",
        model=Claude(id="claude-sonnet-4-20250514"),
        tools=[claude_sandbox],
        instructions=[
            "You are a performance engineer. Use claude_sandbox to write "
            "and run benchmarks, profiling scripts, and load tests.",
        ],
    )

    # Tech lead that coordinates
    lead = Agent(
        name="TechLead",
        model=Claude(id="claude-sonnet-4-20250514"),
        team=[security_agent, perf_agent],
        instructions=[
            "You are a tech lead coordinating a code review. "
            "Delegate security review to SecurityAuditor and "
            "performance analysis to PerformanceAnalyst. "
            "Synthesize their findings into a single report.",
        ],
        show_tool_calls=True,
    )

    lead.print_response(
        "Review the project at /workspace for both security issues "
        "and performance bottlenecks. Give me a combined report."
    )


# -----------------------------------------------------------------------
# Runner
# -----------------------------------------------------------------------

EXAMPLES = {
    "1": ("Simple sandbox execution", example_1_simple),
    "2": ("File injection + analysis", example_2_file_injection),
    "3": ("Code generation + testing", example_3_code_gen),
    "4": ("Codebase analysis", example_4_codebase_analysis),
    "5": ("Parallel execution", example_5_parallel),
    "6": ("Streaming output", example_6_streaming),
    "7": ("Agno agent with sandbox tool", example_7_agno_agent),
    "8": ("Agno team with sandbox specialists", example_8_agno_team),
}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in EXAMPLES:
        print("Usage: python examples.py <number>\n")
        for num, (desc, _) in EXAMPLES.items():
            print(f"  {num}. {desc}")
        sys.exit(1)

    desc, fn = EXAMPLES[sys.argv[1]]
    print(f"Running: {desc}\n{'=' * 60}\n")

    if asyncio.iscoroutinefunction(fn):
        asyncio.run(fn())
    else:
        fn()
