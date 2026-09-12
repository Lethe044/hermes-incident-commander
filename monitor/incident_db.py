#!/usr/bin/env python3
"""
Hermes Incident Commander - Incident Search (SQLite)
========================================================
Builds a local, file-based SQLite database (~/.hermes/incidents/incidents.db)
from history.jsonl and its linked *.md reports, with a full-text index over
the root cause and report body, so "have we seen this before?" works for the
standalone watchdog too - not just inside a full Hermes install.

Uses only the Python standard library (sqlite3). No new dependency.

If the local SQLite build doesn't have the FTS5 extension compiled in (rare,
but happens on some minimal Python builds), this falls back automatically to
a plain table with a `LIKE`-based search - slower and less relevant-ranked,
but still correct and dependency-free.

Usage:
    python -m monitor.incident_db --sync              # (re)build the index from history.jsonl
    python -m monitor.incident_db --search "nginx"     # full-text search
    python -m monitor.incident_db --search "disk full" --limit 5
    python -m monitor.incident_db --search "nginx" --format json
    python -m monitor.incident_db --search "nginx" --format csv > incidents.csv
    python -m monitor.incident_db --stats              # summary: counts, rate, busiest category
    python -m monitor.incident_db --stats --format json
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

RESULT_FIELDS = [
    "timestamp", "severity", "category", "root_cause", "report_file", "auto_remediated",
]

INCIDENT_DIR = Path.home() / ".hermes" / "incidents"
HISTORY_LOG = INCIDENT_DIR / "history.jsonl"
DB_PATH = INCIDENT_DIR / "incidents.db"

_BASE_SCHEMA = """
CREATE TABLE IF NOT EXISTS incidents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT,
    severity TEXT,
    category TEXT,
    root_cause TEXT,
    report_file TEXT UNIQUE,
    auto_remediated INTEGER,
    report_body TEXT
);
"""

_FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS incidents_fts USING fts5(
    root_cause, report_body, content='incidents', content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS incidents_ai AFTER INSERT ON incidents BEGIN
  INSERT INTO incidents_fts(rowid, root_cause, report_body)
  VALUES (new.id, new.root_cause, new.report_body);
END;

CREATE TRIGGER IF NOT EXISTS incidents_ad AFTER DELETE ON incidents BEGIN
  INSERT INTO incidents_fts(incidents_fts, rowid, root_cause, report_body)
  VALUES ('delete', old.id, old.root_cause, old.report_body);
END;

CREATE TRIGGER IF NOT EXISTS incidents_au AFTER UPDATE ON incidents BEGIN
  INSERT INTO incidents_fts(incidents_fts, rowid, root_cause, report_body)
  VALUES ('delete', old.id, old.root_cause, old.report_body);
  INSERT INTO incidents_fts(rowid, root_cause, report_body)
  VALUES (new.id, new.root_cause, new.report_body);
END;
"""


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    """Opens (creating if needed) the incidents database and ensures the
    schema exists.

    `db_path` defaults to the *current* value of `DB_PATH` (resolved each
    call, not bound at import time) so tests can monkeypatch
    `incident_db.DB_PATH` and have it actually take effect; a
    `Path = DB_PATH` default argument would silently ignore that patch and
    keep connecting to the original path."""
    db_path = db_path if db_path is not None else DB_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_BASE_SCHEMA)
    try:
        conn.executescript(_FTS_SCHEMA)
    except sqlite3.OperationalError:
        # e.g. "no such module: fts5" on a minimal SQLite build - degrade
        # gracefully to LIKE-based search rather than crashing. _fts_available()
        # re-checks by inspecting sqlite_master, so nothing else needs to know.
        pass
    return conn


def _fts_available(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='incidents_fts'"
    ).fetchone()
    return row is not None


def _load_report_body(report_file: str) -> str:
    if not report_file:
        return ""
    path = INCIDENT_DIR / report_file
    if path.exists():
        try:
            return path.read_text(errors="ignore")
        except OSError:
            return ""
    return ""


def sync(conn: sqlite3.Connection | None = None) -> int:
    """(Re)builds the incidents table from history.jsonl. Upserts by
    report_file so re-running is idempotent (safe to call after every
    incident, or on a cron job). Returns the number of records read."""
    own_conn = conn is None
    conn = conn or get_connection()
    if not HISTORY_LOG.exists():
        if own_conn:
            conn.close()
        return 0

    count = 0
    for i, line in enumerate(HISTORY_LOG.read_text().splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue

        report_file = rec.get("report_file") or f"unknown-{i}"
        body = _load_report_body(rec.get("report_file", ""))
        conn.execute(
            """
            INSERT INTO incidents (timestamp, severity, category, root_cause,
                                    report_file, auto_remediated, report_body)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(report_file) DO UPDATE SET
                timestamp=excluded.timestamp,
                severity=excluded.severity,
                category=excluded.category,
                root_cause=excluded.root_cause,
                auto_remediated=excluded.auto_remediated,
                report_body=excluded.report_body
            """,
            (
                rec.get("timestamp", ""),
                rec.get("severity", "unknown"),
                rec.get("category", "unknown"),
                rec.get("root_cause", ""),
                report_file,
                1 if rec.get("auto_remediated") else 0,
                body,
            ),
        )
        count += 1

    conn.commit()
    if own_conn:
        conn.close()
    return count


def _fts_query(raw: str) -> str:
    """Wraps every whitespace-separated term in its own FTS5 phrase so
    arbitrary punctuation in real incident text (hyphens, colons, etc.)
    can't break FTS5's query syntax. Terms are implicitly AND-ed."""
    terms = raw.split()
    if not terms:
        return '""'
    return " ".join('"' + t.replace('"', '""') + '"' for t in terms)


def search(
    query: str, limit: int = 10, conn: sqlite3.Connection | None = None
) -> list[dict[str, Any]]:
    """Searches root_cause + report_body for `query`. Uses FTS5 (ranked by
    relevance) when available, otherwise falls back to a `LIKE` scan over
    the same two columns."""
    own_conn = conn is None
    conn = conn or get_connection()
    conn.row_factory = sqlite3.Row
    fts_available = _fts_available(conn)

    if fts_available:
        rows = conn.execute(
            """
            SELECT incidents.timestamp, incidents.severity, incidents.category,
                   incidents.root_cause, incidents.report_file,
                   incidents.auto_remediated
            FROM incidents_fts
            JOIN incidents ON incidents.id = incidents_fts.rowid
            WHERE incidents_fts MATCH ?
            ORDER BY bm25(incidents_fts)
            LIMIT ?
            """,
            (_fts_query(query), limit),
        ).fetchall()
    else:
        like = f"%{query}%"
        rows = conn.execute(
            """
            SELECT timestamp, severity, category, root_cause, report_file,
                   auto_remediated
            FROM incidents
            WHERE root_cause LIKE ? OR report_body LIKE ?
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (like, like, limit),
        ).fetchall()

    results = [dict(r) for r in rows]
    if own_conn:
        conn.close()
    return results


def format_results(results: list[dict[str, Any]], fmt: str = "text") -> str:
    """Renders search results as `text` (the original human-readable
    two-line-per-incident format), `json` (a JSON array, one object per
    incident), or `csv` (a header row plus one row per incident) - so the
    same search() output can be piped into a report or another tool
    instead of only being printed for a human to read."""
    if fmt == "json":
        return json.dumps(results, indent=2)

    if fmt == "csv":
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=RESULT_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for r in results:
            writer.writerow(r)
        return buf.getvalue().rstrip("\n")

    if not results:
        return "No matching incidents found."
    lines = []
    for r in results:
        lines.append(f"[{r['severity']}] {r['timestamp']} ({r['category']}) - {r['root_cause'][:100]}")
        lines.append(f"    report: {r['report_file']}")
    return "\n".join(lines)


def _parse_ts(ts: str) -> datetime | None:
    """Parses an incident timestamp into an aware UTC datetime for age
    calculations. Timestamps come from more than one source (the
    watchdog's own ISO-8601 output, hand-parsed markdown reports) so this
    tolerates both naive and 'Z'-suffixed forms, and returns None rather
    than raising on anything it can't parse."""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def compute_stats(conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    """Aggregates the incidents table into summary stats: totals, a
    breakdown by severity and category, the auto-remediation rate, how
    many incidents happened in the last 7/30 days, and the busiest
    category - so "how are we doing" doesn't require eyeballing raw
    search results."""
    own_conn = conn is None
    conn = conn or get_connection()
    conn.row_factory = sqlite3.Row

    total = conn.execute("SELECT COUNT(*) AS n FROM incidents").fetchone()["n"]

    by_severity: dict[str, int] = {}
    for row in conn.execute(
        "SELECT COALESCE(NULLIF(severity, ''), 'unknown') AS severity, COUNT(*) AS n "
        "FROM incidents GROUP BY severity"
    ):
        by_severity[row["severity"]] = row["n"]

    by_category: dict[str, int] = {}
    for row in conn.execute(
        "SELECT COALESCE(NULLIF(category, ''), 'unknown') AS category, COUNT(*) AS n "
        "FROM incidents GROUP BY category"
    ):
        by_category[row["category"]] = row["n"]

    auto_remediated = conn.execute(
        "SELECT COUNT(*) AS n FROM incidents WHERE auto_remediated = 1"
    ).fetchone()["n"]

    most_recent_row = conn.execute(
        "SELECT timestamp FROM incidents WHERE timestamp IS NOT NULL AND timestamp != '' "
        "ORDER BY timestamp DESC LIMIT 1"
    ).fetchone()
    most_recent = most_recent_row["timestamp"] if most_recent_row else None

    # Computed in Python rather than with SQLite's date() functions, since
    # timestamps aren't guaranteed to all be in a format SQLite understands.
    now = datetime.now(timezone.utc)
    last_7_days = last_30_days = 0
    for row in conn.execute("SELECT timestamp FROM incidents"):
        dt = _parse_ts(row["timestamp"])
        if dt is None:
            continue
        age_days = (now - dt).total_seconds() / 86400
        if age_days <= 7:
            last_7_days += 1
        if age_days <= 30:
            last_30_days += 1

    top_category = max(by_category, key=by_category.get) if by_category else None

    result = {
        "total": total,
        "by_severity": by_severity,
        "by_category": by_category,
        "auto_remediated": auto_remediated,
        "auto_remediated_rate": round(auto_remediated / total, 3) if total else 0.0,
        "last_7_days": last_7_days,
        "last_30_days": last_30_days,
        "most_recent_timestamp": most_recent,
        "top_category": top_category,
    }
    if own_conn:
        conn.close()
    return result


_SEVERITY_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}


def format_stats(stats: dict[str, Any], fmt: str = "text") -> str:
    """Renders compute_stats() output as `text` (a short human-readable
    summary) or `json` (the raw dict) - mirrors format_results()'s output
    contract so --search and --stats behave consistently."""
    if fmt == "json":
        return json.dumps(stats, indent=2)

    if stats["total"] == 0:
        return "No incidents recorded yet. Run --sync after the watchdog has written some history."

    lines = [
        f"Total incidents: {stats['total']}",
        f"Auto-remediated: {stats['auto_remediated']} ({stats['auto_remediated_rate'] * 100:.1f}%)",
        f"Last 7 days: {stats['last_7_days']}   Last 30 days: {stats['last_30_days']}",
    ]
    if stats["most_recent_timestamp"]:
        lines.append(f"Most recent: {stats['most_recent_timestamp']}")
    if stats["top_category"]:
        lines.append(
            f"Busiest category: {stats['top_category']} "
            f"({stats['by_category'][stats['top_category']]} incident(s))"
        )

    lines.append("")
    lines.append("By severity:")
    for sev in sorted(stats["by_severity"], key=lambda s: (_SEVERITY_ORDER.get(s, 99), s)):
        lines.append(f"  {sev}: {stats['by_severity'][sev]}")

    lines.append("")
    lines.append("By category:")
    for cat, n in sorted(stats["by_category"].items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"  {cat}: {n}")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Hermes Incident Commander - local incident search (SQLite, no server)"
    )
    parser.add_argument("--sync", action="store_true", help="(Re)build the index from history.jsonl")
    parser.add_argument("--search", metavar="QUERY", help="Search past incidents")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument(
        "--stats", action="store_true",
        help="Print summary stats (totals, by severity/category, auto-remediation rate, "
             "last 7/30 days, busiest category)",
    )
    parser.add_argument(
        "--format", choices=["text", "json", "csv"], default="text",
        help="Output format for --search/--stats results (default: text; csv only applies "
             "to --search). json/csv are meant to be piped into a report or another tool.",
    )
    args = parser.parse_args()

    if not args.sync and not args.search and not args.stats:
        parser.print_help()
        return

    conn = get_connection()
    try:
        if args.sync:
            n = sync(conn)
            print(f"Synced {n} incident(s) into {DB_PATH}")

        if args.search:
            results = search(args.search, limit=args.limit, conn=conn)
            output = format_results(results, fmt=args.format)
            if output:
                print(output)

        if args.stats:
            fmt = "json" if args.format == "json" else "text"
            print(format_stats(compute_stats(conn), fmt=fmt))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
