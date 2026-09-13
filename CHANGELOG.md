# Changelog

All notable changes to this project are documented here.
This project follows [Semantic Versioning](https://semver.org/).

## [2.6.0] - Unreleased

### Added
- **Quiet hours / maintenance windows** - a new `quiet_hours` config field:
  recurring daily UTC windows (each `{"start": "HH:MM", "end": "HH:MM",
  "days": [...]}`, "days" optional, windows may wrap midnight) during
  which breaches are still detected, written to the report, and indexed
  as normal - only the outbound notification is suppressed. `in_quiet_hours()`
  in `monitor/watchdog.py`; validated by `--validate-config` (malformed
  windows, bad HH:MM format, invalid weekday abbreviations are all
  flagged as errors). The flapping-repeat throttle added in 2.5.0 and
  quiet hours now share one `suppress_reason` decision in `run_once()`.
- **Generic webhook notification channel** - `GENERIC_WEBHOOK_URL` (or
  `Notifier(generic_webhook_url=...)`) posts a small, stable JSON body
  (`source`, `title`, `message`, `severity` - severity omitted when not
  applicable) to any endpoint that accepts a JSON POST, for platforms
  without first-class support (Opsgenie, Microsoft Teams via a relay, an
  internal tool). Reaches every `send()`-based notification path
  (`send_alert`, `send_p0_alert`, `send_resolution`, `send_daily_briefing`,
  the `--test-notify` CLI helper).
- **Clickable category chart in the offline dashboard** - clicking a bar
  in "Incidents by Category" (`category_svg()`) now filters the
  recent-incidents table to that category via a new `window.filterByCategory()`,
  reusing the existing search box's filter logic rather than a separate
  implementation. Category names are safely escaped
  (`html.escape(json.dumps(...))`) before being embedded in the inline
  `onclick` handler.
- **`incident_db.py --stats --format csv`** - the severity/category
  breakdown as a flat `dimension,key,value` CSV, easy to chart in a
  spreadsheet. `--format` now applies uniformly to both `--search` and
  `--stats`.
- 27 new tests: generic-webhook tests in `TestNotifier` (4),
  `TestQuietHours` (10), `quiet_hours` validation tests in
  `TestValidateConfig` (6), `--stats --format csv` tests (2), clickable
  category-chart tests including a Node.js DOM-simulation test for
  `filterByCategory` (3), and quiet-hours suppression tests in
  `TestRunOnceAndPagerDutyResolve` (2). 186 tests total across the suite.

## [2.5.0] - Released

### Added
- **Threshold suggestion for flapping incidents** - `suggest_threshold()` in
  `monitor/watchdog.py`: when a cpu/mem/disk category is flapping, the
  report now proposes a specific new threshold value (preferring a learned
  `--adaptive-thresholds` baseline for the current hour when trusted data
  exists, otherwise a conservative +5 bump capped at 98) instead of just
  repeating the "FLAPPING DETECTED" warning. Non-numeric categories
  (`service`, `network`, ...) get no suggestion, since there's no single
  threshold to change.
- **Notification throttling during an ongoing flap** - once a category has
  crossed the flapping threshold and been alerted on once, `run_once()` no
  longer sends a repeat Discord/Slack/PagerDuty notification for every
  additional occurrence in the same window - only the first crossing
  notifies. The incident report and `history.jsonl` still record every
  occurrence; only the outbound page is throttled.
- **`incident_db.py --stats`** - `compute_stats()`/`format_stats()`: total
  incidents, breakdown by severity and category, auto-remediation rate,
  incidents in the last 7/30 days, most recent incident, and the busiest
  category. Supports `--format json` alongside the default text summary.
- **Category breakdown chart in the offline dashboard** - `category_svg()`
  in `monitor/dashboard.py`: a horizontal SVG bar chart of incident counts
  by category (same hand-rolled-SVG/no-chart.js style as the other charts),
  in its own panel between "Incidents by Severity" and the trend chart.
  Caps at 8 categories with a "+N more categories not shown" footer for
  hosts with a lot of distinct categories.

### Fixed
- **Real path-isolation bug in `flapping.py`, `baseline.py`, and
  `incident_db.py`.** Their `path`/`db_path` function parameters defaulted
  to a module-level constant (e.g. `path: Path = FREQUENCY_FILE`), which
  Python binds once at import time - so patching `flapping.FREQUENCY_FILE`
  (or `baseline.BASELINE_FILE`, `incident_db.DB_PATH`) afterward, including
  from tests via `monkeypatch`, was silently ignored and calls kept using
  the original path. In practice this meant several existing tests that
  believed they were writing to an isolated `tmp_path` were actually
  writing to the real `~/.hermes/incidents/` directory. Changed all of
  these to `path: Path | None = None` with the module constant resolved
  fresh inside the function body on every call, and updated the affected
  tests to patch every module a code path touches (watchdog's
  `write_incident()`/`run_once()` also reach into `monitor.flapping` and
  `monitor.incident_db`, not just its own globals).
- 29 new tests: `TestSuggestThreshold` (6), notification-throttling tests
  in `TestRunOnceAndPagerDutyResolve` (3), flapping-report-suggestion tests
  in `TestFlapping` (2), `TestIncidentDBStats` (10),
  `TestDashboardCategoryChart` (8) - plus path-isolation fixes to 2
  pre-existing tests that were unknowingly writing to the real
  `~/.hermes/incidents/`. 159 tests total across the suite.

## [2.4.0] - Released

### Added
- **Trend chart in the offline dashboard** - `monitor/dashboard.py` now
  renders a 14-day incidents-per-day line chart (`trend_svg()`, same
  hand-rolled-SVG/no-chart.js style as the severity bar chart) in its own
  panel between "Incidents by Severity" and the incidents table, so "is
  this getting better or worse" is visible at a glance without leaving the
  offline dashboard. Groups incidents by day from their timestamp (real
  ISO-8601 parse with a plain-prefix fallback for hand-typed dates);
  renders a "No incidents in the last N days" message instead of an empty
  chart when there's nothing to show.
- **`--validate-config`** - `monitor/watchdog.py --config PATH
  --validate-config` checks the file for unrecognized keys (a likely
  typo - these were previously silently ignored), thresholds outside
  0-100, a non-positive `poll_interval_seconds`, an invalid
  `consecutive_breaches_required`/`cooldown_minutes`, and allow-list
  entries that don't actually match anything (e.g. a `restart_services`
  entry for a service that isn't in `watched_services`, or
  `auto_remediate: true` with an empty allow-list). Prints the fully
  resolved config and exits 0/1 - without starting the watchdog.
  Complements `--dry-run`, which validates behavior against live metrics
  rather than the config file itself.
- **CSV/JSON export from `incident_db.py --search`** - `--format json` or
  `--format csv` alongside the existing default text output
  (`format_results()`), so a search's results can be piped straight into
  another tool or a weekly incident-review report instead of only being
  read on screen.
- 34 new tests in `tests/test_monitor.py` (`TestIncidentDBExport`: 7,
  `TestValidateConfig`: 20, `TestDashboardTrend`: 7). 130 tests total
  across the suite.

## [2.3.0] - Released

### Added
- **Search box in the offline dashboard** - `monitor/dashboard.py`'s
  "Recent Incidents" table now has a live, client-side search/filter
  (severity, category, root cause, or report filename). Pure vanilla JS,
  no new dependency; only rendered when there's at least one incident.
- **Notifier retry/backoff** - `monitor/notifier.py`'s `_post_json` now
  retries transient failures (timeouts, connection errors, HTTP 429/5xx)
  with exponential backoff (`max_retries=2`, `backoff_seconds=0.5` by
  default, both configurable on `Notifier(...)`). Non-retryable client
  errors (4xx other than 429) fail fast instead of wasting time retrying
  a bad payload or bad auth token.
- **Flapping detection** (`monitor/flapping.py`) - tracks how often each
  incident category has fired recently. 3+ incidents of the same category
  within 60 minutes (both configurable) gets flagged with a
  "⚠️ FLAPPING DETECTED" banner at the top of the incident report, a
  console warning, a `flapping` field in `history.jsonl`, and a
  "🔁 flapping" badge in the dashboard table.
- **`scripts/install-watchdog.sh`** - a one-command systemd installer for
  the watchdog. Dry-run by default (nothing is written until you pass
  `--yes`); idempotent; writes secrets to a root-only (chmod 600)
  EnvironmentFile rather than embedding them in the unit file; refuses to
  proceed with `--yes` if systemd isn't actually reachable instead of
  failing halfway through. `--uninstall --yes` removes it again.
- 26 new tests in `tests/test_monitor.py` (`TestNotifierRetry`: 5,
  `TestFlapping`: 7, `TestDashboardSearch`: 4, plus baseline/incident_db
  coverage from 2.2.0). 96 tests total across the suite.

## [2.2.0] - Released

### Added
- **2 more cloud-native incident scenarios**: `ecs-task-crashloop` (P1) and
  `lambda-timeout-spike` (P2). Fully self-contained (local JSON/log files
  standing in for `aws ecs describe-tasks` / CloudWatch Logs output) so
  they run the same in CI as on a laptop - no AWS credentials needed.
  10 incident scenarios total.
- **`monitor/incident_db.py`** - local incident search over
  `history.jsonl` and its linked `.md` reports, using SQLite (stdlib only,
  no new dependency). Uses FTS5 full-text search when available, and
  degrades automatically to a `LIKE`-based scan on SQLite builds without
  FTS5. `write_incident()` now keeps the index in sync automatically after
  every incident; `python -m monitor.incident_db --sync`/`--search` are
  there for manual use or a cron job.
- **`monitor/baseline.py` + `WatchdogConfig.adaptive_thresholds`** -
  opt-in, time-of-day-aware thresholds. Learns a running per-hour mean/
  stddev (Welford's algorithm) from the watchdog's own polls and can raise
  the effective threshold above your configured static one during
  historically busy hours - but never lower it, and never above
  `static_threshold * 1.5`, so an ongoing real incident can't train the
  watchdog into ignoring itself. `--adaptive-thresholds` to opt in,
  `--show-baseline` to inspect what's been learned per hour.
- **Grafana dashboard JSON** (`docs/assets/grafana-dashboard.json`) - a
  ready-to-import dashboard (CPU/mem/disk gauges, failed-services stat, a
  timeseries panel, and a breach-status timeline) for the Prometheus
  `/metrics` endpoint added in 2.1.0.
- 14 new tests in `tests/test_monitor.py` (`TestIncidentDB`: 6,
  `TestBaseline`: 8). 80 tests total.

## [2.1.0] - Released

### Added
- **Kubernetes pod crash-loop scenario** (`k8s-pod-crashloop`, P1) - new
  incident scenario in both the RL environment and the standalone demo.
  Degrades gracefully (like the Docker scenario) when `kubectl` or a
  cluster isn't available. 8 incident scenarios total.
- **PagerDuty integration** (`monitor/notifier.py`) - `send_pagerduty_event()`
  and `resolve_pagerduty_event()` trigger/resolve incidents via PagerDuty's
  Events API v2, using only stdlib `urllib` (no new dependency). Configured
  via `PAGERDUTY_ROUTING_KEY`. `send_alert()` and `send_p0_alert()` now fire
  PagerDuty automatically alongside Discord/Slack when a routing key is set.
- A real dashboard screenshot (`docs/assets/dashboard-screenshot.png`),
  generated from `monitor/dashboard.py`'s actual HTML output against sample
  incident data, and an illustrative demo terminal mockup
  (`docs/assets/demo-terminal-example.svg`) - both linked from the README.
- Live GitHub star-history chart embedded in the README.
- `ROADMAP.md` - a living backlog of ideas for continuous development,
  separate from the changelog so it doesn't need a release to be updated.
- **`--dry-run` mode** for the watchdog (`python -m monitor.watchdog --dry-run`)
  - runs one check, shows exactly what `--auto-remediate` would restart or
  delete (prefixed `[DRY RUN]`), and does not touch services, files, or
  notification channels. Lets you validate a new `watchdog_config.yaml`
  before trusting it.
- **PagerDuty resolve wiring** - the watchdog now tracks which breach types
  have an open PagerDuty incident (persisted to
  `~/.hermes/incidents/open_pagerduty_incidents.json` so it survives across
  `--once`/cron invocations) and calls `resolve_pagerduty_event()`
  automatically once a breach recovers, instead of leaving incidents open
  forever.
- **Prometheus `/metrics` endpoint** (`monitor/prometheus_exporter.py`) -
  optional, stdlib-only (`http.server`) exporter. Start it with
  `python -m monitor.watchdog --metrics-port 9877`; binds to `127.0.0.1` by
  default. Exposes `hermes_watchdog_cpu_percent`,
  `hermes_watchdog_mem_percent`, `hermes_watchdog_disk_percent`,
  `hermes_watchdog_failed_services_count`, and a `hermes_watchdog_breach`
  gauge per metric.
- 20 new tests in `tests/test_monitor.py` covering PagerDuty (9), dry-run
  remediation (4), `run_once` open/resolve wiring (4), and the Prometheus
  exporter (3). 66 tests total.

### Changed
- `SAFETY.md` and `.env.example` updated to cover `PAGERDUTY_ROUTING_KEY` as
  a secret with the same handling as webhook URLs.
- `skills/incident-commander/SKILL.md` - added a Kubernetes diagnostics
  block and a PagerDuty line to the notification templates and integration
  points; bumped skill `version` to `1.1`.

### Fixed
- Repo-wide em dash cleanup - all em dashes replaced with regular hyphens
  across every `.py`, `.md`, `.yaml`, and `.toml` file (code, comments,
  docs, and the dashboard's "not auto-fixed" table glyph), per the
  project's style rule.

## [2.0.0] - Released

### Added
- **Standalone Watchdog** (`monitor/watchdog.py`) - continuously monitors
  real host metrics (CPU, memory, disk, failed systemd services) via
  `psutil`, triages breaches with Claude, writes structured incident
  reports, and can optionally perform **safe, allow-listed** auto-remediation
  (restart a whitelisted service, clean a whitelisted log directory). Works
  with just `ANTHROPIC_API_KEY` - no Hermes Agent installation required.
- **Notifier module** (`monitor/notifier.py`) - dependency-free Discord and
  Slack webhook alerts (stdlib `urllib` only), with a `--test-notify` helper.
- **Offline HTML dashboard** (`monitor/dashboard.py`) - generates a single,
  self-contained, dependency-free dashboard from incident history, with
  severity breakdown and a recent-incidents table. No server, no CDN.
- Two new incident scenarios: `docker-container-crash` (P1) and
  `network-unreachable` (P1), in both the RL environment and the demo.
- `SAFETY.md` documenting the threat model for demo mode vs. the watchdog's
  allow-listed auto-remediation.
- `pyproject.toml` with console-script entry points (`hermes-ic-demo`,
  `hermes-ic-watchdog`, `hermes-ic-dashboard`, `hermes-ic-notify-test`).
- GitHub Actions CI (`.github/workflows/ci.yml`) - runs the smoke test and
  full pytest suite on Python 3.10/3.11/3.12 on every push and PR, plus a
  `ruff` lint job.
- Issue templates, PR template, and `CONTRIBUTING.md`.
- `tests/test_monitor.py` covering the notifier, watchdog breach/threshold
  logic, safe-remediation allow-listing, and dashboard rendering.

### Changed
- `README.md` restructured with a "Standalone Mode" quickstart, badges, and
  an updated architecture/scenario overview.

## [1.0.0] - Hackathon submission

- Initial release: Atropos RL environment, `demo/demo_incident.py`,
  `skills/incident-commander/SKILL.md`, 5 incident scenarios, test suite.
