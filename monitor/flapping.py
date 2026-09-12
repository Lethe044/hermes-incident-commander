"""
Hermes Incident Commander - Flapping Detection
====================================================
Tracks how often each incident category has fired recently, persisted to
~/.hermes/incidents/incident_frequency.json, so repeated incidents of the
same category (flapping - usually a real unresolved problem, or thresholds
tuned too tight) get flagged in the report instead of silently repeating
forever with no signal that something is off.

Uses only the Python standard library. No new dependency.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

INCIDENT_DIR = Path.home() / ".hermes" / "incidents"
FREQUENCY_FILE = INCIDENT_DIR / "incident_frequency.json"

# This many incidents of the same category within the window counts as flapping.
DEFAULT_WINDOW_MINUTES = 60
DEFAULT_FLAP_THRESHOLD = 3


def _load(path: Path | None = None) -> dict[str, list[str]]:
    path = path if path is not None else FREQUENCY_FILE
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save(data: dict[str, list[str]], path: Path | None = None) -> None:
    path = path if path is not None else FREQUENCY_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def _parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def record_and_check(
    category: str,
    timestamp: str,
    window_minutes: int = DEFAULT_WINDOW_MINUTES,
    flap_threshold: int = DEFAULT_FLAP_THRESHOLD,
    path: Path | None = None,
) -> dict[str, Any]:
    """Records this incident's timestamp for `category`, prunes timestamps
    older than `window_minutes` so the file never grows unbounded, and
    returns how many incidents of that category have occurred within the
    trailing window (including this one) plus whether that meets
    `flap_threshold`.

    `path` defaults to the *current* value of `FREQUENCY_FILE` (resolved
    each call, not bound at import time) so tests - or anything else - can
    monkeypatch `flapping.FREQUENCY_FILE` and have it actually take
    effect; a `Path = FREQUENCY_FILE` default argument would silently
    ignore that patch and keep writing to the original path."""
    path = path if path is not None else FREQUENCY_FILE
    data = _load(path)
    timestamps = data.get(category, [])

    try:
        now = _parse_ts(timestamp)
        cutoff = now - timedelta(minutes=window_minutes)
        recent = [t for t in timestamps if _parse_ts(t) >= cutoff]
    except (ValueError, TypeError):
        # Malformed/unparseable timestamp on an old entry - don't let bad
        # historical data break flapping detection; just start fresh for
        # this category rather than crashing the watchdog.
        recent = []

    recent.append(timestamp)
    recent.sort()
    data[category] = recent
    _save(data, path)

    count = len(recent)
    return {
        "category": category,
        "count": count,
        "window_minutes": window_minutes,
        "flap_threshold": flap_threshold,
        "is_flapping": count >= flap_threshold,
    }
