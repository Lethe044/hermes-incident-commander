#!/usr/bin/env python3
"""
Hermes Incident Commander — Interactive Demo
============================================
Run this to see Incident Commander in action without a full Hermes
installation. Uses the Anthropic API directly to simulate Hermes's
tool-calling agent loop.

Requirements:
    pip install anthropic rich

Usage:
    export ANTHROPIC_API_KEY=sk-ant-...
    python demo/demo_incident.py

    # Or run a specific scenario:
    python demo/demo_incident.py --scenario disk-full-logs
    python demo/demo_incident.py --scenario svc-crash-nginx
    python demo/demo_incident.py --scenario cpu-runaway-process
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# ── Rich for pretty terminal output ──────────────────────────────────────────
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.markdown import Markdown
    from rich.progress import Progress, SpinnerColumn, TextColumn
    from rich.rule import Rule
    from rich.syntax import Syntax
    from rich.table import Table
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False
    print("Tip: pip install rich  — for beautiful output")

# ── Anthropic SDK ─────────────────────────────────────────────────────────────
try:
    import anthropic
except ImportError:
    print("Error: pip install anthropic")
    sys.exit(1)

console = Console() if RICH_AVAILABLE else None

# ---------------------------------------------------------------------------
# Tool implementations (simulated system tools for demo safety)
# ---------------------------------------------------------------------------

INCIDENT_DIR = Path.home() / ".hermes" / "incidents"
SKILLS_DIR   = Path.home() / ".hermes" / "skills"

def _run(cmd: str, timeout: int = 15) -> Dict[str, Any]:
    """Run a shell command and return structured output."""
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout
        )
        return {
            "exit_code": result.returncode,
            "output": result.stdout[:4000],
            "error": result.stderr[:1000] if result.stderr else None,
            "success": result.returncode == 0,
        }
    except subprocess.TimeoutExpired:
        return {"exit_code": -1, "output": "", "error": "Command timed out", "success": False}
    except Exception as exc:
        return {"exit_code": -1, "output": "", "error": str(exc), "success": False}


TOOL_DEFINITIONS = [
    {
        "name": "terminal",
        "description": (
            "Execute a shell command on the server. Use for system diagnostics, "
            "service management, log inspection, and remediation. "
            "Commands run as the current user."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to execute",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (default 15)",
                    "default": 15,
                },
            },
            "required": ["command"],
        },
    },
    {
        "name": "write_file",
        "description": "Write content to a file. Use to create incident reports and prevention skills.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute or ~ path"},
                "content": {"type": "string", "description": "File content"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "read_file",
        "description": "Read a file's contents.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
            },
            "required": ["path"],
        },
    },
]


def dispatch_tool(tool_name: str, tool_input: Dict[str, Any]) -> str:
    """Route a tool call to its implementation and return a string result."""
    if tool_name == "terminal":
        cmd = tool_input["command"]
        timeout = tool_input.get("timeout", 15)
        result = _run(cmd, timeout)
        parts = []
        if result["output"]:
            parts.append(result["output"])
        if result["error"]:
            parts.append(f"STDERR: {result['error']}")
        parts.append(f"[exit_code={result['exit_code']}]")
        return "\n".join(parts)

    elif tool_name == "write_file":
        path = Path(tool_input["path"].replace("~", str(Path.home())))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(tool_input["content"])
        return f"Written {len(tool_input['content'])} bytes to {path}"

    elif tool_name == "read_file":
        path = Path(tool_input["path"].replace("~", str(Path.home())))
        if path.exists():
            return path.read_text()[:4000]
        return f"File not found: {path}"

    else:
        return f"Unknown tool: {tool_name}"


# ---------------------------------------------------------------------------
# Incident Scenarios (subset, self-contained for demo)
# ---------------------------------------------------------------------------

DEMO_SCENARIOS = {
    "disk-full-logs": {
        "title": "🚨 Disk 95% full — Log files exploded",
        "severity": "P1",
        "setup": [
            "mkdir -p /tmp/hermes_demo_logs",
            "dd if=/dev/urandom of=/tmp/hermes_demo_logs/app.log.old bs=1M count=30 2>/dev/null",
            "dd if=/dev/urandom of=/tmp/hermes_demo_logs/debug.log.old bs=1M count=20 2>/dev/null",
            "echo 'DISK_INCIDENT_ACTIVE=1' > /tmp/hermes_incident_marker",
        ],
        "cleanup": [
            "rm -rf /tmp/hermes_demo_logs /tmp/hermes_incident_marker",
        ],
        "prompt": (
            "ALERT: Disk usage just hit 95% on our application server. "
            "Services are failing because they can't write to disk. "
            "Log rotation hasn't been running properly. "
            "There are huge log files in /tmp/hermes_demo_logs consuming all our space. "
            "Find them, clean up disk space, and document what you did. "
            "Write a post-incident report to ~/.hermes/incidents/ "
            "and create a prevention skill in ~/.hermes/skills/disk-monitor/"
        ),
    },
    "svc-crash-nginx": {
        "title": "🚨 nginx crashed — Website unreachable",
        "severity": "P0",
        "setup": [
            "echo 'SERVICE_INCIDENT_ACTIVE=1' > /tmp/hermes_incident_marker",
        ],
        "cleanup": [
            "rm -f /tmp/hermes_incident_marker",
        ],
        "prompt": (
            "ALERT: Our website is down! Users are getting connection refused. "
            "nginx was running 10 minutes ago but now it's not responding. "
            "This is a P0 incident — we're losing revenue every minute. "
            "Investigate the system, check what services are running or failing, "
            "identify the problem, attempt to fix it, "
            "and write a detailed post-incident report to ~/.hermes/incidents/."
        ),
    },
    "cpu-runaway-process": {
        "title": "🚨 CPU at 95% — Runaway computation detected",
        "severity": "P2",
        "setup": [
            # Spin up a moderate CPU consumer (not too heavy for demo)
            "python3 -c \""
            "import os, time; "
            "open('/tmp/hermes_cpu_hog.pid','w').write(str(os.getpid())); "
            "[abs(x) for x in range(50_000_000)]"
            "\" &",
            "sleep 0.5",
            "echo 'CPU_INCIDENT_ACTIVE=1' > /tmp/hermes_incident_marker",
        ],
        "cleanup": [
            "kill $(cat /tmp/hermes_cpu_hog.pid 2>/dev/null) 2>/dev/null || true",
            "rm -f /tmp/hermes_cpu_hog.pid /tmp/hermes_incident_marker",
        ],
        "prompt": (
            "ALERT: CPU utilization has been at 90%+ for the last 10 minutes. "
            "Server response times are severely degraded. "
            "Something is doing heavy computation that shouldn't be. "
            "Find the runaway process (check /tmp/hermes_cpu_hog.pid), "
            "identify what it is, terminate it safely, "
            "verify the fix, and write a post-incident report to ~/.hermes/incidents/."
        ),
    },
}


# ---------------------------------------------------------------------------
# Agent loop
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are Hermes Incident Commander — an autonomous Site Reliability Engineer.

When you receive an incident alert:
1. Immediately run diagnostics (uptime, df -h, free -h, ps aux, systemctl list-units --failed)
2. Classify severity (P0/P1/P2/P3) and announce it
3. Find the root cause through systematic investigation  
4. Apply the safest effective remediation
5. Verify the fix worked
6. Write a structured post-incident report to ~/.hermes/incidents/<timestamp>-<slug>.md
7. Create a prevention skill SKILL.md in ~/.hermes/skills/<category>-prevention/

Be autonomous and thorough. Do not ask for permission for safe diagnostic operations.
Speed matters — every minute of downtime costs money."""


def run_incident_agent(
    scenario: Dict[str, Any],
    api_key: str,
    max_turns: int = 20,
) -> Dict[str, Any]:
    """Run the agent loop for one incident scenario."""

    client = anthropic.Anthropic(api_key=api_key)
    messages: List[Dict[str, Any]] = [
        {"role": "user", "content": scenario["prompt"]}
    ]
    turn = 0
    tool_calls_made: List[str] = []
    start_time = time.time()

    if RICH_AVAILABLE:
        console.print(Rule(f"[bold red]{scenario['title']}[/]"))
        console.print(Panel(
            scenario["prompt"],
            title="[yellow]📟 Incident Alert[/]",
            border_style="red",
        ))
    else:
        print(f"\n{'='*60}")
        print(scenario["title"])
        print(scenario["prompt"])
        print('='*60)

    while turn < max_turns:
        turn += 1

        if RICH_AVAILABLE:
            with Progress(
                SpinnerColumn("dots"),
                TextColumn(f"[cyan]Hermes thinking... (turn {turn}/{max_turns})[/]"),
                transient=True,
                console=console,
            ) as p:
                p.add_task("")
                response = client.messages.create(
                    model="claude-sonnet-4-20250514",
                    max_tokens=4096,
                    system=SYSTEM_PROMPT,
                    tools=TOOL_DEFINITIONS,
                    messages=messages,
                )
        else:
            print(f"\n[Turn {turn}] Hermes thinking...")
            response = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                tools=TOOL_DEFINITIONS,
                messages=messages,
            )

        # Extract text and tool calls
        tool_calls = [b for b in response.content if b.type == "tool_use"]
        text_blocks = [b for b in response.content if b.type == "text" and b.text.strip()]

        # Show agent's reasoning
        for tb in text_blocks:
            if RICH_AVAILABLE:
                console.print(Panel(
                    Markdown(tb.text),
                    title="[green]🤖 Hermes[/]",
                    border_style="green",
                ))
            else:
                print(f"\n[Hermes]: {tb.text}")

        # If no tool calls, agent is done
        if not tool_calls or response.stop_reason == "end_turn":
            break

        # Execute each tool call
        tool_results = []
        for tc in tool_calls:
            tool_calls_made.append(tc.name)
            cmd_preview = (
                tc.input.get("command", tc.input.get("path", ""))[:80]
            )

            if RICH_AVAILABLE:
                console.print(
                    f"  [yellow]⚡ {tc.name}[/] [dim]{cmd_preview}[/]"
                )
            else:
                print(f"\n  > {tc.name}: {cmd_preview}")

            result_text = dispatch_tool(tc.name, tc.input)

            if RICH_AVAILABLE and tc.name == "terminal" and len(result_text) < 2000:
                console.print(Syntax(result_text, "bash", theme="monokai", line_numbers=False))
            elif not RICH_AVAILABLE:
                print(f"  OUTPUT: {result_text[:500]}")

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tc.id,
                "content": result_text,
            })

        # Add assistant + tool results to conversation
        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": tool_results})

    elapsed = time.time() - start_time

    # ── Summary ──────────────────────────────────────────────────────────────
    report_files = list(INCIDENT_DIR.glob("*.md")) if INCIDENT_DIR.exists() else []
    skill_dirs   = list(SKILLS_DIR.iterdir()) if SKILLS_DIR.exists() else []

    if RICH_AVAILABLE:
        console.print(Rule("[bold green]Incident Resolution Summary[/]"))
        t = Table(show_header=True, header_style="bold cyan")
        t.add_column("Metric", style="dim")
        t.add_column("Value")
        t.add_row("Severity",       scenario.get("severity", "?"))
        t.add_row("Turns used",     str(turn))
        t.add_row("Tool calls",     str(len(tool_calls_made)))
        t.add_row("Elapsed time",   f"{elapsed:.1f}s")
        t.add_row("Reports written",str(len(report_files)))
        t.add_row("Skills created", str(max(0, len(skill_dirs) - 5)))
        t.add_row("Tools used",     ", ".join(sorted(set(tool_calls_made))))
        console.print(t)
    else:
        print(f"\nSUMMARY: {turn} turns, {len(tool_calls_made)} tool calls, {elapsed:.1f}s")

    return {
        "turns": turn,
        "tool_calls": len(tool_calls_made),
        "elapsed_seconds": elapsed,
        "reports_written": len(report_files),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Hermes Incident Commander — Interactive Demo"
    )
    parser.add_argument(
        "--scenario",
        choices=list(DEMO_SCENARIOS.keys()),
        default="disk-full-logs",
        help="Which incident to simulate",
    )
    parser.add_argument(
        "--no-setup",
        action="store_true",
        help="Skip environment setup (use if environment is already broken)",
    )
    parser.add_argument(
        "--max-turns",
        type=int,
        default=20,
        help="Maximum agent turns",
    )
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("Error: Set ANTHROPIC_API_KEY environment variable")
        print("  export ANTHROPIC_API_KEY=sk-ant-...")
        sys.exit(1)

    scenario = DEMO_SCENARIOS[args.scenario]

    # Create output directories
    INCIDENT_DIR.mkdir(parents=True, exist_ok=True)
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)

    # Setup broken environment
    if not args.no_setup:
        if RICH_AVAILABLE:
            console.print(f"\n[dim]Setting up incident environment for: {args.scenario}[/]")
        for cmd in scenario.get("setup", []):
            _run(cmd)

    try:
        run_incident_agent(scenario, api_key, args.max_turns)
    finally:
        # Cleanup
        for cmd in scenario.get("cleanup", []):
            _run(cmd)

    if RICH_AVAILABLE:
        console.print("\n[bold green]✅ Demo complete![/]")
        console.print(f"Check [cyan]~/.hermes/incidents/[/] for incident reports")
        console.print(f"Check [cyan]~/.hermes/skills/[/] for auto-created prevention skills")


if __name__ == "__main__":
    main()
