# Changelog

All notable changes to this project are documented here.
This project follows [Semantic Versioning](https://semver.org/).

## [2.2.0] - Unreleased

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
