"""
Hermes Incident Commander - Prometheus Exporter
===================================================
A tiny, dependency-free HTTP server (stdlib http.server only) that exposes
the watchdog's most recent metrics snapshot in Prometheus text exposition
format on /metrics. Meant to run alongside:

    python -m monitor.watchdog --metrics-port 9877

so an existing Prometheus/Grafana stack can scrape the watchdog instead of
(or alongside) reading its JSONL history file.

This is read-only. It only ever serves numbers the watchdog has already
collected with psutil - it cannot be used to control the watchdog, trigger
remediation, or reach anything else on the host. It binds to 127.0.0.1 by
default; put it behind your own reverse proxy or firewall rule if you need
a remote Prometheus server to scrape it.
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

_latest: dict[str, Any] = {}
_lock = threading.Lock()


def update_latest_metrics(
    metrics: Any,
    breaches: dict[str, bool],
    is_flapping: bool = False,
    in_quiet_hours: bool = False,
) -> None:
    """Called by the watchdog after every poll to publish the latest
    snapshot. `metrics` is a monitor.watchdog.Metrics instance (typed as
    Any here to avoid a circular import). `is_flapping`/`in_quiet_hours`
    reflect the current incident's suppression state, if any - both
    default to False for a poll with no active incident."""
    with _lock:
        _latest["metrics"] = metrics
        _latest["breaches"] = dict(breaches)
        _latest["is_flapping"] = is_flapping
        _latest["in_quiet_hours"] = in_quiet_hours


def render_prometheus_text() -> str:
    """Builds the /metrics response body. Exposed as a standalone function
    so it can be unit-tested without spinning up a real HTTP server."""
    with _lock:
        metrics = _latest.get("metrics")
        breaches = dict(_latest.get("breaches", {}))
        is_flapping = _latest.get("is_flapping", False)
        in_quiet_hours = _latest.get("in_quiet_hours", False)

    if metrics is None:
        return "# Hermes Incident Commander watchdog has not completed a check yet\n"

    lines = [
        "# HELP hermes_watchdog_cpu_percent Current CPU usage percent",
        "# TYPE hermes_watchdog_cpu_percent gauge",
        f"hermes_watchdog_cpu_percent {metrics.cpu_percent}",
        "# HELP hermes_watchdog_mem_percent Current memory usage percent",
        "# TYPE hermes_watchdog_mem_percent gauge",
        f"hermes_watchdog_mem_percent {metrics.mem_percent}",
        "# HELP hermes_watchdog_disk_percent Current disk usage percent",
        "# TYPE hermes_watchdog_disk_percent gauge",
        f"hermes_watchdog_disk_percent {metrics.disk_percent}",
        "# HELP hermes_watchdog_failed_services_count Number of watched systemd services currently failed",
        "# TYPE hermes_watchdog_failed_services_count gauge",
        f"hermes_watchdog_failed_services_count {len(metrics.failed_services)}",
        "# HELP hermes_watchdog_breach Whether a given metric is currently over its threshold (1) or not (0)",
        "# TYPE hermes_watchdog_breach gauge",
    ]
    for key in sorted(breaches):
        lines.append(f'hermes_watchdog_breach{{metric="{key}"}} {1 if breaches[key] else 0}')
    lines.extend([
        "# HELP hermes_watchdog_flapping Whether the current incident's category is flapping (1) or not (0)",
        "# TYPE hermes_watchdog_flapping gauge",
        f"hermes_watchdog_flapping {1 if is_flapping else 0}",
        "# HELP hermes_watchdog_in_quiet_hours Whether the watchdog is currently inside a configured quiet-hours window (1) or not (0)",
        "# TYPE hermes_watchdog_in_quiet_hours gauge",
        f"hermes_watchdog_in_quiet_hours {1 if in_quiet_hours else 0}",
    ])
    lines.append("")
    return "\n".join(lines)


class _MetricsHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 (stdlib method name)
        if self.path != "/metrics":
            self.send_response(404)
            self.end_headers()
            return
        body = render_prometheus_text().encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        pass  # keep the watchdog's own console output clean


def start_metrics_server(port: int, host: str = "127.0.0.1") -> HTTPServer:
    """Starts a background daemon thread serving /metrics. Returns the
    server object; call .shutdown() on it to stop (mainly useful in tests -
    in normal use the process just exits)."""
    server = HTTPServer((host, port), _MetricsHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server
