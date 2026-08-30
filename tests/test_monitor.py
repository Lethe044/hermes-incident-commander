"""
Hermes Incident Commander — Monitor Package Test Suite
=========================================================
Covers monitor/notifier.py, monitor/watchdog.py, and monitor/dashboard.py.
No real network calls or real system mutation happen in these tests —
HTTP calls are mocked and filesystem operations are confined to tmp_path.

Run with:
    pytest tests/test_monitor.py -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from monitor.dashboard import bar_svg, load_from_jsonl, render_html
from monitor.notifier import Notifier
from monitor.watchdog import (
    Metrics,
    WatchdogConfig,
    _clean_old_files,
    safe_remediate,
)

# ---------------------------------------------------------------------------
# Notifier
# ---------------------------------------------------------------------------

class TestNotifier:

    def test_not_configured_by_default(self, monkeypatch):
        monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
        monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
        notifier = Notifier()
        assert notifier.configured is False

    def test_configured_via_env(self, monkeypatch):
        monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/x/y")
        notifier = Notifier()
        assert notifier.configured is True

    def test_send_noop_when_unconfigured(self):
        notifier = Notifier(discord_webhook_url=None, slack_webhook_url=None)
        results = notifier.send("hello", title="Test")
        assert results == []

    @patch("monitor.notifier.urllib.request.urlopen")
    def test_send_posts_to_discord(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 204
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        notifier = Notifier(discord_webhook_url="https://discord.com/api/webhooks/123/abc")
        results = notifier.send("hello world", title="Test Alert")

        assert len(results) == 1
        assert results[0].ok is True
        mock_urlopen.assert_called_once()

    @patch("monitor.notifier.urllib.request.urlopen")
    def test_send_p0_alert_formats_message(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        notifier = Notifier(slack_webhook_url="https://hooks.slack.com/services/x")
        results = notifier.send_p0_alert(service="nginx", impact="Website down")
        assert len(results) == 1
        assert results[0].ok is True

    @patch("monitor.notifier.urllib.request.urlopen", side_effect=Exception("boom"))
    def test_send_handles_network_errors_gracefully(self, mock_urlopen):
        # A raw Exception isn't caught (only URLError is) — but URLError must be
        import urllib.error
        with patch("monitor.notifier.urllib.request.urlopen", side_effect=urllib.error.URLError("no route")):
            notifier = Notifier(discord_webhook_url="https://discord.com/api/webhooks/1/2")
            results = notifier.send("hi")
            assert len(results) == 1
            assert results[0].ok is False


# ---------------------------------------------------------------------------
# Watchdog — pure logic (no real psutil polling required)
# ---------------------------------------------------------------------------

class TestWatchdogConfig:

    def test_defaults_are_sane(self):
        cfg = WatchdogConfig()
        assert 0 < cfg.cpu_threshold <= 100
        assert cfg.auto_remediate is False
        assert cfg.disk_path == "/"

    def test_from_file_overrides_defaults(self, tmp_path):
        config_file = tmp_path / "cfg.yaml"
        config_file.write_text(
            "cpu_threshold: 75\nauto_remediate: true\nwatched_services: [nginx]\n"
        )
        cfg = WatchdogConfig.from_file(str(config_file))
        assert cfg.cpu_threshold == 75
        assert cfg.auto_remediate is True
        assert cfg.watched_services == ["nginx"]
        # Untouched fields keep their defaults
        assert cfg.mem_threshold == 90.0


class TestMetricsBreaches:

    def test_breach_detection(self):
        cfg = WatchdogConfig(cpu_threshold=90, mem_threshold=90, disk_threshold=90)
        metrics = Metrics(
            timestamp="2026-01-01T00:00:00Z",
            cpu_percent=95.0,
            mem_percent=50.0,
            disk_percent=10.0,
            failed_services=["nginx"],
        )
        breaches = metrics.breaches(cfg)
        assert breaches["cpu"] is True
        assert breaches["mem"] is False
        assert breaches["disk"] is False
        assert breaches["service"] is True

    def test_no_breach_when_all_healthy(self):
        cfg = WatchdogConfig()
        metrics = Metrics(
            timestamp="2026-01-01T00:00:00Z",
            cpu_percent=10.0,
            mem_percent=20.0,
            disk_percent=30.0,
            failed_services=[],
        )
        breaches = metrics.breaches(cfg)
        assert not any(breaches.values())


class TestSafeRemediation:

    def test_no_remediation_when_auto_remediate_disabled(self):
        cfg = WatchdogConfig(auto_remediate=False)
        diagnosis = {"recommended_actions": ["restart nginx"]}
        performed = safe_remediate(diagnosis, cfg)
        assert performed == []

    def test_ignores_actions_not_on_allowlist(self):
        cfg = WatchdogConfig(
            auto_remediate=True,
            remediation_allowlist={"restart_services": [], "clean_log_dirs": [], "max_log_age_days": 14},
        )
        diagnosis = {"recommended_actions": ["restart some-random-service"]}
        performed = safe_remediate(diagnosis, cfg)
        assert performed == []

    def test_cleans_allowlisted_log_dir(self, tmp_path):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        old_file = log_dir / "app.log.1"
        old_file.write_text("x" * 100)
        # Make it look old
        import os
        import time
        old_time = time.time() - 30 * 86400
        os.utime(old_file, (old_time, old_time))

        cfg = WatchdogConfig(
            auto_remediate=True,
            remediation_allowlist={
                "restart_services": [],
                "clean_log_dirs": [str(log_dir)],
                "max_log_age_days": 14,
            },
        )
        diagnosis = {"recommended_actions": [f"clean old logs in {log_dir}"]}
        performed = safe_remediate(diagnosis, cfg)
        assert len(performed) == 1
        assert not old_file.exists()

    def test_clean_old_files_respects_age_cutoff(self, tmp_path):
        recent = tmp_path / "recent.log"
        recent.write_text("fresh")
        removed = _clean_old_files(str(tmp_path), max_age_days=14)
        assert removed == 0
        assert recent.exists()

    def test_clean_old_files_missing_dir_is_safe(self):
        assert _clean_old_files("/nonexistent/path/xyz", 14) == 0


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

class TestDashboard:

    def test_load_from_jsonl(self, tmp_path, monkeypatch):
        history = tmp_path / "history.jsonl"
        records = [
            {"timestamp": "2026-01-01T00:00:00Z", "severity": "P0", "category": "cpu"},
            {"timestamp": "2026-01-02T00:00:00Z", "severity": "P2", "category": "disk"},
        ]
        history.write_text("\n".join(json.dumps(r) for r in records) + "\n")

        import monitor.dashboard as dash
        monkeypatch.setattr(dash, "HISTORY_LOG", history)
        loaded = load_from_jsonl()
        assert len(loaded) == 2
        assert loaded[0]["severity"] == "P0"

    def test_load_from_jsonl_skips_malformed_lines(self, tmp_path, monkeypatch):
        history = tmp_path / "history.jsonl"
        history.write_text('{"severity": "P1"}\nnot json\n{"severity": "P2"}\n')

        import monitor.dashboard as dash
        monkeypatch.setattr(dash, "HISTORY_LOG", history)
        loaded = load_from_jsonl()
        assert len(loaded) == 2

    def test_render_html_with_no_records(self):
        output = render_html([])
        assert "No incidents yet" in output
        assert "<html" in output

    def test_render_html_with_records(self):
        records = [
            {"timestamp": "2026-01-01T00:00:00Z", "severity": "P0", "category": "cpu",
             "root_cause": "runaway process", "auto_remediated": True, "report_file": "a.md"},
            {"timestamp": "2026-01-02T00:00:00Z", "severity": "P2", "category": "disk",
             "root_cause": "log explosion", "auto_remediated": False, "report_file": "b.md"},
        ]
        output = render_html(records)
        assert "runaway process" in output
        assert "a.md" in output
        assert "<svg" in output  # bar chart rendered

    def test_bar_svg_handles_empty_counts(self):
        from collections import Counter
        svg = bar_svg(Counter())
        assert "<svg" in svg
        assert "P0" in svg and "P1" in svg and "P2" in svg and "P3" in svg
