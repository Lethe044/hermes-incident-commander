"""
Hermes Incident Commander — Test Suite
=======================================
Run with:
    pytest tests/ -v
    pytest tests/ -v --tb=short
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# ---------------------------------------------------------------------------
# Import the modules under test (environment-independent)
# ---------------------------------------------------------------------------
from environments.incident_env import (
    INCIDENT_SCENARIOS,
    IncidentScenario,
    compute_incident_reward,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_agent_result():
    """A mock AgentResult with realistic content."""
    result = MagicMock()
    result.turns_used = 7
    result.finished_naturally = True
    result.messages = [
        {"role": "user", "content": "ALERT: nginx is down"},
        {
            "role": "assistant",
            "content": (
                "I'll investigate this incident immediately. "
                "First, let me gather system diagnostics. "
                "After analysis, I've identified the root cause: "
                "nginx service crashed due to a configuration error. "
                "The issue is a missing SSL certificate file. "
                "I've restarted the service and the resolution is complete."
            ),
            "tool_calls": [MagicMock()],
        },
        {"role": "assistant", "content": "Incident resolved. Report written.", "tool_calls": []},
    ]
    result.tool_errors = []
    return result


@pytest.fixture
def mock_ctx(tmp_path):
    """A mock ToolContext that runs real bash commands."""
    ctx = MagicMock()
    incident_dir = tmp_path / ".hermes" / "incidents"
    skills_dir = tmp_path / ".hermes" / "skills"
    incident_dir.mkdir(parents=True)
    skills_dir.mkdir(parents=True)

    def fake_terminal(cmd, timeout=10):
        try:
            result = subprocess.run(
                cmd.replace("~", str(tmp_path)),
                shell=True, capture_output=True, text=True, timeout=timeout
            )
            return {"exit_code": result.returncode, "output": result.stdout}
        except Exception as exc:
            return {"exit_code": -1, "output": str(exc)}

    ctx.terminal = fake_terminal
    ctx._tmp_path = tmp_path
    return ctx


# ---------------------------------------------------------------------------
# Scenario Validation Tests
# ---------------------------------------------------------------------------

class TestScenarioDefinitions:

    def test_all_scenarios_have_required_fields(self):
        for s in INCIDENT_SCENARIOS:
            assert s.id,               f"Scenario missing id"
            assert s.severity,         f"{s.id}: missing severity"
            assert s.category,         f"{s.id}: missing category"
            assert s.title,            f"{s.id}: missing title"
            assert s.description,      f"{s.id}: missing description"
            assert s.success_criteria, f"{s.id}: must have at least one success criterion"

    def test_severity_values_are_valid(self):
        valid_severities = {"P0", "P1", "P2", "P3"}
        for s in INCIDENT_SCENARIOS:
            assert s.severity in valid_severities, (
                f"{s.id}: invalid severity '{s.severity}'"
            )

    def test_scenario_ids_are_unique(self):
        ids = [s.id for s in INCIDENT_SCENARIOS]
        assert len(ids) == len(set(ids)), f"Duplicate scenario IDs: {ids}"

    def test_scenario_ids_are_slugs(self):
        """IDs should be lowercase hyphenated slugs."""
        import re
        for s in INCIDENT_SCENARIOS:
            assert re.match(r'^[a-z0-9-]+$', s.id), (
                f"{s.id}: ID must be lowercase alphanumeric with hyphens"
            )

    def test_setup_commands_are_valid_bash_syntax(self):
        """All setup commands must parse as valid bash."""
        for scenario in INCIDENT_SCENARIOS:
            for cmd in scenario.system_state.get("setup_commands", []):
                result = subprocess.run(
                    ["bash", "-n", "-c", cmd],
                    capture_output=True, text=True
                )
                assert result.returncode == 0, (
                    f"{scenario.id}: invalid bash syntax: {cmd!r}\n"
                    f"Error: {result.stderr}"
                )

    def test_success_criteria_are_valid_bash_syntax(self):
        for scenario in INCIDENT_SCENARIOS:
            for cmd in scenario.success_criteria:
                result = subprocess.run(
                    ["bash", "-n", "-c", cmd],
                    capture_output=True, text=True
                )
                assert result.returncode == 0, (
                    f"{scenario.id}: invalid success criterion: {cmd!r}"
                )

    def test_at_least_one_p0_scenario(self):
        p0 = [s for s in INCIDENT_SCENARIOS if s.severity == "P0"]
        assert len(p0) >= 1, "Must have at least one P0 (critical) scenario"

    def test_all_categories_covered(self):
        categories = {s.category for s in INCIDENT_SCENARIOS}
        required = {"service", "disk", "memory", "cpu"}
        missing = required - categories
        assert not missing, f"Missing scenario categories: {missing}"


# ---------------------------------------------------------------------------
# Reward Function Tests
# ---------------------------------------------------------------------------

class TestRewardFunction:

    def test_reward_in_valid_range(self, mock_agent_result, mock_ctx):
        scenario = INCIDENT_SCENARIOS[0]
        reward, details = compute_incident_reward(scenario, mock_agent_result, mock_ctx)
        assert 0.0 <= reward <= 1.0, f"Reward {reward} out of range [0, 1]"

    def test_reward_details_has_required_keys(self, mock_agent_result, mock_ctx):
        scenario = INCIDENT_SCENARIOS[0]
        _, details = compute_incident_reward(scenario, mock_agent_result, mock_ctx)
        required_keys = {"scenario_id", "severity", "component_scores", "turns_used"}
        for key in required_keys:
            assert key in details, f"Missing key in reward details: {key}"

    def test_component_scores_sum_to_at_most_one(self, mock_agent_result, mock_ctx):
        scenario = INCIDENT_SCENARIOS[0]
        reward, details = compute_incident_reward(scenario, mock_agent_result, mock_ctx)
        component_sum = sum(details["component_scores"].values())
        assert component_sum <= 1.001, (
            f"Component scores sum to {component_sum}, expected <= 1.0"
        )

    def test_rca_keywords_boost_score(self, mock_ctx):
        """Agent that mentions root cause should score higher on RCA component."""
        good = MagicMock()
        good.turns_used = 5
        good.messages = [
            {"role": "assistant",
             "content": "I identified the root cause: the process was leaking memory because of a bug in the allocation code. The issue is now resolved.",
             "tool_calls": [MagicMock()]},
        ]

        bad = MagicMock()
        bad.turns_used = 5
        bad.messages = [
            {"role": "assistant", "content": "Done.", "tool_calls": []},
        ]

        scenario = INCIDENT_SCENARIOS[0]
        reward_good, details_good = compute_incident_reward(scenario, good, mock_ctx)
        reward_bad, details_bad = compute_incident_reward(scenario, bad, mock_ctx)

        assert details_good["component_scores"]["rca"] >= details_bad["component_scores"]["rca"]

    def test_speed_bonus_for_fast_resolution(self, mock_ctx):
        """Faster resolution (fewer turns) should yield higher speed score."""
        fast = MagicMock()
        fast.turns_used = 4
        fast.messages = [{"role": "assistant", "content": "Fixed", "tool_calls": []}]

        slow = MagicMock()
        slow.turns_used = 25
        slow.messages = [{"role": "assistant", "content": "Fixed", "tool_calls": []}]

        scenario = INCIDENT_SCENARIOS[0]
        _, fast_details = compute_incident_reward(scenario, fast, mock_ctx)
        _, slow_details = compute_incident_reward(scenario, slow, mock_ctx)

        assert fast_details["component_scores"]["speed"] >= slow_details["component_scores"]["speed"]

    def test_efficiency_penalty_for_excessive_tool_calls(self, mock_ctx):
        efficient = MagicMock()
        efficient.turns_used = 8
        efficient.messages = [
            {"role": "assistant", "content": None, "tool_calls": [MagicMock()]}
        ] * 8  # 8 tool calls

        inefficient = MagicMock()
        inefficient.turns_used = 35
        inefficient.messages = [
            {"role": "assistant", "content": None, "tool_calls": [MagicMock()]}
        ] * 35  # 35 tool calls

        scenario = INCIDENT_SCENARIOS[0]
        _, e_details = compute_incident_reward(scenario, efficient, mock_ctx)
        _, i_details = compute_incident_reward(scenario, inefficient, mock_ctx)

        assert e_details["component_scores"]["efficiency"] >= i_details["component_scores"]["efficiency"]

    def test_report_score_when_report_written(self, mock_agent_result, tmp_path):
        """If agent wrote a report file, report score should be > 0."""
        # Set up mock ctx that reports a file exists
        ctx = MagicMock()

        def fake_terminal(cmd, timeout=10):
            if "incidents" in cmd and "wc -l" in cmd:
                return {"exit_code": 0, "output": "1"}  # 1 report written
            if "skills" in cmd and "wc -l" in cmd:
                return {"exit_code": 0, "output": "3"}
            return {"exit_code": 0, "output": ""}

        ctx.terminal = fake_terminal

        scenario = INCIDENT_SCENARIOS[0]
        _, details = compute_incident_reward(scenario, mock_agent_result, ctx)
        assert details["component_scores"]["report"] == 0.15

    def test_skill_score_when_skill_created(self, mock_agent_result, tmp_path):
        ctx = MagicMock()

        def fake_terminal(cmd, timeout=10):
            if "incidents" in cmd:
                return {"exit_code": 0, "output": "0"}
            if "skills" in cmd and "wc -l" in cmd:
                return {"exit_code": 0, "output": "8"}  # > 5 = agent created skills
            return {"exit_code": 0, "output": ""}

        ctx.terminal = fake_terminal
        scenario = INCIDENT_SCENARIOS[0]
        _, details = compute_incident_reward(scenario, mock_agent_result, ctx)
        assert details["skill_created"] is True


# ---------------------------------------------------------------------------
# Skill File Tests
# ---------------------------------------------------------------------------

class TestSkillFile:

    SKILL_PATH = Path(__file__).parent.parent / "skills" / "incident-commander" / "SKILL.md"

    def test_skill_file_exists(self):
        assert self.SKILL_PATH.exists(), f"SKILL.md not found at {self.SKILL_PATH}"

    def test_skill_has_yaml_frontmatter(self):
        content = self.SKILL_PATH.read_text()
        assert content.startswith("---"), "SKILL.md must start with YAML frontmatter (---)"

    def test_skill_frontmatter_has_required_fields(self):
        content = self.SKILL_PATH.read_text()
        assert "name:" in content
        assert "description:" in content
        assert "license:" in content

    def test_skill_name_matches_directory(self):
        content = self.SKILL_PATH.read_text()
        assert "name: incident-commander" in content

    def test_skill_under_500_lines(self):
        lines = self.SKILL_PATH.read_text().splitlines()
        assert len(lines) <= 500, (
            f"SKILL.md has {len(lines)} lines; keep under 500 for context efficiency"
        )

    def test_skill_contains_core_sections(self):
        content = self.SKILL_PATH.read_text().lower()
        required_sections = ["detect", "triage", "diagnose", "remediate", "verify"]
        for section in required_sections:
            assert section in content, f"SKILL.md missing section: {section}"

    def test_skill_mentions_hermes_features(self):
        content = self.SKILL_PATH.read_text().lower()
        hermes_features = ["memory", "gateway", "cron", "subagent", "skill"]
        mentioned = sum(1 for f in hermes_features if f in content)
        assert mentioned >= 4, (
            f"SKILL.md should mention Hermes features. Found {mentioned}/5: {hermes_features}"
        )


# ---------------------------------------------------------------------------
# Integration: Demo Script Syntax Check
# ---------------------------------------------------------------------------

class TestDemoScript:

    DEMO_PATH = Path(__file__).parent.parent / "demo" / "demo_incident.py"

    def test_demo_script_exists(self):
        assert self.DEMO_PATH.exists()

    def test_demo_script_valid_python_syntax(self):
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(self.DEMO_PATH)],
            capture_output=True, text=True
        )
        assert result.returncode == 0, f"Syntax error: {result.stderr}"

    def test_demo_scenarios_have_required_keys(self):
        # Import demo module to access DEMO_SCENARIOS
        import importlib.util
        spec = importlib.util.spec_from_file_location("demo", self.DEMO_PATH)
        demo = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(demo)

        for name, scenario in demo.DEMO_SCENARIOS.items():
            assert "title" in scenario,    f"{name}: missing title"
            assert "severity" in scenario, f"{name}: missing severity"
            assert "prompt" in scenario,   f"{name}: missing prompt"
            assert "setup" in scenario,    f"{name}: missing setup commands"
            assert "cleanup" in scenario,  f"{name}: missing cleanup commands"


# ---------------------------------------------------------------------------
# Smoke test runner (can be run standalone)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Running smoke tests directly...")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", __file__, "-v", "--tb=short"],
        cwd=Path(__file__).parent.parent
    )
    sys.exit(result.returncode)
