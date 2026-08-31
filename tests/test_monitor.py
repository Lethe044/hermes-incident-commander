"""
Hermes Incident Commander - Monitor Package Test Suite
=========================================================
Covers monitor/notifier.py, monitor/watchdog.py, and monitor/dashboard.py.
No real network calls or real system mutation happen in these tests -
HTTP calls are mocked and filesystem operations are confined to tmp_path.

Run with:
    pytest tests/test_monitor.py -v
"""

from __future__ import annotations

import json
import sys
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from monitor.dashboard import bar_svg, load_from_jsonl, render_html
from monitor.notifier import Notifier
from monitor.watchdog import (
    Metrics,
    WatchdogConfig,
    _clean_old_files,
    run_once,
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
        # A raw Exception isn't caught (only URLError is) - but URLError must be
        import urllib.error
        with patch("monitor.notifier.urllib.request.urlopen", side_effect=urllib.error.URLError("no route")):
            notifier = Notifier(discord_webhook_url="https://discord.com/api/webhooks/1/2")
            results = notifier.send("hi")
            assert len(results) == 1
            assert results[0].ok is False


# ---------------------------------------------------------------------------
# Notifier - PagerDuty
# ---------------------------------------------------------------------------

class TestPagerDuty:

    def test_not_configured_by_default(self, monkeypatch):
        monkeypatch.delenv("PAGERDUTY_ROUTING_KEY", raising=False)
        notifier = Notifier()
        assert notifier.configured is False
        assert notifier.pagerduty_routing_key is None

    def test_configured_via_env(self, monkeypatch):
        monkeypatch.setenv("PAGERDUTY_ROUTING_KEY", "R0UTING-KEY")
        notifier = Notifier()
        assert notifier.configured is True

    def test_send_pagerduty_event_noop_when_unconfigured(self):
        notifier = Notifier(pagerduty_routing_key=None)
        assert notifier.send_pagerduty_event("summary") is None

    def test_resolve_pagerduty_event_noop_when_unconfigured(self):
        notifier = Notifier(pagerduty_routing_key=None)
        assert notifier.resolve_pagerduty_event("dedup-1") is None

    @patch("monitor.notifier.urllib.request.urlopen")
    def test_send_pagerduty_event_posts_trigger(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 202
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        notifier = Notifier(pagerduty_routing_key="R0UTING-KEY")
        result = notifier.send_pagerduty_event(
            summary="Disk 95% full", severity="critical", dedup_key="disk-1"
        )

        assert result is not None
        assert result.ok is True
        mock_urlopen.assert_called_once()

        sent_request = mock_urlopen.call_args[0][0]
        body = json.loads(sent_request.data.decode("utf-8"))
        assert body["routing_key"] == "R0UTING-KEY"
        assert body["event_action"] == "trigger"
        assert body["dedup_key"] == "disk-1"
        assert body["payload"]["severity"] == "critical"
        assert body["payload"]["summary"] == "Disk 95% full"

    @patch("monitor.notifier.urllib.request.urlopen")
    def test_send_pagerduty_event_falls_back_to_error_severity(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 202
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        notifier = Notifier(pagerduty_routing_key="R0UTING-KEY")
        notifier.send_pagerduty_event(summary="x", severity="not-a-real-severity")

        sent_request = mock_urlopen.call_args[0][0]
        body = json.loads(sent_request.data.decode("utf-8"))
        assert body["payload"]["severity"] == "error"

    @patch("monitor.notifier.urllib.request.urlopen")
    def test_resolve_pagerduty_event_posts_resolve(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 202
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        notifier = Notifier(pagerduty_routing_key="R0UTING-KEY")
        result = notifier.resolve_pagerduty_event("disk-1")

        assert result is not None
        assert result.ok is True
        sent_request = mock_urlopen.call_args[0][0]
        body = json.loads(sent_request.data.decode("utf-8"))
        assert body["event_action"] == "resolve"
        assert body["dedup_key"] == "disk-1"

    @patch("monitor.notifier.urllib.request.urlopen")
    def test_send_alert_triggers_pagerduty_when_configured(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 202
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        notifier = Notifier(pagerduty_routing_key="R0UTING-KEY")
        results = notifier.send_alert(severity="P0", title_text="disk", detail="Disk full")

        assert len(results) == 1  # only PagerDuty configured, no discord/slack
        assert results[0].ok is True

    def test_send_alert_skips_pagerduty_when_unconfigured(self):
        notifier = Notifier(discord_webhook_url=None, slack_webhook_url=None, pagerduty_routing_key=None)
        results = notifier.send_alert(severity="P1", title_text="cpu", detail="CPU high")
        assert results == []


# ---------------------------------------------------------------------------
# Watchdog - pure logic (no real psutil polling required)
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

    def test_dry_run_reports_without_auto_remediate_enabled(self):
        cfg = WatchdogConfig(auto_remediate=False)
        diagnosis = {"recommended_actions": ["restart nginx"]}
        performed = safe_remediate(diagnosis, cfg, dry_run=True)
        assert performed == []  # nginx isn't on the (empty) allowlist

    def test_dry_run_does_not_restart_service(self):
        cfg = WatchdogConfig(
            auto_remediate=False,
            remediation_allowlist={"restart_services": ["nginx"], "clean_log_dirs": [], "max_log_age_days": 14},
        )
        diagnosis = {"recommended_actions": ["restart nginx"]}
        with patch("monitor.watchdog.subprocess.run") as mock_run:
            performed = safe_remediate(diagnosis, cfg, dry_run=True)
        mock_run.assert_not_called()
        assert len(performed) == 1
        assert performed[0].startswith("[DRY RUN] Would restart")

    def test_dry_run_does_not_delete_files(self, tmp_path):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        old_file = log_dir / "app.log.1"
        old_file.write_text("x" * 100)
        import os
        import time
        old_time = time.time() - 30 * 86400
        os.utime(old_file, (old_time, old_time))

        cfg = WatchdogConfig(
            auto_remediate=False,
            remediation_allowlist={
                "restart_services": [],
                "clean_log_dirs": [str(log_dir)],
                "max_log_age_days": 14,
            },
        )
        diagnosis = {"recommended_actions": [f"clean old logs in {log_dir}"]}
        performed = safe_remediate(diagnosis, cfg, dry_run=True)
        assert len(performed) == 1
        assert performed[0].startswith("[DRY RUN] Would remove")
        assert old_file.exists()  # nothing was actually deleted

    def test_clean_old_files_dry_run_counts_without_deleting(self, tmp_path):
        old_file = tmp_path / "app.log.1"
        old_file.write_text("x")
        import os
        import time
        old_time = time.time() - 30 * 86400
        os.utime(old_file, (old_time, old_time))

        count = _clean_old_files(str(tmp_path), max_age_days=14, dry_run=True)
        assert count == 1
        assert old_file.exists()


# ---------------------------------------------------------------------------
# Watchdog - run_once, dry-run, and PagerDuty open/resolve wiring
# ---------------------------------------------------------------------------

class TestRunOnceAndPagerDutyResolve:

    FAKE_DIAGNOSIS = {
        "severity": "P1",
        "category": "cpu",
        "root_cause_hypothesis": "runaway process",
        "recommended_actions": ["restart nginx"],
        "report_markdown": "# Fake report",
    }

    def _patch_incident_paths(self, monkeypatch, tmp_path):
        import monitor.watchdog as wd
        monkeypatch.setattr(wd, "INCIDENT_DIR", tmp_path)
        monkeypatch.setattr(wd, "HISTORY_LOG", tmp_path / "history.jsonl")
        monkeypatch.setattr(wd, "OPEN_INCIDENTS_FILE", tmp_path / "open_pagerduty_incidents.json")
        return wd

    def test_dry_run_writes_report_but_sends_no_notification(self, tmp_path, monkeypatch):
        wd = self._patch_incident_paths(monkeypatch, tmp_path)
        breached = Metrics(timestamp="t1", cpu_percent=99.0, mem_percent=10, disk_percent=10, failed_services=[])
        notifier = Notifier(pagerduty_routing_key="R0UTING-KEY")

        with patch.object(wd, "collect_metrics", return_value=breached), \
             patch.object(wd, "triage_with_claude", return_value=self.FAKE_DIAGNOSIS), \
             patch.object(notifier, "send_alert") as mock_send_alert:
            report = wd.run_once(WatchdogConfig(), notifier, dry_run=True, quiet=True)

        assert report is not None and report.exists()
        mock_send_alert.assert_not_called()
        assert not wd.OPEN_INCIDENTS_FILE.exists()

    def test_real_breach_sends_alert_with_dedup_key_and_persists_open_incident(self, tmp_path, monkeypatch):
        wd = self._patch_incident_paths(monkeypatch, tmp_path)
        breached = Metrics(timestamp="t1", cpu_percent=99.0, mem_percent=10, disk_percent=10, failed_services=[])
        notifier = Notifier(pagerduty_routing_key="R0UTING-KEY")

        with patch.object(wd, "collect_metrics", return_value=breached), \
             patch.object(wd, "triage_with_claude", return_value=self.FAKE_DIAGNOSIS), \
             patch.object(notifier, "send_alert") as mock_send_alert:
            report = wd.run_once(WatchdogConfig(), notifier, quiet=True)

        assert report is not None
        mock_send_alert.assert_called_once()
        assert mock_send_alert.call_args.kwargs["dedup_key"] == "hermes-ic-cpu"
        assert json.loads(wd.OPEN_INCIDENTS_FILE.read_text()) == {"cpu": "hermes-ic-cpu"}

    def test_recovery_resolves_pagerduty_and_clears_open_incident(self, tmp_path, monkeypatch):
        wd = self._patch_incident_paths(monkeypatch, tmp_path)
        wd.OPEN_INCIDENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        wd.OPEN_INCIDENTS_FILE.write_text(json.dumps({"cpu": "hermes-ic-cpu"}))

        normal = Metrics(timestamp="t2", cpu_percent=10.0, mem_percent=10, disk_percent=10, failed_services=[])
        notifier = Notifier(pagerduty_routing_key="R0UTING-KEY")

        with patch.object(wd, "collect_metrics", return_value=normal), \
             patch.object(notifier, "resolve_pagerduty_event") as mock_resolve:
            report = wd.run_once(WatchdogConfig(), notifier, quiet=True)

        assert report is None
        mock_resolve.assert_called_once_with("hermes-ic-cpu")
        assert json.loads(wd.OPEN_INCIDENTS_FILE.read_text()) == {}

    def test_resolve_not_called_when_pagerduty_unconfigured(self, tmp_path, monkeypatch):
        wd = self._patch_incident_paths(monkeypatch, tmp_path)
        wd.OPEN_INCIDENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        wd.OPEN_INCIDENTS_FILE.write_text(json.dumps({"cpu": "hermes-ic-cpu"}))

        normal = Metrics(timestamp="t2", cpu_percent=10.0, mem_percent=10, disk_percent=10, failed_services=[])
        notifier = Notifier()  # no channels configured

        with patch.object(wd, "collect_metrics", return_value=normal):
            wd.run_once(WatchdogConfig(), notifier, quiet=True)

        # still cleared locally even without a PagerDuty key to notify
        assert json.loads(wd.OPEN_INCIDENTS_FILE.read_text()) == {}


# ---------------------------------------------------------------------------
# Prometheus exporter
# ---------------------------------------------------------------------------

class TestPrometheusExporter:

    def test_render_before_any_metrics_collected(self):
        from monitor import prometheus_exporter as pe
        pe._latest.clear()
        assert "has not completed" in pe.render_prometheus_text()

    def test_render_reflects_latest_metrics_and_breaches(self):
        from monitor import prometheus_exporter as pe
        metrics = Metrics(timestamp="t", cpu_percent=87.5, mem_percent=40.0, disk_percent=20.0, failed_services=["nginx"])
        pe.update_latest_metrics(metrics, {"cpu": True, "mem": False, "disk": False, "service": True})
        text = pe.render_prometheus_text()
        assert "hermes_watchdog_cpu_percent 87.5" in text
        assert "hermes_watchdog_failed_services_count 1" in text
        assert 'hermes_watchdog_breach{metric="cpu"} 1' in text
        assert 'hermes_watchdog_breach{metric="mem"} 0' in text

    def test_server_serves_metrics_over_real_http(self):
        import urllib.request
        from monitor import prometheus_exporter as pe

        metrics = Metrics(timestamp="t", cpu_percent=50.0, mem_percent=10.0, disk_percent=10.0, failed_services=[])
        pe.update_latest_metrics(metrics, {"cpu": False, "mem": False, "disk": False, "service": False})

        server = pe.start_metrics_server(0)  # port 0 = OS picks a free ephemeral port
        try:
            port = server.server_address[1]
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=5) as resp:
                assert resp.status == 200
                assert "hermes_watchdog_cpu_percent" in resp.read().decode()
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/not-a-real-path", timeout=5)
                assert False, "expected a 404"
            except urllib.error.HTTPError as e:
                assert e.code == 404
        finally:
            server.shutdown()


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
