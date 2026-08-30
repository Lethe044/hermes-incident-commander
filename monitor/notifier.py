"""
Hermes Incident Commander — Notifier
=====================================
Zero-dependency Discord / Slack webhook notifications. Uses only the Python
standard library (urllib) so it never adds a new pip dependency.

Configure via environment variables (recommended — never commit webhook
URLs to source control):

    export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."
    export SLACK_WEBHOOK_URL="https://hooks.slack.com/services/..."

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


@dataclass
class NotifyResult:
    channel: str
    ok: bool
    detail: str = ""


class Notifier:
    """Sends alerts to whichever webhooks are configured. Silently no-ops
    (but still returns a result you can log) if nothing is configured, so
    it's always safe to call — no need to sprinkle `if configured:` checks
    through calling code."""

    def __init__(
        self,
        discord_webhook_url: str | None = None,
        slack_webhook_url: str | None = None,
        timeout: int = 10,
    ):
        self.discord_webhook_url = discord_webhook_url or os.environ.get("DISCORD_WEBHOOK_URL")
        self.slack_webhook_url = slack_webhook_url or os.environ.get("SLACK_WEBHOOK_URL")
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.discord_webhook_url or self.slack_webhook_url)

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

    # -- convenience templates (mirrors README notification templates) -

    def send_p0_alert(self, service: str, impact: str) -> list[NotifyResult]:
        return self.send(
            f"Service: {service}\nImpact: {impact}\n"
            f"Hermes is investigating. Updates will follow.",
            title="🚨 P0 INCIDENT DECLARED",
        )

    def send_alert(self, severity: str, title_text: str, detail: str) -> list[NotifyResult]:
        emoji = {"P0": "🚨", "P1": "🔴", "P2": "🟠", "P3": "🟡"}.get(severity, "ℹ️")
        return self.send(detail, title=f"{emoji} {severity} — {title_text}")

    def send_resolution(self, duration_minutes: float, root_cause: str, report_path: str = "") -> list[NotifyResult]:
        msg = f"Duration: {duration_minutes:.1f} min\nRoot cause: {root_cause}"
        if report_path:
            msg += f"\nFull report: {report_path}"
        return self.send(msg, title="✅ INCIDENT RESOLVED")

    def send_daily_briefing(self, summary: str) -> list[NotifyResult]:
        return self.send(summary, title="📋 Daily Incident Briefing")


def test_notify() -> None:
    """CLI helper: `python -m monitor.notifier` sends a test message to every
    configured webhook so you can verify your setup before relying on it."""
    notifier = Notifier()
    if not notifier.configured:
        print("No webhooks configured. Set DISCORD_WEBHOOK_URL and/or SLACK_WEBHOOK_URL.")
        return
    results = notifier.send(
        "This is a test notification from Hermes Incident Commander.",
        title="🧪 Test Alert",
    )
    for r in results:
        status = "OK" if r.ok else "FAILED"
        print(f"[{status}] {r.channel}: {r.detail}")


if __name__ == "__main__":
    test_notify()
