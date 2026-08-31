"""
Hermes Incident Commander - Standalone Monitoring Toolkit
===========================================================
This package lets Incident Commander run as a real, always-on watchdog on a
Linux host WITHOUT requiring a full Hermes Agent installation. All you need
is an ANTHROPIC_API_KEY.

Modules:
    watchdog             - polls real system metrics (psutil) and triggers
                            Claude-based triage + safe, allow-listed
                            auto-remediation.
    notifier             - sends Discord / Slack / PagerDuty alerts.
    dashboard            - renders a single self-contained HTML incident
                            dashboard.
    prometheus_exporter  - optional /metrics endpoint for existing
                            Prometheus/Grafana stacks.
"""

__all__ = ["dashboard", "notifier", "prometheus_exporter", "watchdog"]
