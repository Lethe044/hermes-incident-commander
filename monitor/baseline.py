"""
Hermes Incident Commander - Time-of-Day-Aware Baseline
============================================================
Learns a simple per-hour-of-day baseline (running mean + standard
deviation, via Welford's online algorithm - no numpy/pandas needed) for
cpu/mem/disk from the watchdog's own poll history, persisted to
~/.hermes/incidents/baseline.json.

Opt-in via `WatchdogConfig.adaptive_thresholds` - when off (the default),
nothing in this module is called and thresholds behave exactly as before.

The goal: a nightly batch job that reliably pushes CPU to 70% at 2am
shouldn't page you every night once the watchdog has seen enough 2am
samples to know that's normal for this host, while an actual anomaly (CPU
spiking at 2am when it's normally quiet) still should.

Safety: the adaptive threshold can only ever RAISE the bar above your
configured static threshold, never lower it, and is capped at
`cap_multiplier` x the static threshold - so a real ongoing incident
can't slowly train the watchdog into ignoring itself.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

INCIDENT_DIR = Path.home() / ".hermes" / "incidents"
BASELINE_FILE = INCIDENT_DIR / "baseline.json"

# Don't trust an hour's baseline - and therefore don't raise the threshold
# for it - until we've seen at least this many samples for that hour.
MIN_SAMPLES_PER_HOUR = 20


def _load(path: Path = BASELINE_FILE) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save(data: dict[str, Any], path: Path = BASELINE_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def update_baseline(
    metric_samples: dict[str, float], hour: int, path: Path = BASELINE_FILE
) -> None:
    """Updates the running per-hour mean/variance for each metric in
    `metric_samples` (e.g. {"cpu": 23.4, "mem": 51.0, "disk": 46.2}).
    `hour` is 0-23, local time. Safe to call on every poll - this is O(1)
    per metric and never re-reads raw samples."""
    data = _load(path)
    hour_key = str(hour)
    for metric, value in metric_samples.items():
        stats = data.setdefault(metric, {}).setdefault(
            hour_key, {"n": 0, "mean": 0.0, "m2": 0.0}
        )
        n = stats["n"] + 1
        delta = value - stats["mean"]
        mean = stats["mean"] + delta / n
        delta2 = value - mean
        m2 = stats["m2"] + delta * delta2
        stats["n"], stats["mean"], stats["m2"] = n, mean, m2
    _save(data, path)


def get_adaptive_threshold(
    metric: str,
    hour: int,
    static_threshold: float,
    z_threshold: float = 3.0,
    cap_multiplier: float = 1.5,
    min_samples: int = MIN_SAMPLES_PER_HOUR,
    path: Path = BASELINE_FILE,
) -> float:
    """Returns the effective threshold to use for `metric` at `hour`:
    never below `static_threshold`, never above
    `static_threshold * cap_multiplier`, and only raised above
    `static_threshold` once this hour has at least `min_samples` samples."""
    data = _load(path)
    stats = data.get(metric, {}).get(str(hour))
    if not stats or stats.get("n", 0) < min_samples:
        return static_threshold

    n = stats["n"]
    variance = stats["m2"] / n if n > 1 else 0.0
    stddev = math.sqrt(max(variance, 0.0))
    adaptive = stats["mean"] + z_threshold * stddev

    return min(max(static_threshold, adaptive), static_threshold * cap_multiplier)


def hour_summary(path: Path = BASELINE_FILE) -> dict[str, dict[str, Any]]:
    """Returns a small human-readable summary per metric/hour - used by the
    `--show-baseline` CLI flag and handy for debugging. Hours with fewer
    than MIN_SAMPLES_PER_HOUR samples are marked as not-yet-trusted."""
    data = _load(path)
    summary: dict[str, dict[str, Any]] = {}
    for metric, hours in data.items():
        summary[metric] = {}
        for hour_key, stats in sorted(hours.items(), key=lambda kv: int(kv[0])):
            n = stats.get("n", 0)
            variance = stats["m2"] / n if n > 1 else 0.0
            summary[metric][hour_key] = {
                "n": n,
                "mean": round(stats.get("mean", 0.0), 1),
                "stddev": round(math.sqrt(max(variance, 0.0)), 1),
                "trusted": n >= MIN_SAMPLES_PER_HOUR,
            }
    return summary
