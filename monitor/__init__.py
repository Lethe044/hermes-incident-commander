"""
Hermes Incident Commander — Standalone Monitoring Toolkit
===========================================================
This package lets Incident Commander run as a real, always-on watchdog on a
Linux host WITHOUT requiring a full Hermes Agent installation. All you need
is an ANTHROPIC_API_KEY.

Modules:
    watchdog   — polls real system metrics (psutil) and triggers Claude-based
                 triage + safe, allow-listed auto-remediation.
    notifier   — sends Discord / Slack webhook alerts.
    dashboard  — renders a single self-contained HTML incident dashboard.
"""

__all__ = ["dashboard", "notifier", "watchdog"]
