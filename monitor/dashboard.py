#!/usr/bin/env python3
"""
Hermes Incident Commander - Dashboard Generator
==================================================
Renders a single, self-contained, dependency-free HTML dashboard from your
incident history - no server, no external CDN, no build step. Works offline.

Data sources (in priority order):
  1. ~/.hermes/incidents/history.jsonl  - structured records written by
     monitor/watchdog.py (preferred: accurate severity/category/metrics).
  2. ~/.hermes/incidents/*.md           - post-incident reports written by
     the demo or by Hermes itself (parsed heuristically).

Usage:
    python -m monitor.dashboard
    python -m monitor.dashboard --output ~/hermes-dashboard.html --open
"""

from __future__ import annotations

import argparse
import html
import json
import re
import webbrowser
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

INCIDENT_DIR = Path.home() / ".hermes" / "incidents"
HISTORY_LOG = INCIDENT_DIR / "history.jsonl"

SEVERITY_COLOR = {
    "P0": "#e5484d",
    "P1": "#f2994a",
    "P2": "#e0c341",
    "P3": "#4a9eda",
    "unknown": "#6b7280",
}

MD_FIELD_RE = re.compile(r"\*\*(Date|Severity|Duration|Impact)\*\*:?\s*([^\n]+)", re.IGNORECASE)


def load_from_jsonl() -> list[dict[str, Any]]:
    if not HISTORY_LOG.exists():
        return []
    records = []
    for line in HISTORY_LOG.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def load_from_markdown() -> list[dict[str, Any]]:
    if not INCIDENT_DIR.exists():
        return []
    records = []
    for path in sorted(INCIDENT_DIR.glob("*.md")):
        text = path.read_text(errors="ignore")
        fields = {k.lower(): v.strip() for k, v in MD_FIELD_RE.findall(text)}
        severity = fields.get("severity", "unknown").strip().upper()
        if severity not in SEVERITY_COLOR:
            severity = "unknown"
        records.append({
            "timestamp": fields.get("date", path.stem),
            "severity": severity,
            "category": "unknown",
            "root_cause": fields.get("impact", ""),
            "report_file": path.name,
            "auto_remediated": False,
        })
    return records


def load_records() -> list[dict[str, Any]]:
    records = load_from_jsonl()
    if records:
        return records
    return load_from_markdown()


def bar_svg(counts: Counter, width: int = 480, height: int = 160) -> str:
    """A tiny hand-rolled SVG bar chart - no chart.js / no CDN dependency."""
    severities = ["P0", "P1", "P2", "P3"]
    max_count = max([counts.get(s, 0) for s in severities] + [1])
    bar_w = width // (len(severities) * 2)
    bars = []
    for i, sev in enumerate(severities):
        count = counts.get(sev, 0)
        bar_h = int((count / max_count) * (height - 30))
        x = i * (width // len(severities)) + bar_w // 2
        y = height - bar_h - 20
        color = SEVERITY_COLOR[sev]
        bars.append(
            f'<rect x="{x}" y="{y}" width="{bar_w}" height="{max(bar_h, 2)}" '
            f'rx="4" fill="{color}" />'
            f'<text x="{x + bar_w/2}" y="{height - 4}" text-anchor="middle" '
            f'font-size="12" fill="#9ca3af">{sev}</text>'
            f'<text x="{x + bar_w/2}" y="{y - 6}" text-anchor="middle" '
            f'font-size="13" font-weight="600" fill="#e5e7eb">{count}</text>'
        )
    return (
        f'<svg viewBox="0 0 {width} {height}" width="100%" style="max-width:{width}px">'
        + "".join(bars) + "</svg>"
    )


def _incident_date(ts: str) -> str | None:
    """Extracts a YYYY-MM-DD date from an incident timestamp for grouping
    by day. Tries a real ISO-8601 parse first; falls back to a plain
    prefix check for hand-typed dates that fromisoformat rejects (still
    correct, just not timezone-aware)."""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        if len(ts) >= 10 and ts[4] == "-" and ts[7] == "-":
            return ts[:10]
        return None


def trend_svg(records: list[dict[str, Any]], width: int = 480, height: int = 140, days: int = 14) -> str:
    """A tiny hand-rolled SVG line chart of incidents-per-day over the
    trailing `days` days (today inclusive) - same no-chart.js/no-CDN style
    as bar_svg, so "is this getting better or worse" is visible at a
    glance without leaving the offline dashboard."""
    counts_by_date: Counter[str] = Counter()
    for r in records:
        d = _incident_date(str(r.get("timestamp", "")))
        if d:
            counts_by_date[d] += 1

    today = datetime.now().date()
    day_list = [(today - timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]
    values = [counts_by_date.get(d, 0) for d in day_list]

    if not any(values):
        return (
            f'<svg viewBox="0 0 {width} {height}" width="100%" style="max-width:{width}px">'
            f'<text x="{width / 2}" y="{height / 2}" text-anchor="middle" '
            f'font-size="12" fill="#6b7280">No incidents in the last {days} days</text></svg>'
        )

    max_count = max(values + [1])
    pad_left, pad_bottom, pad_top = 24, 20, 14
    plot_w = width - pad_left - 10
    plot_h = height - pad_bottom - pad_top
    n = len(day_list)
    step = plot_w / max(n - 1, 1)

    points = []
    for i, v in enumerate(values):
        x = pad_left + i * step
        y = pad_top + plot_h - (v / max_count) * plot_h
        points.append((x, y))

    path_d = "M " + " L ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    area_d = path_d + f" L {points[-1][0]:.1f},{pad_top + plot_h:.1f} L {points[0][0]:.1f},{pad_top + plot_h:.1f} Z"

    dots = "".join(
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.5" fill="#58a6ff" />' for x, y in points
    )

    # Only label the first, middle, and last day - labelling all 14+ points
    # would overlap at this width. Anchor the first/last labels to their
    # own edge (not centered) so they don't clip past the viewBox.
    label_idxs = sorted({0, n // 2, n - 1})
    label_parts = []
    for i in label_idxs:
        anchor = "start" if i == 0 else "end" if i == n - 1 else "middle"
        label_parts.append(
            f'<text x="{points[i][0]:.1f}" y="{height - 4}" text-anchor="{anchor}" '
            f'font-size="10" fill="#6b7280">{day_list[i][5:]}</text>'
        )
    labels = "".join(label_parts)

    return (
        f'<svg viewBox="0 0 {width} {height}" width="100%" style="max-width:{width}px">'
        f'<path d="{area_d}" fill="#58a6ff" fill-opacity="0.12" stroke="none" />'
        f'<path d="{path_d}" fill="none" stroke="#58a6ff" stroke-width="2" />'
        + dots + labels +
        f'<text x="{pad_left}" y="{pad_top - 2}" font-size="10" fill="#6b7280">max {max_count}/day</text>'
        "</svg>"
    )


def render_html(records: list[dict[str, Any]]) -> str:
    counts = Counter(r.get("severity", "unknown").upper() for r in records)
    total = len(records)
    remediated = sum(1 for r in records if r.get("auto_remediated"))
    critical = counts.get("P0", 0) + counts.get("P1", 0)

    rows = []
    for r in sorted(records, key=lambda x: x.get("timestamp", ""), reverse=True)[:50]:
        sev = r.get("severity", "unknown").upper()
        color = SEVERITY_COLOR.get(sev, SEVERITY_COLOR["unknown"])
        flap_badge = ' <span class="flap-badge" title="Flapping: repeated incidents of this category recently">🔁 flapping</span>' if r.get("flapping") else ""
        rows.append(f"""
        <tr>
          <td><span class="badge" style="background:{color}">{html.escape(sev)}</span></td>
          <td>{html.escape(str(r.get('timestamp', '')))}</td>
          <td>{html.escape(str(r.get('category', 'unknown')))}{flap_badge}</td>
          <td>{html.escape(str(r.get('root_cause', ''))[:120])}</td>
          <td>{'✅' if r.get('auto_remediated') else '-'}</td>
          <td>{html.escape(str(r.get('report_file', '')))}</td>
        </tr>""")

    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")

    search_box = """
    <div class="search-row">
      <input type="text" id="incident-search" placeholder="Search incidents (severity, category, root cause, report file)..." autocomplete="off">
      <span id="search-count" class="search-count"></span>
    </div>""" if rows else ""

    table_html = (
        '<table id="incidents-table"><thead><tr><th>Severity</th><th>Timestamp</th>'
        '<th>Category</th><th>Root Cause</th><th>Auto-fixed</th><th>Report</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table>'
        '<div class="empty" id="no-results" style="display:none">No incidents match your search.</div>'
    ) if rows else '<div class="empty">No incidents yet. Run the demo or the watchdog to populate this dashboard.</div>'

    search_script = """
  <script>
    // Pure, unit-testable matcher: kept separate from DOM wiring below so it
    // can be extracted and tested with a plain JS runtime (see
    // tests/test_monitor.py::TestDashboard).
    function rowMatches(text, query) {
      if (!query) return true;
      return text.toLowerCase().indexOf(query.toLowerCase()) !== -1;
    }

    (function () {
      var input = document.getElementById('incident-search');
      if (!input) return;
      var rows = Array.prototype.slice.call(
        document.querySelectorAll('#incidents-table tbody tr')
      );
      var noResults = document.getElementById('no-results');
      var searchCount = document.getElementById('search-count');

      input.addEventListener('input', function () {
        var query = input.value;
        var visible = 0;
        rows.forEach(function (row) {
          var match = rowMatches(row.textContent, query);
          row.style.display = match ? '' : 'none';
          if (match) visible++;
        });
        if (noResults) noResults.style.display = visible === 0 ? '' : 'none';
        if (searchCount) {
          searchCount.textContent = query
            ? visible + ' / ' + rows.length + ' shown'
            : '';
        }
      });
    })();
  </script>""" if rows else ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Hermes Incident Commander - Dashboard</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 32px; background: #0d1117; color: #e6edf3;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
  }}
  h1 {{ font-size: 22px; margin: 0 0 4px; }}
  .subtitle {{ color: #8b949e; font-size: 13px; margin-bottom: 28px; }}
  .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 14px; margin-bottom: 28px; }}
  .card {{ background: #161b22; border: 1px solid #30363d; border-radius: 10px; padding: 16px; }}
  .card .value {{ font-size: 28px; font-weight: 700; }}
  .card .label {{ font-size: 12px; color: #8b949e; margin-top: 4px; }}
  .panel {{ background: #161b22; border: 1px solid #30363d; border-radius: 10px; padding: 20px; margin-bottom: 24px; }}
  .panel h2 {{ font-size: 15px; margin: 0 0 14px; color: #c9d1d9; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th {{ text-align: left; color: #8b949e; font-weight: 500; padding: 8px 10px; border-bottom: 1px solid #30363d; }}
  td {{ padding: 8px 10px; border-bottom: 1px solid #21262d; vertical-align: top; }}
  .badge {{ color: #0d1117; font-weight: 700; font-size: 11px; padding: 2px 8px; border-radius: 999px; }}
  .empty {{ color: #8b949e; text-align: center; padding: 40px 0; }}
  footer {{ color: #6e7681; font-size: 12px; margin-top: 20px; }}
  .search-row {{ display: flex; align-items: center; gap: 10px; margin-bottom: 14px; }}
  .search-row input {{
    flex: 1; background: #0d1117; border: 1px solid #30363d; border-radius: 8px;
    color: #e6edf3; padding: 8px 12px; font-size: 13px; outline: none;
  }}
  .search-row input:focus {{ border-color: #58a6ff; }}
  .search-count {{ color: #8b949e; font-size: 12px; white-space: nowrap; }}
  .flap-badge {{
    display: inline-block; margin-left: 6px; font-size: 11px; color: #f2994a;
    background: rgba(242, 153, 74, 0.12); border: 1px solid rgba(242, 153, 74, 0.4);
    border-radius: 999px; padding: 1px 8px;
  }}
</style>
</head>
<body>
  <h1>⚕ Hermes Incident Commander - Dashboard</h1>
  <div class="subtitle">Generated {generated_at} · {total} incident(s) on record</div>

  <div class="cards">
    <div class="card"><div class="value">{total}</div><div class="label">Total Incidents</div></div>
    <div class="card"><div class="value" style="color:{SEVERITY_COLOR['P0']}">{critical}</div><div class="label">P0 + P1 (Critical)</div></div>
    <div class="card"><div class="value">{remediated}</div><div class="label">Auto-Remediated</div></div>
    <div class="card"><div class="value">{total - remediated}</div><div class="label">Manual / Reported Only</div></div>
  </div>

  <div class="panel">
    <h2>Incidents by Severity</h2>
    {bar_svg(counts)}
  </div>

  <div class="panel">
    <h2>Incidents per Day (last 14 days)</h2>
    {trend_svg(records)}
  </div>

  <div class="panel">
    <h2>Recent Incidents</h2>
    {search_box}
    {table_html}
  </div>

  <footer>Hermes Incident Commander · built on Hermes Agent by NousResearch · dashboard renders 100% offline, no external requests</footer>
{search_script}
</body>
</html>"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the Hermes Incident Commander dashboard")
    parser.add_argument("--output", default=str(Path.home() / ".hermes" / "dashboard.html"))
    parser.add_argument("--open", action="store_true", help="Open the dashboard in your browser")
    args = parser.parse_args()

    records = load_records()
    output_path = Path(args.output).expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_html(records))

    print(f"Dashboard written to {output_path} ({len(records)} incident(s))")
    if args.open:
        webbrowser.open(f"file://{output_path.resolve()}")


if __name__ == "__main__":
    main()
