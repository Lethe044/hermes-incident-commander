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

from monitor.dashboard import bar_svg, load_from_jsonl, render_html, trend_svg
from monitor.notifier import Notifier
from monitor.watchdog import (
    Metrics,
    WatchdogConfig,
    _clean_old_files,
    run_once,
    safe_remediate,
    validate_config,
    write_incident,
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
            notifier = Notifier(discord_webhook_url="https://discord.com/api/webhooks/1/2", max_retries=0)
            results = notifier.send("hi")
            assert len(results) == 1
            assert results[0].ok is False


# ---------------------------------------------------------------------------
# Notifier - retry / backoff
# ---------------------------------------------------------------------------

class TestNotifierRetry:

    @patch("monitor.notifier.time.sleep")
    @patch("monitor.notifier.urllib.request.urlopen")
    def test_retries_on_transient_url_error_then_succeeds(self, mock_urlopen, mock_sleep):
        import urllib.error
        mock_resp = MagicMock()
        mock_resp.status = 204
        # first call fails (transient), second call succeeds
        mock_urlopen.side_effect = [urllib.error.URLError("timed out"), MagicMock(__enter__=MagicMock(return_value=mock_resp), __exit__=MagicMock(return_value=False))]

        notifier = Notifier(discord_webhook_url="https://discord.com/api/webhooks/1/2", max_retries=2, backoff_seconds=0.5)
        results = notifier.send("hi")

        assert len(results) == 1
        assert results[0].ok is True
        assert "after 1 retry" in results[0].detail
        assert mock_urlopen.call_count == 2
        mock_sleep.assert_called_once_with(0.5)  # backoff_seconds * 2**0

    @patch("monitor.notifier.time.sleep")
    @patch("monitor.notifier.urllib.request.urlopen")
    def test_exhausts_retries_and_reports_failure(self, mock_urlopen, mock_sleep):
        import urllib.error
        mock_urlopen.side_effect = urllib.error.URLError("no route to host")

        notifier = Notifier(discord_webhook_url="https://discord.com/api/webhooks/1/2", max_retries=2, backoff_seconds=0.1)
        results = notifier.send("hi")

        assert len(results) == 1
        assert results[0].ok is False
        assert "gave up after 3 attempts" in results[0].detail
        assert mock_urlopen.call_count == 3  # 1 initial + 2 retries
        # exponential backoff: 0.1 * 2**0, then 0.1 * 2**1
        assert [c.args[0] for c in mock_sleep.call_args_list] == [0.1, 0.2]

    @patch("monitor.notifier.time.sleep")
    @patch("monitor.notifier.urllib.request.urlopen")
    def test_does_not_retry_client_errors(self, mock_urlopen, mock_sleep):
        import urllib.error
        mock_urlopen.side_effect = urllib.error.HTTPError(
            "https://discord.com/api/webhooks/1/2", 401, "Unauthorized", {}, None
        )

        notifier = Notifier(discord_webhook_url="https://discord.com/api/webhooks/1/2", max_retries=2)
        results = notifier.send("hi")

        assert len(results) == 1
        assert results[0].ok is False
        assert "not retrying a client error" in results[0].detail
        assert mock_urlopen.call_count == 1  # no retries for a 401
        mock_sleep.assert_not_called()

    @patch("monitor.notifier.time.sleep")
    @patch("monitor.notifier.urllib.request.urlopen")
    def test_retries_on_429_and_5xx(self, mock_urlopen, mock_sleep):
        import urllib.error
        mock_resp = MagicMock()
        mock_resp.status = 200
        success = MagicMock(__enter__=MagicMock(return_value=mock_resp), __exit__=MagicMock(return_value=False))
        mock_urlopen.side_effect = [
            urllib.error.HTTPError("https://x", 503, "Service Unavailable", {}, None),
            urllib.error.HTTPError("https://x", 429, "Too Many Requests", {}, None),
            success,
        ]

        notifier = Notifier(slack_webhook_url="https://hooks.slack.com/services/x", max_retries=2, backoff_seconds=0.01)
        results = notifier.send("hi")

        assert len(results) == 1
        assert results[0].ok is True
        assert mock_urlopen.call_count == 3

    @patch("monitor.notifier.time.sleep")
    @patch("monitor.notifier.urllib.request.urlopen")
    def test_zero_retries_means_single_attempt(self, mock_urlopen, mock_sleep):
        import urllib.error
        mock_urlopen.side_effect = urllib.error.URLError("down")

        notifier = Notifier(discord_webhook_url="https://discord.com/api/webhooks/1/2", max_retries=0)
        results = notifier.send("hi")

        assert mock_urlopen.call_count == 1
        assert "gave up after 1 attempt" in results[0].detail
        mock_sleep.assert_not_called()


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


class TestValidateConfig:

    def _write(self, tmp_path, text):
        p = tmp_path / "cfg.yaml"
        p.write_text(text)
        return str(p)

    def test_valid_minimal_config_has_no_errors(self, tmp_path):
        path = self._write(tmp_path, "cpu_threshold: 80\nwatched_services: [nginx]\n")
        errors, warnings = validate_config(path)
        assert errors == []

    def test_missing_file_is_an_error(self, tmp_path):
        errors, warnings = validate_config(str(tmp_path / "does_not_exist.yaml"))
        assert len(errors) == 1
        assert "not found" in errors[0]

    def test_invalid_yaml_syntax_is_an_error(self, tmp_path):
        path = self._write(tmp_path, "cpu_threshold: [unclosed\n")
        errors, warnings = validate_config(path)
        assert len(errors) == 1
        assert "YAML" in errors[0]

    def test_non_mapping_yaml_is_an_error(self, tmp_path):
        path = self._write(tmp_path, "- just\n- a\n- list\n")
        errors, warnings = validate_config(path)
        assert len(errors) == 1
        assert "mapping" in errors[0]

    def test_unknown_key_is_a_warning_not_an_error(self, tmp_path):
        path = self._write(tmp_path, "cpu_thresholdd: 80\n")  # typo'd key
        errors, warnings = validate_config(path)
        assert errors == []
        assert any("cpu_thresholdd" in w and "typo" in w for w in warnings)

    def test_threshold_out_of_range_is_an_error(self, tmp_path):
        path = self._write(tmp_path, "cpu_threshold: 150\n")
        errors, warnings = validate_config(path)
        assert any("cpu_threshold" in e for e in errors)

    def test_threshold_wrong_type_is_an_error(self, tmp_path):
        path = self._write(tmp_path, "cpu_threshold: not-a-number\n")
        errors, warnings = validate_config(path)
        assert any("cpu_threshold" in e for e in errors)

    def test_negative_poll_interval_is_an_error(self, tmp_path):
        path = self._write(tmp_path, "poll_interval_seconds: -5\n")
        errors, warnings = validate_config(path)
        assert any("poll_interval_seconds" in e for e in errors)

    def test_zero_consecutive_breaches_is_an_error(self, tmp_path):
        path = self._write(tmp_path, "consecutive_breaches_required: 0\n")
        errors, warnings = validate_config(path)
        assert any("consecutive_breaches_required" in e for e in errors)

    def test_nonexistent_disk_path_is_a_warning(self, tmp_path):
        path = self._write(tmp_path, "disk_path: /this/path/should/not/exist/anywhere\n")
        errors, warnings = validate_config(path)
        assert errors == []
        assert any("disk_path" in w for w in warnings)

    def test_restart_service_not_in_watched_services_is_a_warning(self, tmp_path):
        path = self._write(
            tmp_path,
            "watched_services: [nginx]\n"
            "remediation_allowlist:\n  restart_services: [postgresql]\n",
        )
        errors, warnings = validate_config(path)
        assert errors == []
        assert any("postgresql" in w for w in warnings)

    def test_restart_service_in_watched_services_has_no_warning(self, tmp_path):
        path = self._write(
            tmp_path,
            "watched_services: [nginx]\n"
            "remediation_allowlist:\n  restart_services: [nginx]\n",
        )
        errors, warnings = validate_config(path)
        assert not any("nginx" in w for w in warnings)

    def test_clean_log_dirs_must_be_list_of_strings(self, tmp_path):
        path = self._write(
            tmp_path, "remediation_allowlist:\n  clean_log_dirs: [123]\n"
        )
        errors, warnings = validate_config(path)
        assert any("clean_log_dirs" in e for e in errors)

    def test_negative_max_log_age_days_is_an_error(self, tmp_path):
        path = self._write(
            tmp_path, "remediation_allowlist:\n  max_log_age_days: -1\n"
        )
        errors, warnings = validate_config(path)
        assert any("max_log_age_days" in e for e in errors)

    def test_auto_remediate_with_empty_allowlist_is_a_warning(self, tmp_path):
        path = self._write(tmp_path, "auto_remediate: true\n")
        errors, warnings = validate_config(path)
        assert any("never do anything" in w for w in warnings)

    def test_auto_remediate_with_populated_allowlist_has_no_such_warning(self, tmp_path):
        path = self._write(
            tmp_path,
            "auto_remediate: true\nwatched_services: [nginx]\n"
            "remediation_allowlist:\n  restart_services: [nginx]\n",
        )
        errors, warnings = validate_config(path)
        assert not any("never do anything" in w for w in warnings)

    def test_watched_services_wrong_type_is_an_error(self, tmp_path):
        path = self._write(tmp_path, "watched_services: nginx\n")  # should be a list
        errors, warnings = validate_config(path)
        assert any("watched_services" in e for e in errors)

    def test_cli_validate_config_exit_code_and_output(self, tmp_path, monkeypatch, capsys):
        import monitor.watchdog as wd
        path = self._write(tmp_path, "cpu_threshold: 150\nunexpected_key: 1\n")
        monkeypatch.setattr(sys, "argv", ["watchdog.py", "--config", path, "--validate-config"])
        with pytest_raises_systemexit() as exc:
            wd.main()
        assert exc.code == 1
        out = capsys.readouterr().out
        assert "ERROR" in out
        assert "WARNING" in out

    def test_cli_validate_config_without_config_flag_exits_nonzero(self, monkeypatch):
        import monitor.watchdog as wd
        monkeypatch.setattr(sys, "argv", ["watchdog.py", "--validate-config"])
        with pytest_raises_systemexit() as exc:
            wd.main()
        assert exc.code == 1

    def test_cli_validate_config_success_prints_resolved_config(self, tmp_path, monkeypatch, capsys):
        import monitor.watchdog as wd
        path = self._write(tmp_path, "cpu_threshold: 70\n")
        monkeypatch.setattr(sys, "argv", ["watchdog.py", "--config", path, "--validate-config"])
        with pytest_raises_systemexit() as exc:
            wd.main()
        assert exc.code == 0
        out = capsys.readouterr().out
        assert "Resolved config" in out
        assert "cpu_threshold: 70" in out


def pytest_raises_systemexit():
    """Small local helper standing in for `pytest.raises(SystemExit)` so
    this file works the same under real pytest and under the sandbox's
    manual test harness (see manual_harness.py), which doesn't implement
    pytest.raises."""
    class _Ctx:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            if exc_type is None:
                raise AssertionError("SystemExit was not raised")
            if not issubclass(exc_type, SystemExit):
                return False
            self.code = exc.code
            return True

    return _Ctx()


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


# ---------------------------------------------------------------------------
# Incident search (SQLite + FTS5, with LIKE fallback)
# ---------------------------------------------------------------------------

class TestIncidentDB:

    def _make_history(self, tmp_path):
        history = tmp_path / "history.jsonl"
        history.write_text(
            '{"timestamp": "t1", "severity": "P0", "category": "service", '
            '"root_cause": "nginx worker processes exited after a bad config reload", '
            '"auto_remediated": true, "report_file": "r1.md"}\n'
            '{"timestamp": "t2", "severity": "P1", "category": "disk", '
            '"root_cause": "Log rotation was disabled, disk filled up", '
            '"auto_remediated": true, "report_file": "r2.md"}\n'
        )
        return history

    def _patch_paths(self, monkeypatch, tmp_path):
        import monitor.incident_db as idb
        monkeypatch.setattr(idb, "INCIDENT_DIR", tmp_path)
        monkeypatch.setattr(idb, "HISTORY_LOG", self._make_history(tmp_path))
        monkeypatch.setattr(idb, "DB_PATH", tmp_path / "incidents.db")
        return idb

    def test_sync_reads_history_and_search_finds_match(self, tmp_path, monkeypatch):
        idb = self._patch_paths(monkeypatch, tmp_path)
        conn = idb.get_connection()
        n = idb.sync(conn)
        assert n == 2

        results = idb.search("nginx", conn=conn)
        assert len(results) == 1
        assert results[0]["report_file"] == "r1.md"

        results = idb.search("rotation", conn=conn)
        assert len(results) == 1
        assert results[0]["report_file"] == "r2.md"

    def test_search_no_match_returns_empty_list(self, tmp_path, monkeypatch):
        idb = self._patch_paths(monkeypatch, tmp_path)
        conn = idb.get_connection()
        idb.sync(conn)
        assert idb.search("totally-unrelated-xyz", conn=conn) == []

    def test_sync_is_idempotent(self, tmp_path, monkeypatch):
        idb = self._patch_paths(monkeypatch, tmp_path)
        conn = idb.get_connection()
        idb.sync(conn)
        idb.sync(conn)  # re-run against the same, unchanged history
        count = conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]
        assert count == 2  # not 4 - upserted by report_file, not duplicated

    def test_sync_missing_history_file_returns_zero(self, tmp_path, monkeypatch):
        import monitor.incident_db as idb
        monkeypatch.setattr(idb, "INCIDENT_DIR", tmp_path)
        monkeypatch.setattr(idb, "HISTORY_LOG", tmp_path / "does_not_exist.jsonl")
        monkeypatch.setattr(idb, "DB_PATH", tmp_path / "incidents.db")
        assert idb.sync() == 0

    def test_fts_query_escapes_punctuation_safely(self):
        import monitor.incident_db as idb
        # Hyphens/colons in real incident text (e.g. "CannotStartContainerError:")
        # must not raise an FTS5 syntax error.
        query = idb._fts_query("nginx-worker: crashed")
        assert query.count('"') >= 4  # each term individually phrase-quoted

    def test_like_fallback_when_fts5_unavailable(self, tmp_path, monkeypatch):
        import sqlite3
        import monitor.incident_db as idb

        conn = sqlite3.connect(":memory:")
        conn.executescript(idb._BASE_SCHEMA)  # no FTS schema on purpose
        conn.execute(
            "INSERT INTO incidents (timestamp, severity, category, root_cause, "
            "report_file, auto_remediated, report_body) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("t1", "P1", "disk", "Log rotation was disabled", "r1.md", 0, ""),
        )
        conn.commit()

        assert idb._fts_available(conn) is False
        results = idb.search("rotation", conn=conn)
        assert len(results) == 1
        assert results[0]["report_file"] == "r1.md"


class TestIncidentDBExport:

    def _sample_results(self):
        import monitor.incident_db as idb
        return [
            {
                "timestamp": "t1", "severity": "P0", "category": "service",
                "root_cause": "nginx worker processes exited", "report_file": "r1.md",
                "auto_remediated": 1,
            },
            {
                "timestamp": "t2", "severity": "P1", "category": "disk",
                "root_cause": "disk filled up", "report_file": "r2.md",
                "auto_remediated": 0,
            },
        ], idb

    def test_format_text_matches_original_output(self):
        results, idb = self._sample_results()
        out = idb.format_results(results, fmt="text")
        assert "[P0] t1 (service) - nginx worker processes exited" in out
        assert "    report: r1.md" in out
        assert "[P1] t2 (disk) - disk filled up" in out

    def test_format_text_empty_results(self):
        _, idb = self._sample_results()
        assert idb.format_results([], fmt="text") == "No matching incidents found."

    def test_format_json_round_trips(self):
        import json
        results, idb = self._sample_results()
        out = idb.format_results(results, fmt="json")
        parsed = json.loads(out)
        assert parsed == results

    def test_format_json_empty_results_is_empty_array(self):
        import json
        _, idb = self._sample_results()
        assert json.loads(idb.format_results([], fmt="json")) == []

    def test_format_csv_has_header_and_rows(self):
        import csv
        import io
        results, idb = self._sample_results()
        out = idb.format_results(results, fmt="csv")
        rows = list(csv.DictReader(io.StringIO(out)))
        assert len(rows) == 2
        assert rows[0]["report_file"] == "r1.md"
        assert rows[0]["severity"] == "P0"
        assert rows[1]["root_cause"] == "disk filled up"

    def test_format_csv_empty_results_has_only_header(self):
        import csv
        import io
        _, idb = self._sample_results()
        out = idb.format_results([], fmt="csv")
        rows = list(csv.DictReader(io.StringIO(out)))
        assert rows == []
        assert "severity" in out

    def test_cli_search_json_end_to_end(self, tmp_path, monkeypatch, capsys):
        import json
        import monitor.incident_db as idb
        history = tmp_path / "history.jsonl"
        history.write_text(
            '{"timestamp": "t1", "severity": "P0", "category": "service", '
            '"root_cause": "nginx worker processes exited", '
            '"auto_remediated": true, "report_file": "r1.md"}\n'
        )
        monkeypatch.setattr(idb, "INCIDENT_DIR", tmp_path)
        monkeypatch.setattr(idb, "HISTORY_LOG", history)
        monkeypatch.setattr(idb, "DB_PATH", tmp_path / "incidents.db")
        monkeypatch.setattr(sys, "argv", ["incident_db.py", "--sync", "--search", "nginx", "--format", "json"])
        idb.main()
        out = capsys.readouterr().out
        # Last JSON-looking line onward should parse; find the array start.
        start = out.index("[")
        parsed = json.loads(out[start:])
        assert len(parsed) == 1
        assert parsed[0]["report_file"] == "r1.md"


# ---------------------------------------------------------------------------
# Time-of-day-aware baseline (adaptive thresholds)
# ---------------------------------------------------------------------------

class TestBaseline:

    def test_insufficient_samples_falls_back_to_static(self, tmp_path):
        from monitor import baseline
        path = tmp_path / "baseline.json"
        t = baseline.get_adaptive_threshold("cpu", 2, static_threshold=90.0, path=path)
        assert t == 90.0

    def test_hour_with_no_data_uses_static(self, tmp_path):
        from monitor import baseline
        path = tmp_path / "baseline.json"
        for _ in range(50):
            baseline.update_baseline({"cpu": 70.0}, hour=2, path=path)
        # different hour, never updated
        t = baseline.get_adaptive_threshold("cpu", 14, static_threshold=90.0, path=path)
        assert t == 90.0

    def test_adaptive_threshold_never_drops_below_static(self, tmp_path):
        from monitor import baseline
        path = tmp_path / "baseline.json"
        for _ in range(50):
            baseline.update_baseline({"cpu": 5.0}, hour=3, path=path)  # a very quiet hour
        t = baseline.get_adaptive_threshold("cpu", 3, static_threshold=90.0, path=path)
        assert t >= 90.0

    def test_adaptive_threshold_can_rise_above_static_when_justified(self, tmp_path):
        import random
        from monitor import baseline
        path = tmp_path / "baseline.json"
        random.seed(1)
        for _ in range(50):
            baseline.update_baseline({"cpu": 88.0 + random.uniform(-2, 2)}, hour=2, path=path)
        t = baseline.get_adaptive_threshold("cpu", 2, static_threshold=90.0, path=path)
        assert t > 90.0

    def test_adaptive_threshold_is_capped(self, tmp_path):
        import random
        from monitor import baseline
        path = tmp_path / "baseline.json"
        random.seed(2)
        for _ in range(50):
            baseline.update_baseline({"cpu": random.uniform(0, 100)}, hour=5, path=path)
        t = baseline.get_adaptive_threshold("cpu", 5, static_threshold=90.0, cap_multiplier=1.5, path=path)
        assert t <= 90.0 * 1.5

    def test_hour_summary_marks_trust_correctly(self, tmp_path):
        from monitor import baseline
        path = tmp_path / "baseline.json"
        for _ in range(5):  # below MIN_SAMPLES_PER_HOUR
            baseline.update_baseline({"cpu": 50.0}, hour=9, path=path)
        summary = baseline.hour_summary(path=path)
        assert summary["cpu"]["9"]["trusted"] is False
        assert summary["cpu"]["9"]["n"] == 5

    def test_breaches_uses_static_thresholds_when_adaptive_disabled(self, tmp_path, monkeypatch):
        import monitor.baseline as baseline_mod
        monkeypatch.setattr(baseline_mod, "BASELINE_FILE", tmp_path / "baseline.json")

        cfg = WatchdogConfig(cpu_threshold=90.0, adaptive_thresholds=False)
        metrics = Metrics(timestamp="2026-01-01T02:00:00+00:00", cpu_percent=90.5,
                           mem_percent=10, disk_percent=10, failed_services=[])
        assert metrics.breaches(cfg)["cpu"] is True

    def test_breaches_uses_adaptive_threshold_when_enabled(self, tmp_path, monkeypatch):
        import random
        import monitor.baseline as baseline_mod
        monkeypatch.setattr(baseline_mod, "BASELINE_FILE", tmp_path / "baseline.json")

        random.seed(3)
        for _ in range(50):
            baseline_mod.update_baseline({"cpu": 88.0 + random.uniform(-2, 2), "mem": 10, "disk": 10}, hour=2)

        cfg = WatchdogConfig(cpu_threshold=90.0, adaptive_thresholds=True)
        metrics = Metrics(timestamp="2026-01-01T02:00:00+00:00", cpu_percent=90.5,
                           mem_percent=10, disk_percent=10, failed_services=[])
        # learned baseline for this hour justifies a threshold above 90.5
        assert metrics.breaches(cfg)["cpu"] is False


# ---------------------------------------------------------------------------
# Flapping detection
# ---------------------------------------------------------------------------

class TestFlapping:

    def test_first_occurrence_is_not_flapping(self, tmp_path):
        from monitor import flapping
        r = flapping.record_and_check("cpu", "2026-01-01T10:00:00+00:00", path=tmp_path / "f.json")
        assert r["count"] == 1
        assert r["is_flapping"] is False

    def test_third_occurrence_within_window_is_flapping(self, tmp_path):
        from monitor import flapping
        path = tmp_path / "f.json"
        flapping.record_and_check("cpu", "2026-01-01T10:00:00+00:00", path=path)
        flapping.record_and_check("cpu", "2026-01-01T10:15:00+00:00", path=path)
        r = flapping.record_and_check("cpu", "2026-01-01T10:30:00+00:00", path=path)
        assert r["count"] == 3
        assert r["is_flapping"] is True

    def test_different_categories_tracked_independently(self, tmp_path):
        from monitor import flapping
        path = tmp_path / "f.json"
        flapping.record_and_check("cpu", "2026-01-01T10:00:00+00:00", path=path)
        flapping.record_and_check("cpu", "2026-01-01T10:05:00+00:00", path=path)
        r_disk = flapping.record_and_check("disk", "2026-01-01T10:10:00+00:00", path=path)
        assert r_disk["count"] == 1
        assert r_disk["is_flapping"] is False

    def test_old_occurrences_outside_window_are_pruned(self, tmp_path):
        from monitor import flapping
        path = tmp_path / "f.json"
        flapping.record_and_check("cpu", "2026-01-01T10:00:00+00:00", path=path)
        flapping.record_and_check("cpu", "2026-01-01T10:15:00+00:00", path=path)
        # 2 hours later - the first two entries are outside the 60-minute window
        r = flapping.record_and_check("cpu", "2026-01-01T12:30:00+00:00", path=path)
        assert r["count"] == 1
        assert r["is_flapping"] is False

    def test_custom_threshold_and_window(self, tmp_path):
        from monitor import flapping
        r = flapping.record_and_check(
            "mem", "2026-01-01T10:00:00+00:00", flap_threshold=1, path=tmp_path / "f.json"
        )
        assert r["is_flapping"] is True

    def test_write_incident_flags_flapping_in_report_and_history(self, tmp_path, monkeypatch):
        import monitor.watchdog as wd
        import monitor.flapping as flapping_mod
        monkeypatch.setattr(wd, "INCIDENT_DIR", tmp_path)
        monkeypatch.setattr(wd, "HISTORY_LOG", tmp_path / "history.jsonl")
        monkeypatch.setattr(flapping_mod, "FREQUENCY_FILE", tmp_path / "incident_frequency.json")

        diagnosis = {
            "severity": "P2", "category": "cpu",
            "root_cause_hypothesis": "runaway cron",
            "recommended_actions": [], "report_markdown": "# Report",
        }
        timestamps = [
            "2026-01-01T10:00:00+00:00",
            "2026-01-01T10:15:00+00:00",
            "2026-01-01T10:30:00+00:00",
        ]
        report_path = None
        for ts in timestamps:
            metrics = Metrics(timestamp=ts, cpu_percent=95, mem_percent=10, disk_percent=10, failed_services=[])
            report_path = wd.write_incident(metrics, diagnosis, [])

        content = report_path.read_text()
        assert "FLAPPING DETECTED" in content
        assert "3rd `cpu` incident" in content

        records = [json.loads(line) for line in wd.HISTORY_LOG.read_text().splitlines()]
        assert records[0]["flapping"] is False
        assert records[1]["flapping"] is False
        assert records[2]["flapping"] is True

    def test_ordinal_suffixes(self):
        # Exercises the same suffix rules used in write_incident's report text.
        def ordinal(n):
            if 10 <= n % 100 <= 20:
                return f"{n}th"
            return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }"
        cases = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th", 11: "11th",
                 12: "12th", 13: "13th", 21: "21st", 22: "22nd", 23: "23rd"}
        for n, expected in cases.items():
            assert ordinal(n) == expected


# ---------------------------------------------------------------------------
# Dashboard search box (client-side JS, extracted and run under Node)
# ---------------------------------------------------------------------------

class TestDashboardSearch:

    def test_search_elements_present_when_there_are_records(self):
        records = [
            {"timestamp": "t1", "severity": "P0", "category": "service",
             "root_cause": "nginx crashed", "auto_remediated": True, "report_file": "r1.md"},
        ]
        out = render_html(records)
        assert 'id="incident-search"' in out
        assert 'id="no-results"' in out
        assert 'id="search-count"' in out
        assert "function rowMatches" in out

    def test_search_box_absent_when_no_records(self):
        out = render_html([])
        assert 'id="incident-search"' not in out
        assert "function rowMatches" not in out

    def test_flap_badge_rendered_for_flapping_incidents(self):
        records = [
            {"timestamp": "t1", "severity": "P2", "category": "cpu",
             "root_cause": "x", "auto_remediated": False, "report_file": "r1.md", "flapping": True},
            {"timestamp": "t2", "severity": "P2", "category": "cpu",
             "root_cause": "x", "auto_remediated": False, "report_file": "r2.md", "flapping": False},
        ]
        out = render_html(records)
        assert out.count('class="flap-badge"') == 1  # only the flapping one gets the badge

    def test_rowmatches_js_behaves_correctly_under_node(self):
        import re
        import shutil
        import subprocess

        if shutil.which("node") is None:
            import pytest
            pytest.skip("node not available in this environment")

        records = [
            {"timestamp": "t1", "severity": "P0", "category": "service",
             "root_cause": "nginx crashed", "auto_remediated": True, "report_file": "r1.md"},
        ]
        out = render_html(records)
        match = re.search(r"function rowMatches\(text, query\) \{.*?\n    \}", out, re.S)
        assert match, "rowMatches function not found in rendered HTML"

        script = match.group(0) + """
        if (rowMatches("nginx crashed", "") !== true) throw new Error("empty query should match");
        if (rowMatches("NGINX crashed", "nginx") !== true) throw new Error("should be case-insensitive");
        if (rowMatches("log rotation disabled", "nginx") !== false) throw new Error("non-match should be false");
        console.log("ok");
        """
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, result.stderr
        assert "ok" in result.stdout


class TestDashboardTrend:

    def test_trend_svg_shows_empty_message_when_no_dated_records(self):
        out = trend_svg([])
        assert "No incidents in the last" in out

    def test_trend_svg_renders_line_and_area_for_dated_records(self):
        from datetime import datetime
        today = datetime.now().date().isoformat()
        records = [
            {"timestamp": f"{today}T10:00:00Z", "severity": "P1"},
            {"timestamp": f"{today}T11:00:00Z", "severity": "P1"},
        ]
        out = trend_svg(records)
        assert "<path" in out
        assert "No incidents in the last" not in out
        assert "max 2/day" in out

    def test_trend_svg_groups_by_day_across_multiple_timestamps_same_day(self):
        from datetime import datetime
        today = datetime.now().date().isoformat()
        records = [{"timestamp": f"{today}T0{h}:00:00Z", "severity": "P2"} for h in range(3)]
        out = trend_svg(records)
        assert "max 3/day" in out

    def test_trend_svg_ignores_records_outside_the_window(self):
        records = [{"timestamp": "2000-01-01T00:00:00Z", "severity": "P3"}]
        out = trend_svg(records, days=14)
        assert "No incidents in the last 14 days" in out

    def test_trend_svg_handles_unparseable_timestamps_gracefully(self):
        records = [{"timestamp": "not-a-real-timestamp", "severity": "P2"}]
        out = trend_svg(records)  # must not raise
        assert "<svg" in out

    def test_incident_date_parses_iso_and_prefix_forms(self):
        from monitor.dashboard import _incident_date
        assert _incident_date("2026-05-01T12:00:00Z") == "2026-05-01"
        assert _incident_date("2026-05-01 something odd") == "2026-05-01"
        assert _incident_date("") is None
        assert _incident_date("garbage") is None

    def test_trend_panel_present_in_rendered_html(self):
        out = render_html([])
        assert "Incidents per Day" in out
