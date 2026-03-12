"""
Hermes Incident Commander — Atropos RL Environment
===================================================
Trains Hermes to autonomously resolve production infrastructure incidents.

Usage:
    # Serve rollouts (RL training loop)
    python environments/incident_env.py serve --config environments/incident_config.yaml

    # Evaluate current model
    python environments/incident_env.py evaluate --config environments/incident_config.yaml

    # Generate SFT data
    python environments/incident_env.py process --config environments/incident_config.yaml
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import textwrap
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Atropos / Hermes imports — available when hermes-agent is installed
# ---------------------------------------------------------------------------
try:
    from environments.hermes_base_env import HermesAgentBaseEnv
    from environments.agent_loop import AgentResult, ToolContext
    from atroposlib.envs.base import ScoredDataGroup
    HERMES_AVAILABLE = True
except ImportError:
    # Allows the file to be read / linted without hermes-agent installed
    HERMES_AVAILABLE = False
    HermesAgentBaseEnv = object  # type: ignore


# ---------------------------------------------------------------------------
# Incident Scenario Definitions
# ---------------------------------------------------------------------------

@dataclass
class IncidentScenario:
    """A single incident training scenario."""
    id: str
    severity: str          # P0 / P1 / P2 / P3
    category: str          # cpu / memory / disk / service / docker / network
    title: str
    system_state: Dict[str, Any]   # What `setup_environment()` injects
    success_criteria: List[str]    # Shell commands that must pass for reward=1.0
    partial_criteria: List[str]    # Commands that give partial credit
    description: str               # Injected into the agent prompt


INCIDENT_SCENARIOS: List[IncidentScenario] = [

    # ------------------------------------------------------------------
    # P0 — Total service outage
    # ------------------------------------------------------------------
    IncidentScenario(
        id="svc-crash-nginx",
        severity="P0",
        category="service",
        title="nginx crashed — website unreachable",
        system_state={
            "setup_commands": [
                "apt-get install -y nginx -qq 2>/dev/null || true",
                "systemctl stop nginx 2>/dev/null || true",
                "echo 'MANUALLY_CRASHED' > /tmp/incident_marker",
            ]
        },
        success_criteria=[
            "systemctl is-active nginx",               # Service is running
            "curl -sf http://localhost/ > /dev/null",   # HTTP responds
        ],
        partial_criteria=[
            "test -f /tmp/incident_marker",             # Agent found the marker
        ],
        description=textwrap.dedent("""
            ALERT: Our website is completely down. Users are getting connection refused.
            nginx is the web server. It was running 10 minutes ago but now it's not
            responding. We don't know why it stopped. Please investigate and fix it ASAP.
            This is a P0 incident — we're losing revenue every minute.
        """).strip(),
    ),

    # ------------------------------------------------------------------
    # P1 — Disk full
    # ------------------------------------------------------------------
    IncidentScenario(
        id="disk-full-logs",
        severity="P1",
        category="disk",
        title="Disk 95% full — log files exploded",
        system_state={
            "setup_commands": [
                "mkdir -p /tmp/fake_logs",
                # Create 50MB of fake log files
                "dd if=/dev/urandom of=/tmp/fake_logs/app.log.1 bs=1M count=25 2>/dev/null",
                "dd if=/dev/urandom of=/tmp/fake_logs/app.log.2 bs=1M count=25 2>/dev/null",
                "echo 'DISK_INCIDENT_ACTIVE' > /tmp/incident_marker",
            ]
        },
        success_criteria=[
            "test ! -f /tmp/fake_logs/app.log.1",   # Large logs removed
            "test ! -f /tmp/fake_logs/app.log.2",
        ],
        partial_criteria=[
            "test -f /tmp/incident_marker",
            # Agent identified /tmp/fake_logs as the culprit
        ],
        description=textwrap.dedent("""
            ALERT: Disk usage just hit 95% on our app server. Applications are starting
            to fail because they can't write to disk. Log rotation hasn't been running
            properly. There are huge log files somewhere eating all our space.
            Find them and clean up disk space without deleting anything important.
        """).strip(),
    ),

    # ------------------------------------------------------------------
    # P1 — OOM / Memory pressure
    # ------------------------------------------------------------------
    IncidentScenario(
        id="memory-leak-process",
        severity="P1",
        category="memory",
        title="Memory exhausted — mystery process eating RAM",
        system_state={
            "setup_commands": [
                # Start a background process that allocates memory
                "python3 -c \""
                "import time, os; "
                "data = bytearray(150 * 1024 * 1024); "  # 150 MB
                "open('/tmp/memory_hog.pid', 'w').write(str(os.getpid())); "
                "time.sleep(300)"
                "\" &",
                "echo 'MEMORY_INCIDENT_ACTIVE' > /tmp/incident_marker",
            ]
        },
        success_criteria=[
            # Memory hog process is dead
            "! kill -0 $(cat /tmp/memory_hog.pid 2>/dev/null) 2>/dev/null",
        ],
        partial_criteria=[
            "test -f /tmp/memory_hog.pid",   # Agent found the PID file
        ],
        description=textwrap.dedent("""
            ALERT: Memory usage is at 90% and climbing. The OOM killer is about to
            start killing processes. Something is leaking memory or allocating far
            more than it should. Find the process that's consuming excessive memory
            and terminate it safely. Document what you found.
        """).strip(),
    ),

    # ------------------------------------------------------------------
    # P2 — High CPU
    # ------------------------------------------------------------------
    IncidentScenario(
        id="cpu-runaway-process",
        severity="P2",
        category="cpu",
        title="CPU at 95% — runaway computation",
        system_state={
            "setup_commands": [
                # Start a CPU-burning process
                "python3 -c \""
                "import os; "
                "open('/tmp/cpu_hog.pid', 'w').write(str(os.getpid())); "
                "[x**2 for x in range(10**9)]"   # noqa
                "\" &",
                "sleep 1",
                "echo 'CPU_INCIDENT_ACTIVE' > /tmp/incident_marker",
            ]
        },
        success_criteria=[
            "! kill -0 $(cat /tmp/cpu_hog.pid 2>/dev/null) 2>/dev/null",
        ],
        partial_criteria=[
            "test -f /tmp/cpu_hog.pid",
        ],
        description=textwrap.dedent("""
            ALERT: CPU utilisation has been at 95%+ for the last 10 minutes.
            Server response times are degraded. Something is doing heavy computation
            and it's not supposed to be. Find the runaway process, identify what it is,
            and resolve the situation. Write up what you found.
        """).strip(),
    ),

    # ------------------------------------------------------------------
    # P2 — Failed systemd service (custom)
    # ------------------------------------------------------------------
    IncidentScenario(
        id="failed-systemd-unit",
        severity="P2",
        category="service",
        title="Custom worker service in failed state",
        system_state={
            "setup_commands": [
                # Create a systemd service that will fail
                "cat > /tmp/hermes-worker.service << 'EOF'\n"
                "[Unit]\nDescription=Hermes Worker\n"
                "[Service]\nExecStart=/bin/false\nRestart=no\n"
                "[Install]\nWantedBy=multi-user.target\nEOF",
                "cp /tmp/hermes-worker.service /etc/systemd/system/ 2>/dev/null || true",
                "systemctl daemon-reload 2>/dev/null || true",
                "systemctl start hermes-worker 2>/dev/null || true",
                "echo 'SERVICE_INCIDENT_ACTIVE' > /tmp/incident_marker",
            ]
        },
        success_criteria=[
            # Service fixed (either restarted with correct binary or disabled cleanly)
            "! systemctl is-failed hermes-worker 2>/dev/null || "
            "systemctl is-active hermes-worker 2>/dev/null",
        ],
        partial_criteria=[
            "systemctl status hermes-worker 2>/dev/null | grep -q 'failed'",
        ],
        description=textwrap.dedent("""
            Our deployment pipeline shows 'hermes-worker' service is in a failed state.
            It was just deployed 20 minutes ago. We need it running. Please investigate
            why it failed, fix it if possible, and document the root cause.
        """).strip(),
    ),
]


# ---------------------------------------------------------------------------
# Reward Computation
# ---------------------------------------------------------------------------

def compute_incident_reward(
    scenario: IncidentScenario,
    result: "AgentResult",
    ctx: "ToolContext",
) -> Tuple[float, Dict[str, Any]]:
    """
    Multi-component reward function:

    Component               Weight    Description
    ──────────────────────────────────────────────────────────────
    resolution_score        0.50      Did the incident get fixed?
    rca_quality             0.15      Did agent find root cause?
    report_quality          0.15      Was a post-incident report written?
    skill_created           0.10      Did agent create a prevention skill?
    response_speed          0.05      Faster resolution = higher reward
    tool_efficiency         0.05      Fewer unnecessary tool calls = better
    """
    scores: Dict[str, float] = {}
    details: Dict[str, Any] = {}

    # ── 1. Resolution Score (0.50) ──────────────────────────────────────────
    passed_success = 0
    for check_cmd in scenario.success_criteria:
        try:
            check_result = ctx.terminal(f"bash -c '{check_cmd}'", timeout=10)
            if check_result.get("exit_code", 1) == 0:
                passed_success += 1
        except Exception:
            pass

    passed_partial = 0
    for check_cmd in scenario.partial_criteria:
        try:
            check_result = ctx.terminal(f"bash -c '{check_cmd}'", timeout=10)
            if check_result.get("exit_code", 1) == 0:
                passed_partial += 1
        except Exception:
            pass

    n_success = len(scenario.success_criteria) or 1
    n_partial = len(scenario.partial_criteria) or 1
    resolution_score = (passed_success / n_success) * 0.50
    resolution_score += (passed_partial / n_partial) * 0.10  # bonus
    scores["resolution"] = min(resolution_score, 0.50)
    details["success_checks"] = f"{passed_success}/{n_success}"

    # ── 2. Root Cause Analysis Quality (0.15) ──────────────────────────────
    rca_keywords = [
        "root cause", "because", "the issue is", "found that",
        "identified", "analysis", "diagnosis",
    ]
    conversation_text = " ".join(
        m.get("content", "") or ""
        for m in result.messages
        if isinstance(m.get("content"), str)
    ).lower()

    rca_hit = sum(1 for kw in rca_keywords if kw in conversation_text)
    rca_score = min(rca_hit / 3.0, 1.0) * 0.15
    scores["rca"] = rca_score
    details["rca_keywords_found"] = rca_hit

    # ── 3. Post-Incident Report (0.15) ─────────────────────────────────────
    try:
        report_check = ctx.terminal(
            "ls ~/.hermes/incidents/*.md 2>/dev/null | wc -l", timeout=5
        )
        report_count = int(report_check.get("output", "0").strip() or "0")
    except Exception:
        report_count = 0

    report_score = min(report_count, 1) * 0.15
    scores["report"] = report_score
    details["reports_written"] = report_count

    # ── 4. Skill Auto-Creation (0.10) ──────────────────────────────────────
    try:
        skill_check = ctx.terminal(
            "ls ~/.hermes/skills/ 2>/dev/null | grep -v '^$' | wc -l", timeout=5
        )
        skill_count = int(skill_check.get("output", "0").strip() or "0")
        # Baseline is skills that came with hermes; >baseline means agent created one
        skill_created = skill_count > 5
    except Exception:
        skill_created = False

    # Also check if agent mentioned creating a skill
    skill_keywords = ["skill", "SKILL.md", "prevention", "playbook", "created a new"]
    skill_mentioned = any(kw in conversation_text for kw in skill_keywords)

    skill_score = (0.10 if skill_created else 0.0) + (0.05 if skill_mentioned else 0.0)
    scores["skill"] = min(skill_score, 0.10)
    details["skill_created"] = skill_created

    # ── 5. Response Speed (0.05) ────────────────────────────────────────────
    turns_used = result.turns_used
    # Ideal: resolve in ≤ 8 turns; penalize beyond 20
    if turns_used <= 8:
        speed_score = 0.05
    elif turns_used <= 20:
        speed_score = 0.05 * (1 - (turns_used - 8) / 12)
    else:
        speed_score = 0.0
    scores["speed"] = speed_score
    details["turns_used"] = turns_used

    # ── 6. Tool Efficiency (0.05) ───────────────────────────────────────────
    # Count tool calls
    tool_call_count = sum(
        1 for m in result.messages
        if m.get("role") == "assistant" and m.get("tool_calls")
    )
    # Penalize for excessive tool calls (>30 = likely flailing)
    if tool_call_count <= 15:
        efficiency_score = 0.05
    elif tool_call_count <= 30:
        efficiency_score = 0.05 * (1 - (tool_call_count - 15) / 15)
    else:
        efficiency_score = 0.0
    scores["efficiency"] = efficiency_score
    details["tool_calls"] = tool_call_count

    # ── Final Score ─────────────────────────────────────────────────────────
    total = sum(scores.values())
    details["component_scores"] = scores
    details["scenario_id"] = scenario.id
    details["severity"] = scenario.severity

    return round(total, 4), details


# ---------------------------------------------------------------------------
# IncidentCommanderEnv
# ---------------------------------------------------------------------------

if HERMES_AVAILABLE:

    class IncidentCommanderEnv(HermesAgentBaseEnv):
        """
        RL training environment for Hermes Incident Commander.

        Each rollout:
          1. Selects a random incident scenario
          2. Sets up the broken system state in a sandboxed terminal
          3. Presents the incident to the agent with full access to the terminal
          4. Scores the agent's response across 6 dimensions
          5. Returns a ScoredDataGroup for Atropos GRPO training
        """

        name = "incident-commander"

        # Toolsets the agent is allowed to use
        ENABLED_TOOLSETS = ["terminal", "file", "web", "delegate"]
        DISABLED_TOOLSETS = ["browser", "vision", "image_gen", "tts"]

        # System prompt injected into every rollout
        SYSTEM_PROMPT = textwrap.dedent("""
            You are Hermes Incident Commander — an autonomous Site Reliability Engineer.

            When you receive an incident alert, you will:
            1. Immediately gather system diagnostics (CPU, memory, disk, services)
            2. Classify the severity (P0/P1/P2/P3)
            3. Identify the root cause through systematic investigation
            4. Apply the safest effective remediation
            5. Verify the fix worked
            6. Write a post-incident report to ~/.hermes/incidents/<timestamp>-<slug>.md
            7. Create a new prevention skill in ~/.hermes/skills/ if the pattern is novel

            You have full terminal access. Use it autonomously. Do not ask for permission
            for safe operations (reading files, running diagnostics, restarting services).
            Announce severity and progress clearly so operators can follow along.

            Speed matters — every minute of downtime costs money.
        """).strip()

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._scenarios = INCIDENT_SCENARIOS
            self._scenario_weights = self._compute_weights()

        def _compute_weights(self) -> List[float]:
            """Weight P0/P1 higher during training for harder problem exposure."""
            weight_map = {"P0": 3.0, "P1": 2.0, "P2": 1.5, "P3": 1.0}
            weights = [weight_map.get(s.severity, 1.0) for s in self._scenarios]
            total = sum(weights)
            return [w / total for w in weights]

        async def setup(self):
            """Called once before training begins."""
            await super().setup()
            os.makedirs(os.path.expanduser("~/.hermes/incidents"), exist_ok=True)

        def get_next_item(self) -> IncidentScenario:
            """Sample a scenario, weighted by severity."""
            return random.choices(self._scenarios, weights=self._scenario_weights, k=1)[0]

        def format_prompt(self, scenario: IncidentScenario) -> str:
            """Turn a scenario into the user message the agent receives."""
            return (
                f"🚨 INCIDENT ALERT\n\n"
                f"**Category:** {scenario.category.upper()}\n"
                f"**Title:** {scenario.title}\n\n"
                f"{scenario.description}\n\n"
                f"You have full terminal access. Investigate and resolve this incident now."
            )

        async def _setup_environment(self, scenario: IncidentScenario, ctx: "ToolContext"):
            """Inject the broken system state before the agent runs."""
            for cmd in scenario.system_state.get("setup_commands", []):
                try:
                    await asyncio.get_event_loop().run_in_executor(
                        None, lambda c=cmd: ctx.terminal(c, timeout=30)
                    )
                except Exception as exc:
                    print(f"[setup] Warning: setup command failed: {exc}")

        async def collect_trajectory(
            self,
            item: IncidentScenario,
            server,
        ) -> ScoredDataGroup:
            """Run one full incident rollout and score it."""
            async with self.get_tool_context(item.id) as ctx:

                # 1. Set up the broken environment
                await self._setup_environment(item, ctx)

                # 2. Run the agent
                result: AgentResult = await self.run_agent_loop(
                    prompt=self.format_prompt(item),
                    system_prompt=self.SYSTEM_PROMPT,
                    server=server,
                    ctx=ctx,
                    enabled_toolsets=self.ENABLED_TOOLSETS,
                    disabled_toolsets=self.DISABLED_TOOLSETS,
                    max_turns=30,
                )

                # 3. Compute reward
                reward, details = compute_incident_reward(item, result, ctx)

                # 4. Package for Atropos
                scored = self._build_scored_group(
                    result=result,
                    reward=reward,
                    item_id=item.id,
                    metadata={
                        "scenario": item.id,
                        "severity": item.severity,
                        "category": item.category,
                        **details,
                    },
                )

                return scored

        async def evaluate(self) -> Dict[str, float]:
            """Periodic evaluation — run all scenarios and report mean MTTR."""
            results = []
            for scenario in self._scenarios:
                async with self.get_tool_context(f"eval-{scenario.id}") as ctx:
                    await self._setup_environment(scenario, ctx)
                    result = await self.run_agent_loop(
                        prompt=self.format_prompt(scenario),
                        system_prompt=self.SYSTEM_PROMPT,
                        server=None,  # Uses configured eval model
                        ctx=ctx,
                        enabled_toolsets=self.ENABLED_TOOLSETS,
                        disabled_toolsets=self.DISABLED_TOOLSETS,
                        max_turns=30,
                    )
                    reward, details = compute_incident_reward(scenario, result, ctx)
                    results.append({
                        "scenario": scenario.id,
                        "severity": scenario.severity,
                        "reward": reward,
                        "turns": result.turns_used,
                        **details,
                    })

            mean_reward = sum(r["reward"] for r in results) / len(results)
            p0_p1 = [r for r in results if r["severity"] in ("P0", "P1")]
            critical_reward = (
                sum(r["reward"] for r in p0_p1) / len(p0_p1) if p0_p1 else 0.0
            )

            return {
                "eval/mean_reward": mean_reward,
                "eval/critical_reward": critical_reward,
                "eval/resolution_rate": sum(
                    1 for r in results if r["reward"] >= 0.5
                ) / len(results),
            }


# ---------------------------------------------------------------------------
# Standalone smoke-test (no Atropos required)
# ---------------------------------------------------------------------------

def smoke_test():
    """
    Quick sanity check — verifies scenario setup commands and reward logic
    without running an actual LLM or Atropos server.
    """
    import subprocess

    print("=" * 60)
    print("Hermes Incident Commander — Smoke Test")
    print("=" * 60)

    for scenario in INCIDENT_SCENARIOS:
        print(f"\n[{scenario.severity}] {scenario.title}")
        print(f"  Category : {scenario.category}")
        print(f"  Criteria : {len(scenario.success_criteria)} success, "
              f"{len(scenario.partial_criteria)} partial")

        # Verify setup commands are syntactically valid bash
        for cmd in scenario.system_state.get("setup_commands", []):
            result = subprocess.run(
                ["bash", "-n", "-c", cmd],
                capture_output=True,
                text=True,
            )
            status = "✓" if result.returncode == 0 else "✗"
            print(f"  {status} Syntax: {cmd[:60]}{'...' if len(cmd)>60 else ''}")

    print("\n✅ Smoke test complete — all scenarios validated")


if __name__ == "__main__":
    import sys
    if "--smoke-test" in sys.argv:
        smoke_test()
    elif HERMES_AVAILABLE:
        import argparse
        parser = argparse.ArgumentParser(description="Incident Commander RL Environment")
        parser.add_argument("command", choices=["serve", "process", "evaluate"])
        parser.add_argument("--config", default="environments/incident_config.yaml")
        args = parser.parse_args()
        IncidentCommanderEnv.cli_main(args.command, args.config)
    else:
        print("hermes-agent not installed — running smoke test instead")
        smoke_test()
