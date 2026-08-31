"""
Hermes Incident Commander - Notifier
=====================================
Zero-dependency Discord / Slack / PagerDuty notifications. Uses only the
Python standard library (urllib) so it never adds a new pip dependency.

Configure via environment variables (recommended - never commit webhook
URLs or routing keys to source control):

    export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."
    export SLACK_WEBHOOK_URL="https://hooks.slack.com/services/..."
    export PAGERDUTY_ROUTING_KEY="..."   # PagerDuty Events API v2 integration key

Usage:
    from monitor.notifier import Notifier

    notifier = Notifier()
    notifier.send_p0_alert(service="nginx", impact="Website unreachable")
    notifier.send_resolution(duration_minutes=4.2, root_cause="OOM-killed worker")
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass

PAGERDUTY_EVENTS_URL = "https://events.pagerduty.com/v2/enqueue"

# Maps this project's severity scale to PagerDuty's fixed event severities.
PAGERDUTY_SEVERITY_MAP = {
    "P0": "critical",
    "P1": "error",
    "P2": "warning",
    "P3": "info",
}


@dataclass
class NotifyResult:
    channel: str
    ok: bool
    detail: str = ""


class Notifier:
    """Sends alerts to whichever channels are configured. Silently no-ops
    (but still returns a result you can log) if nothing is configured, so
    it's always safe to call - no need to sprinkle `if configured:` checks
    through calling code."""

    def __init__(
        self,
        discord_webhook_url: str | None = None,
        slack_webhook_url: str | None = None,
        pagerduty_routing_key: str | None = None,
        timeout: int = 10,
    ):
        self.discord_webhook_url = discord_webhook_url or os.environ.get("DISCORD_WEBHOOK_URL")
        self.slack_webhook_url = slack_webhook_url or os.environ.get("SLACK_WEBHOOK_URL")
        self.pagerduty_routing_key = pagerduty_routing_key or os.environ.get("PAGERDUTY_ROUTING_KEY")
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(
            self.discord_webhook_url or self.slack_webhook_url or self.pagerduty_routing_key
        )

    # -- low level -----------------------------------------------------

    def _post_json(self, url: str, payload: dict) -> NotifyResult:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return NotifyResult(channel=url.split("/")[2], ok=200 <= resp.status < 300,
                                     detail=f"HTTP {resp.status}")
        except urllib.error.URLError as exc:
            return NotifyResult(channel=url.split("/")[2], ok=False, detail=str(exc))

    def send(self, message: str, title: str | None = None) -> list[NotifyResult]:
        """Send a plain-text style message to every configured channel."""
        results: list[NotifyResult] = []

        if self.discord_webhook_url:
            content = f"**{title}**\n{message}" if title else message
            results.append(self._post_json(self.discord_webhook_url, {"content": content[:1900]}))

        if self.slack_webhook_url:
            text = f"*{title}*\n{message}" if title else message
            results.append(self._post_json(self.slack_webhook_url, {"text": text[:3800]}))

        return results

    # -- PagerDuty (Events API v2) --------------------------------------

    def send_pagerduty_event(
        self,
        summary: str,
        severity: str = "error",
        source: str = "hermes-incident-commander",
        dedup_key: str | None = None,
        custom_details: dict | None = None,
    ) -> NotifyResult | None:
        """Trigger a PagerDuty alert via the Events API v2. Returns None (not
        a failure) if no routing key is configured, matching the no-op
        behaviour of `send()`. `dedup_key` lets you group repeated triggers
        into a single PagerDuty incident (and is required to resolve one via
        `resolve_pagerduty_event`)."""
        if not self.pagerduty_routing_key:
            return None

        pd_severity = severity if severity in PAGERDUTY_SEVERITY_MAP.values() else "error"
        payload: dict = {
            "routing_key": self.pagerduty_routing_key,
            "event_action": "trigger",
            "payload": {
                "summary": summary[:1024],
                "severity": pd_severity,
                "source": source,
                "custom_details": custom_details or {},
            },
        }
        if dedup_key:
            payload["dedup_key"] = dedup_key

        return self._post_json(PAGERDUTY_EVENTS_URL, payload)

    def resolve_pagerduty_event(self, dedup_key: str) -> NotifyResult | None:
        """Resolve a previously triggered PagerDuty incident by its dedup_key."""
        if not self.pagerduty_routing_key:
            return None
        payload = {
            "routing_key": self.pagerduty_routing_key,
            "event_action": "resolve",
            "dedup_key": dedup_key,
        }
        return self._post_json(PAGERDUTY_EVENTS_URL, payload)

    # -- convenience templates (mirrors README notification templates) -

    def send_p0_alert(self, service: str, impact: str) -> list[NotifyResult]:
        results = self.send(
            f"Service: {service}\nImpact: {impact}\n"
            f"Hermes is investigating. Updates will follow.",
            title="🚨 P0 INCIDENT DECLARED",
        )
        pd_result = self.send_pagerduty_event(
            summary=f"[P0] {service}: {impact}"[:1024],
            severity=PAGERDUTY_SEVERITY_MAP["P0"],
            custom_details={"service": service, "impact": impact},
        )
        if pd_result:
            results.append(pd_result)
        return results

    def send_alert(self, severity: str, title_text: str, detail: str) -> list[NotifyResult]:
        emoji = {"P0": "🚨", "P1": "🔴", "P2": "🟠", "P3": "🟡"}.get(severity, "ℹ️")
        results = self.send(detail, title=f"{emoji} {severity} - {title_text}")

        pd_result = self.send_pagerduty_event(
            summary=f"[{severity}] {title_text}: {detail.splitlines()[0]}"[:1024],
            severity=PAGERDUTY_SEVERITY_MAP.get(severity, "error"),
            custom_details={"severity": severity, "title": title_text, "detail": detail},
        )
        if pd_result:
            results.append(pd_result)
        return results

    def send_resolution(self, duration_minutes: float, root_cause: str, report_path: str = "") -> list[NotifyResult]:
        msg = f"Duration: {duration_minutes:.1f} min\nRoot cause: {root_cause}"
        if report_path:
            msg += f"\nFull report: {report_path}"
        return self.send(msg, title="✅ INCIDENT RESOLVED")

    def send_daily_briefing(self, summary: str) -> list[NotifyResult]:
        return self.send(summary, title="📋 Daily Incident Briefing")


def test_notify() -> None:
    """CLI helper: `python -m monitor.notifier` sends a test message to every
    configured channel so you can verify your setup before relying on it."""
    notifier = Notifier()
    if not notifier.configured:
        print(
            "No channels configured. Set DISCORD_WEBHOOK_URL, SLACK_WEBHOOK_URL, "
            "and/or PAGERDUTY_ROUTING_KEY."
        )
        return
    results = notifier.send(
        "This is a test notification from Hermes Incident Commander.",
        title="🧪 Test Alert",
    )
    if notifier.pagerduty_routing_key:
        pd_result = notifier.send_pagerduty_event(
            summary="Hermes Incident Commander test alert",
            severity="info",
            dedup_key="hermes-ic-test-notify",
        )
        if pd_result:
            results.append(pd_result)
    for r in results:
        status = "OK" if r.ok else "FAILED"
        print(f"[{status}] {r.channel}: {r.detail}")


if __name__ == "__main__":
    test_notify()
