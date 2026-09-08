#!/usr/bin/env python3
"""
Hermes Incident Commander - Standalone Watchdog
==================================================
Continuously monitors REAL host metrics (CPU, memory, disk, failed systemd
units) and, when a threshold is breached for several consecutive checks,
asks Claude to triage and diagnose the situation, writes a structured
incident report, sends a Discord/Slack alert, and - only if you explicitly
opt in - performs SAFE, allow-listed auto-remediation (restart a whitelisted
service, clean a whitelisted log directory). It never lets the model run
arbitrary shell commands on your machine.

This module does NOT require a Hermes Agent installation - only
`pip install psutil anthropic pyyaml` and an ANTHROPIC_API_KEY. This makes
Incident Commander usable as a real always-on tool, not just a hackathon demo.

Usage:
    export ANTHROPIC_API_KEY=sk-ant-...
    python -m monitor.watchdog                       # observe-only, safe default
    python -m monitor.watchdog --once                 # single check, good for cron/CI
    python -m monitor.watchdog --dry-run               # preview what auto-remediation would do
    python -m monitor.watchdog --auto-remediate        # opt in to safe auto-fixes
    python -m monitor.watchdog --config monitor/watchdog_config.yaml
    python -m monitor.watchdog --config monitor/watchdog_config.yaml --validate-config
    python -m monitor.watchdog --metrics-port 9877      # also serve Prometheus /metrics

See SAFETY.md for the full threat model of --auto-remediate.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

try:
    import yaml
    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False

try:
    import anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from monitor.notifier import Notifier
from monitor.prometheus_exporter import start_metrics_server, update_latest_metrics

INCIDENT_DIR = Path.home() / ".hermes" / "incidents"
HISTORY_LOG = INCIDENT_DIR / "history.jsonl"
OPEN_INCIDENTS_FILE = INCIDENT_DIR / "open_pagerduty_incidents.json"

DEFAULT_MODEL = "claude-sonnet-4-20250514"


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class WatchdogConfig:
    cpu_threshold: float = 90.0
    mem_threshold: float = 90.0
    disk_threshold: float = 90.0
    disk_path: str = "/"
    poll_interval_seconds: int = 60
    consecutive_breaches_required: int = 3
    cooldown_minutes: int = 15
    watched_services: list[str] = field(default_factory=list)
    remediation_allowlist: dict[str, Any] = field(default_factory=lambda: {
        "restart_services": [],   # subset of watched_services allowed to be restarted
        "clean_log_dirs": [],     # dirs where old/large files may be deleted
        "max_log_age_days": 14,
    })
    auto_remediate: bool = False
    adaptive_thresholds: bool = False
    model: str = DEFAULT_MODEL

    @classmethod
    def from_file(cls, path: str) -> WatchdogConfig:
        if not YAML_AVAILABLE:
            raise RuntimeError("pyyaml is required to load a config file (`pip install pyyaml`)")
        with open(path) as f:
            raw = yaml.safe_load(f) or {}
        cfg = cls()
        for k, v in raw.items():
            if hasattr(cfg, k):
                setattr(cfg, k, v)
        return cfg


def validate_config(path: str) -> tuple[list[str], list[str]]:
    """Checks a watchdog config YAML file for typos and invalid allow-list
    entries without starting the watchdog. Returns (errors, warnings) -
    errors mean the config can't be used safely as-is, warnings are things
    worth a second look but won't stop the watchdog from running.

    Kept as a pure function (no printing, no sys.exit) so it can be tested
    directly and reused by anything other than the CLI later."""
    errors: list[str] = []
    warnings: list[str] = []

    if not YAML_AVAILABLE:
        errors.append("pyyaml is required to validate a config file (`pip install pyyaml`)")
        return errors, warnings

    if not os.path.isfile(path):
        errors.append(f"Config file not found: {path}")
        return errors, warnings

    try:
        with open(path) as f:
            raw = yaml.safe_load(f) or {}
    except yaml.YAMLError as exc:
        errors.append(f"Invalid YAML syntax: {exc}")
        return errors, warnings

    if not isinstance(raw, dict):
        errors.append("Config file must contain a YAML mapping (key: value pairs) at the top level")
        return errors, warnings

    known_fields = {f.name for f in dataclasses.fields(WatchdogConfig)}
    for key in raw:
        if key not in known_fields:
            warnings.append(f"Unknown key '{key}' - not a recognized setting (typo?), it will be ignored")

    cfg = WatchdogConfig.from_file(path)

    for name in ("cpu_threshold", "mem_threshold", "disk_threshold"):
        value = getattr(cfg, name)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            errors.append(f"'{name}' must be a number, got {value!r}")
        elif not (0 <= value <= 100):
            errors.append(f"'{name}' should be between 0 and 100, got {value}")

    if not isinstance(cfg.poll_interval_seconds, int) or isinstance(cfg.poll_interval_seconds, bool) \
            or cfg.poll_interval_seconds <= 0:
        errors.append(f"'poll_interval_seconds' must be a positive integer, got {cfg.poll_interval_seconds!r}")

    if not isinstance(cfg.consecutive_breaches_required, int) or isinstance(cfg.consecutive_breaches_required, bool) \
            or cfg.consecutive_breaches_required < 1:
        errors.append(
            f"'consecutive_breaches_required' must be an integer >= 1, got {cfg.consecutive_breaches_required!r}"
        )

    if not isinstance(cfg.cooldown_minutes, int) or isinstance(cfg.cooldown_minutes, bool) \
            or cfg.cooldown_minutes < 0:
        errors.append(f"'cooldown_minutes' must be an integer >= 0, got {cfg.cooldown_minutes!r}")

    if not isinstance(cfg.disk_path, str) or not cfg.disk_path:
        errors.append(f"'disk_path' must be a non-empty string, got {cfg.disk_path!r}")
    elif not os.path.exists(cfg.disk_path):
        warnings.append(f"'disk_path' ({cfg.disk_path}) does not exist on this host")

    if not isinstance(cfg.watched_services, list) or not all(isinstance(s, str) for s in cfg.watched_services):
        errors.append("'watched_services' must be a list of strings")

    if not isinstance(cfg.remediation_allowlist, dict):
        errors.append("'remediation_allowlist' must be a mapping")
    else:
        restart_services = cfg.remediation_allowlist.get("restart_services", [])
        clean_log_dirs = cfg.remediation_allowlist.get("clean_log_dirs", [])
        max_log_age_days = cfg.remediation_allowlist.get("max_log_age_days", 14)

        if not isinstance(restart_services, list) or not all(isinstance(s, str) for s in restart_services):
            errors.append("'remediation_allowlist.restart_services' must be a list of strings")
        elif isinstance(cfg.watched_services, list):
            watched = set(cfg.watched_services)
            for svc in restart_services:
                if svc not in watched:
                    warnings.append(
                        f"'{svc}' is allow-listed to restart but not in 'watched_services' - "
                        "the watchdog won't detect it as failing, so this entry has no effect"
                    )

        if not isinstance(clean_log_dirs, list) or not all(isinstance(d, str) for d in clean_log_dirs):
            errors.append("'remediation_allowlist.clean_log_dirs' must be a list of strings")
        else:
            for d in clean_log_dirs:
                if not os.path.isdir(d):
                    warnings.append(f"'remediation_allowlist.clean_log_dirs' entry '{d}' does not exist on this host")

        if not isinstance(max_log_age_days, int) or isinstance(max_log_age_days, bool) or max_log_age_days < 0:
            errors.append(
                f"'remediation_allowlist.max_log_age_days' must be an integer >= 0, got {max_log_age_days!r}"
            )

    if cfg.auto_remediate and not isinstance(cfg.auto_remediate, bool):
        errors.append(f"'auto_remediate' must be a boolean, got {cfg.auto_remediate!r}")
    if not isinstance(cfg.model, str) or not cfg.model:
        errors.append(f"'model' must be a non-empty string, got {cfg.model!r}")

    if cfg.auto_remediate and not restart_services_and_clean_dirs_present(cfg):
        warnings.append(
            "'auto_remediate' is enabled but the allow-list has no 'restart_services' or "
            "'clean_log_dirs' entries - auto-remediation will never do anything"
        )

    return errors, warnings


def restart_services_and_clean_dirs_present(cfg: WatchdogConfig) -> bool:
    allowlist = cfg.remediation_allowlist if isinstance(cfg.remediation_allowlist, dict) else {}
    return bool(allowlist.get("restart_services")) or bool(allowlist.get("clean_log_dirs"))


# ---------------------------------------------------------------------------
# Metric collection (real, read-only - no shell exec required)
# ---------------------------------------------------------------------------

@dataclass
class Metrics:
    timestamp: str
    cpu_percent: float
    mem_percent: float
    disk_percent: float
    failed_services: list[str]

    def breaches(self, cfg: WatchdogConfig) -> dict[str, bool]:
        cpu_threshold = cfg.cpu_threshold
        mem_threshold = cfg.mem_threshold
        disk_threshold = cfg.disk_threshold

        if cfg.adaptive_thresholds:
            # Never let a baseline/parsing problem break breach detection -
            # worst case, we silently fall back to the static thresholds.
            try:
                from monitor import baseline
                hour = datetime.fromisoformat(self.timestamp.replace("Z", "+00:00")).hour
                cpu_threshold = baseline.get_adaptive_threshold("cpu", hour, cfg.cpu_threshold)
                mem_threshold = baseline.get_adaptive_threshold("mem", hour, cfg.mem_threshold)
                disk_threshold = baseline.get_adaptive_threshold("disk", hour, cfg.disk_threshold)
            except Exception:
                pass

        return {
            "cpu": self.cpu_percent >= cpu_threshold,
            "mem": self.mem_percent >= mem_threshold,
            "disk": self.disk_percent >= disk_threshold,
            "service": bool(self.failed_services),
        }


def collect_metrics(cfg: WatchdogConfig) -> Metrics:
    if not PSUTIL_AVAILABLE:
        raise RuntimeError("psutil is required for the watchdog (`pip install psutil`)")

    cpu = psutil.cpu_percent(interval=1)
    mem = psutil.virtual_memory().percent
    disk = psutil.disk_usage(cfg.disk_path).percent

    failed: list[str] = []
    if cfg.watched_services:
        for svc in cfg.watched_services:
            try:
                result = subprocess.run(
                    ["systemctl", "is-failed", svc],
                    capture_output=True, text=True, timeout=5,
                )
                if result.stdout.strip() == "failed":
                    failed.append(svc)
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass  # systemd not available (e.g. macOS, containers) - skip gracefully

    return Metrics(
        timestamp=datetime.now(timezone.utc).isoformat(),
        cpu_percent=cpu,
        mem_percent=mem,
        disk_percent=disk,
        failed_services=failed,
    )


def _maybe_update_baseline(cfg: WatchdogConfig, metrics: Metrics) -> None:
    """Feeds this poll's sample into monitor/baseline.py's per-hour running
    stats, if `adaptive_thresholds` is enabled. Never lets a baseline
    problem interrupt monitoring."""
    if not cfg.adaptive_thresholds:
        return
    try:
        from monitor import baseline
        hour = datetime.fromisoformat(metrics.timestamp.replace("Z", "+00:00")).hour
        baseline.update_baseline(
            {"cpu": metrics.cpu_percent, "mem": metrics.mem_percent, "disk": metrics.disk_percent},
            hour,
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Claude-based triage (analysis only - no tool use, no shell access for the model)
# ---------------------------------------------------------------------------

TRIAGE_SYSTEM_PROMPT = """You are Hermes Incident Commander, an SRE assistant performing
remote triage from a metrics snapshot. You do NOT have direct shell access - you only see
the numbers provided. Respond with ONLY a JSON object (no markdown fences, no prose)
with exactly these keys:

{
  "severity": "P0" | "P1" | "P2" | "P3",
  "category": "cpu" | "memory" | "disk" | "service" | "network" | "docker",
  "root_cause_hypothesis": "<one or two sentences>",
  "recommended_actions": ["<short imperative action>", "..."],
  "report_markdown": "<a full post-incident report in markdown, following the standard
      Hermes Incident Commander template: Date, Severity, Duration, Impact, Timeline,
      Root Cause, Remediation Steps, Prevention, Metrics>"
}"""


def triage_with_claude(metrics: Metrics, breaches: dict[str, bool], cfg: WatchdogConfig) -> dict[str, Any]:
    if not ANTHROPIC_AVAILABLE:
        raise RuntimeError("anthropic SDK is required (`pip install anthropic`)")

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("Set ANTHROPIC_API_KEY to use Claude-based triage")

    client = anthropic.Anthropic(api_key=api_key)
    breached = [k for k, v in breaches.items() if v]

    user_prompt = (
        f"Metrics snapshot at {metrics.timestamp}:\n"
        f"- CPU usage: {metrics.cpu_percent:.1f}% (threshold {cfg.cpu_threshold:.0f}%)\n"
        f"- Memory usage: {metrics.mem_percent:.1f}% (threshold {cfg.mem_threshold:.0f}%)\n"
        f"- Disk usage ({cfg.disk_path}): {metrics.disk_percent:.1f}% (threshold {cfg.disk_threshold:.0f}%)\n"
        f"- Failed systemd services: {metrics.failed_services or 'none'}\n"
        f"- Breached thresholds (sustained for {cfg.consecutive_breaches_required} consecutive "
        f"checks): {breached}\n\n"
        f"Recommended actions may only reference these allow-listed operations - do not "
        f"invent others: restart one of {cfg.remediation_allowlist.get('restart_services', [])}, "
        f"or clean old files in one of {cfg.remediation_allowlist.get('clean_log_dirs', [])}.\n"
        f"Produce the JSON object now."
    )

    response = client.messages.create(
        model=cfg.model,
        max_tokens=1500,
        system=TRIAGE_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )

    text = "".join(b.text for b in response.content if b.type == "text").strip()
    for fence in ("```json", "```"):
        text = text.removeprefix(fence)
    text = text.removesuffix("```")
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Fall back to a minimal structured result rather than crashing the loop
        return {
            "severity": "P2",
            "category": "unknown",
            "root_cause_hypothesis": "Model response was not valid JSON; see raw_response.",
            "recommended_actions": [],
            "report_markdown": f"# Incident Report (fallback)\n\nRaw model response:\n\n{text}",
            "raw_response": text,
        }


# ---------------------------------------------------------------------------
# Safe, allow-listed remediation - NEVER arbitrary shell execution
# ---------------------------------------------------------------------------

def safe_remediate(diagnosis: dict[str, Any], cfg: WatchdogConfig, dry_run: bool = False) -> list[str]:
    """Execute only actions that match the allow-list. Returns a list of
    human-readable descriptions of what was actually done (or, in dry-run
    mode, what *would* be done - nothing is executed or deleted)."""
    performed: list[str] = []
    if not cfg.auto_remediate and not dry_run:
        return performed

    actions = diagnosis.get("recommended_actions", [])
    restart_allowed = set(cfg.remediation_allowlist.get("restart_services", []))
    clean_allowed = cfg.remediation_allowlist.get("clean_log_dirs", [])
    max_age_days = cfg.remediation_allowlist.get("max_log_age_days", 14)

    for action in actions:
        action_lower = action.lower()

        for svc in restart_allowed:
            if svc.lower() in action_lower and "restart" in action_lower:
                if dry_run:
                    performed.append(f"[DRY RUN] Would restart allow-listed service: {svc}")
                    continue
                try:
                    subprocess.run(["systemctl", "restart", svc], timeout=15, check=False)
                    performed.append(f"Restarted allow-listed service: {svc}")
                except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
                    performed.append(f"Failed to restart {svc}: {exc}")

        for log_dir in clean_allowed:
            if log_dir in action or "clean" in action_lower or "log" in action_lower:
                removed = _clean_old_files(log_dir, max_age_days, dry_run=dry_run)
                if removed:
                    verb = "Would remove" if dry_run else "Removed"
                    prefix = "[DRY RUN] " if dry_run else ""
                    performed.append(
                        f"{prefix}{verb} {removed} file(s) older than {max_age_days}d from {log_dir}"
                    )

    return performed


def _clean_old_files(directory: str, max_age_days: int, dry_run: bool = False) -> int:
    path = Path(directory)
    if not path.exists() or not path.is_dir():
        return 0
    cutoff = time.time() - max_age_days * 86400
    matched = 0
    for f in path.glob("*.log*"):
        try:
            if f.is_file() and f.stat().st_mtime < cutoff:
                if not dry_run:
                    f.unlink()
                matched += 1
        except OSError:
            continue
    return matched


# ---------------------------------------------------------------------------
# PagerDuty open-incident tracking (so we know what to resolve, and when)
# ---------------------------------------------------------------------------

def _load_open_incidents() -> dict[str, str]:
    """Maps a breach key (cpu/mem/disk/service) to the PagerDuty dedup_key
    currently open for it. Persisted to disk so it survives across
    `--once` invocations from cron, not just within one `watch_forever`."""
    if not OPEN_INCIDENTS_FILE.exists():
        return {}
    try:
        return json.loads(OPEN_INCIDENTS_FILE.read_text()) or {}
    except (json.JSONDecodeError, OSError):
        return {}


def _save_open_incidents(open_incidents: dict[str, str]) -> None:
    INCIDENT_DIR.mkdir(parents=True, exist_ok=True)
    OPEN_INCIDENTS_FILE.write_text(json.dumps(open_incidents))


def _resolve_recovered_breaches(
    breaches: dict[str, bool], notifier: Notifier, quiet: bool = False
) -> dict[str, str]:
    """Compares current breaches against the persisted open-incidents file
    and resolves any PagerDuty incident whose breach has recovered. Returns
    the (possibly updated) open-incidents mapping."""
    open_incidents = _load_open_incidents()
    changed = False
    for key, is_breached in breaches.items():
        if not is_breached and key in open_incidents:
            dedup_key = open_incidents.pop(key)
            changed = True
            if notifier.pagerduty_routing_key:
                notifier.resolve_pagerduty_event(dedup_key)
                if not quiet:
                    print(f"  -> PagerDuty incident resolved: {dedup_key}")
    if changed:
        _save_open_incidents(open_incidents)
    return open_incidents


def _record_open_incident(breached_keys: list[str], open_incidents: dict[str, str]) -> str:
    """Registers a new PagerDuty dedup_key for the given breach keys and
    persists it. Returns the dedup_key to pass to notifier.send_alert()."""
    dedup_key = "hermes-ic-" + "-".join(sorted(breached_keys))
    for key in breached_keys:
        open_incidents[key] = dedup_key
    _save_open_incidents(open_incidents)
    return dedup_key


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def write_incident(metrics: Metrics, diagnosis: dict[str, Any], performed_actions: list[str]) -> Path:
    INCIDENT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    category = diagnosis.get("category", "unknown")
    slug = f"{ts}-{category}"
    report_path = INCIDENT_DIR / f"{slug}.md"

    # Best-effort flapping check: never let this block writing the report.
    flap_info: dict[str, Any] = {"is_flapping": False, "count": 1, "window_minutes": 0}
    try:
        from monitor import flapping
        flap_info = flapping.record_and_check(category, metrics.timestamp)
    except Exception:
        pass

    def _ordinal(n: int) -> str:
        if 10 <= n % 100 <= 20:
            suffix = "th"
        else:
            suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
        return f"{n}{suffix}"

    body = diagnosis.get("report_markdown", "").strip()
    if flap_info["is_flapping"]:
        body = (
            f"> ⚠️ **FLAPPING DETECTED**: this is the {_ordinal(flap_info['count'])} `{category}` "
            f"incident in the last {flap_info['window_minutes']} minutes. This usually means "
            f"either the root cause isn't actually being fixed, or the threshold for this "
            f"metric is tuned too tight for normal load (see `--adaptive-thresholds` in "
            f"README.md).\n\n"
        ) + body
    if performed_actions:
        body += "\n\n## Auto-Remediation Performed\n" + "\n".join(f"- {a}" for a in performed_actions)
    else:
        body += "\n\n## Auto-Remediation Performed\nNone (observe-only mode, or nothing matched the allow-list)."

    report_path.write_text(body + "\n")

    if flap_info["is_flapping"]:
        print(
            f"  ⚠️  FLAPPING: {flap_info['count']} '{category}' incidents in the last "
            f"{flap_info['window_minutes']} min - see {report_path.name}"
        )

    # Structured JSONL record - consumed by monitor/dashboard.py
    with open(HISTORY_LOG, "a") as f:
        f.write(json.dumps({
            "timestamp": metrics.timestamp,
            "severity": diagnosis.get("severity", "P3"),
            "category": category,
            "cpu_percent": metrics.cpu_percent,
            "mem_percent": metrics.mem_percent,
            "disk_percent": metrics.disk_percent,
            "root_cause": diagnosis.get("root_cause_hypothesis", ""),
            "auto_remediated": bool(performed_actions),
            "actions": performed_actions,
            "report_file": str(report_path.name),
            "flapping": flap_info["is_flapping"],
        }) + "\n")

    # Best-effort: keep the local search index (monitor/incident_db.py) in
    # sync so "have we seen this before?" works without a separate cron job.
    # Never let a search-index problem take down the watchdog itself.
    try:
        from monitor import incident_db
        incident_db.sync()
    except Exception:
        pass

    return report_path


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run_once(cfg: WatchdogConfig, notifier: Notifier, quiet: bool = False, dry_run: bool = False) -> Path | None:
    """Run a single check. Returns the incident report path if one was triggered."""
    metrics = collect_metrics(cfg)
    breaches = metrics.breaches(cfg)
    breached_any = any(breaches.values())
    update_latest_metrics(metrics, breaches)
    _maybe_update_baseline(cfg, metrics)

    open_incidents = _resolve_recovered_breaches(breaches, notifier, quiet=quiet)

    if not quiet:
        print(
            f"[{metrics.timestamp}] cpu={metrics.cpu_percent:.1f}% "
            f"mem={metrics.mem_percent:.1f}% disk={metrics.disk_percent:.1f}% "
            f"failed_services={metrics.failed_services or 'none'} "
            f"breach={breached_any}"
        )

    if not breached_any:
        return None

    diagnosis = triage_with_claude(metrics, breaches, cfg)
    severity = diagnosis.get("severity", "P2")

    performed = safe_remediate(diagnosis, cfg, dry_run=dry_run)
    report_path = write_incident(metrics, diagnosis, performed)

    if dry_run:
        # Don't page anyone or open a PagerDuty incident for a test run - just
        # show what would have happened. The report is still written so you
        # can review Claude's full diagnosis.
        if not quiet:
            print("  [DRY RUN] No notification sent, no PagerDuty incident opened.")
            for action in performed:
                print(f"  {action}")
        return report_path

    breached_keys = sorted(k for k, v in breaches.items() if v)
    dedup_key = _record_open_incident(breached_keys, open_incidents)

    if notifier.configured:
        notifier.send_alert(
            severity=severity,
            title_text=diagnosis.get("category", "incident"),
            detail=(
                f"{diagnosis.get('root_cause_hypothesis', 'See report for details.')}\n"
                f"Report: {report_path}"
                + (f"\nAuto-remediated: {', '.join(performed)}" if performed else "")
            ),
            dedup_key=dedup_key,
        )

    return report_path


def watch_forever(cfg: WatchdogConfig, notifier: Notifier, max_iterations: int | None = None) -> None:
    consecutive: dict[str, int] = {"cpu": 0, "mem": 0, "disk": 0, "service": 0}
    last_incident_at: dict[str, float] = {}
    iterations = 0

    print("Hermes Watchdog started. Observe-only" if not cfg.auto_remediate else
          "Hermes Watchdog started. Auto-remediation ENABLED (allow-listed actions only)")

    while True:
        metrics = collect_metrics(cfg)
        breaches = metrics.breaches(cfg)
        update_latest_metrics(metrics, breaches)
        _maybe_update_baseline(cfg, metrics)

        print(
            f"[{metrics.timestamp}] cpu={metrics.cpu_percent:.1f}% "
            f"mem={metrics.mem_percent:.1f}% disk={metrics.disk_percent:.1f}% "
            f"failed_services={metrics.failed_services or 'none'}"
        )

        open_incidents = _resolve_recovered_breaches(breaches, notifier)

        fire = False
        fired_keys: list[str] = []
        for key, is_breached in breaches.items():
            if is_breached:
                consecutive[key] += 1
            else:
                consecutive[key] = 0

            if consecutive[key] >= cfg.consecutive_breaches_required:
                now = time.time()
                cooled_down = now - last_incident_at.get(key, 0) > cfg.cooldown_minutes * 60
                if cooled_down:
                    fire = True
                    fired_keys.append(key)
                    last_incident_at[key] = now
                    consecutive[key] = 0

        if fire:
            diagnosis = triage_with_claude(metrics, breaches, cfg)
            performed = safe_remediate(diagnosis, cfg)
            report_path = write_incident(metrics, diagnosis, performed)
            dedup_key = _record_open_incident(fired_keys, open_incidents)
            print(f"  -> Incident written: {report_path}")

            if notifier.configured:
                notifier.send_alert(
                    severity=diagnosis.get("severity", "P2"),
                    title_text=diagnosis.get("category", "incident"),
                    detail=(
                        f"{diagnosis.get('root_cause_hypothesis', '')}\nReport: {report_path}"
                        + (f"\nAuto-remediated: {', '.join(performed)}" if performed else "")
                    ),
                    dedup_key=dedup_key,
                )

        iterations += 1
        if max_iterations is not None and iterations >= max_iterations:
            break
        time.sleep(cfg.poll_interval_seconds)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Hermes Incident Commander - Standalone Watchdog")
    parser.add_argument("--config", help="Path to a YAML config file")
    parser.add_argument("--once", action="store_true", help="Run a single check and exit (good for cron)")
    parser.add_argument("--auto-remediate", action="store_true", help="Opt in to safe, allow-listed auto-fixes")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Run a single check and show what --auto-remediate WOULD do, without doing it "
             "(nothing is restarted or deleted). Implies --once.",
    )
    parser.add_argument(
        "--metrics-port", type=int, default=None,
        help="Serve Prometheus-format metrics on this port at /metrics (binds 127.0.0.1)",
    )
    parser.add_argument(
        "--adaptive-thresholds", action="store_true",
        help="Learn a per-hour-of-day baseline and raise thresholds during normally-busy "
             "hours instead of using one static threshold all day (can only raise the bar, "
             "never lower it below your configured threshold; see monitor/baseline.py).",
    )
    parser.add_argument(
        "--show-baseline", action="store_true",
        help="Print the learned per-hour-of-day baseline (cpu/mem/disk) and exit",
    )
    parser.add_argument(
        "--validate-config", action="store_true",
        help="Check the file passed to --config for typos and invalid allow-list entries, "
             "print what it would resolve to, and exit without starting the watchdog",
    )
    parser.add_argument("--cpu-threshold", type=float, default=None)
    parser.add_argument("--mem-threshold", type=float, default=None)
    parser.add_argument("--disk-threshold", type=float, default=None)
    parser.add_argument("--disk-path", type=str, default=None)
    parser.add_argument("--interval", type=int, default=None, help="Poll interval in seconds")
    parser.add_argument("--no-notify", action="store_true", help="Disable Discord/Slack notifications")
    args = parser.parse_args()

    if args.show_baseline:
        from monitor import baseline
        summary = baseline.hour_summary()
        if not summary:
            print("No baseline data yet. Run with --adaptive-thresholds to start collecting it.")
        for metric, hours in summary.items():
            print(f"{metric}:")
            for hour_key, stats in hours.items():
                trust = "trusted" if stats["trusted"] else "not enough data yet"
                print(f"  {int(hour_key):02d}:00  n={stats['n']:<4} mean={stats['mean']:<6} stddev={stats['stddev']:<6} ({trust})")
        return

    if args.validate_config:
        if not args.config:
            print("Error: --validate-config requires --config PATH", file=sys.stderr)
            sys.exit(1)
        errors, warnings = validate_config(args.config)
        if not errors and not warnings:
            print(f"{args.config}: OK, no issues found.")
        for w in warnings:
            print(f"WARNING: {w}")
        for e in errors:
            print(f"ERROR: {e}")
        if not errors:
            cfg = WatchdogConfig.from_file(args.config)
            print("\nResolved config:")
            for f in dataclasses.fields(WatchdogConfig):
                print(f"  {f.name}: {getattr(cfg, f.name)!r}")
        sys.exit(1 if errors else 0)

    cfg = WatchdogConfig.from_file(args.config) if args.config else WatchdogConfig()
    if args.auto_remediate:
        cfg.auto_remediate = True
    if args.adaptive_thresholds:
        cfg.adaptive_thresholds = True
    if args.cpu_threshold is not None:
        cfg.cpu_threshold = args.cpu_threshold
    if args.mem_threshold is not None:
        cfg.mem_threshold = args.mem_threshold
    if args.disk_threshold is not None:
        cfg.disk_threshold = args.disk_threshold
    if args.disk_path is not None:
        cfg.disk_path = args.disk_path
    if args.interval is not None:
        cfg.poll_interval_seconds = args.interval

    notifier = Notifier() if not args.no_notify else Notifier(discord_webhook_url="", slack_webhook_url="")

    if not PSUTIL_AVAILABLE:
        print("Error: psutil is required. Install with: pip install psutil", file=sys.stderr)
        sys.exit(1)

    if args.metrics_port:
        start_metrics_server(args.metrics_port)
        print(f"Prometheus metrics available at http://127.0.0.1:{args.metrics_port}/metrics")

    if args.dry_run:
        print("Running in DRY RUN mode: showing what --auto-remediate would do, changing nothing.")
        report = run_once(cfg, notifier, dry_run=True)
        print(f"Incident triggered: {report}" if report else "No breach detected - all clear.")
    elif args.once:
        report = run_once(cfg, notifier)
        print(f"Incident triggered: {report}" if report else "No breach detected - all clear.")
    else:
        try:
            watch_forever(cfg, notifier)
        except KeyboardInterrupt:
            print("\nWatchdog stopped.")


if __name__ == "__main__":
    main()
