# Changelog

All notable changes to this project are documented here.
This project follows [Semantic Versioning](https://semver.org/).

## [2.1.0] - Unreleased

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
- 9 new tests in `tests/test_monitor.py` (`TestPagerDuty`) covering trigger,
  resolve, severity fallback, and `send_alert` integration. 55 tests total.

### Changed
- `SAFETY.md` and `.env.example` updated to cover `PAGERDUTY_ROUTING_KEY` as
  a secret with the same handling as webhook URLs.
- `skills/incident-commander/SKILL.md` - added a Kubernetes diagnostics
  block and a PagerDuty line to the notification templates and integration
  points; bumped skill `version` to `1.1`.

## [2.0.0] - Released

### Added
- **Standalone Watchdog** (`monitor/watchdog.py`) — continuously monitors
  real host metrics (CPU, memory, disk, failed systemd services) via
  `psutil`, triages breaches with Claude, writes structured incident
  reports, and can optionally perform **safe, allow-listed** auto-remediation
  (restart a whitelisted service, clean a whitelisted log directory). Works
  with just `ANTHROPIC_API_KEY` — no Hermes Agent installation required.
- **Notifier module** (`monitor/notifier.py`) — dependency-free Discord and
  Slack webhook alerts (stdlib `urllib` only), with a `--test-notify` helper.
- **Offline HTML dashboard** (`monitor/dashboard.py`) — generates a single,
  self-contained, dependency-free dashboard from incident history, with
  severity breakdown and a recent-incidents table. No server, no CDN.
- Two new incident scenarios: `docker-container-crash` (P1) and
  `network-unreachable` (P1), in both the RL environment and the demo.
- `SAFETY.md` documenting the threat model for demo mode vs. the watchdog's
  allow-listed auto-remediation.
- `pyproject.toml` with console-script entry points (`hermes-ic-demo`,
  `hermes-ic-watchdog`, `hermes-ic-dashboard`, `hermes-ic-notify-test`).
- GitHub Actions CI (`.github/workflows/ci.yml`) — runs the smoke test and
  full pytest suite on Python 3.10/3.11/3.12 on every push and PR, plus a
  `ruff` lint job.
- Issue templates, PR template, and `CONTRIBUTING.md`.
- `tests/test_monitor.py` covering the notifier, watchdog breach/threshold
  logic, safe-remediation allow-listing, and dashboard rendering.

### Changed
- `README.md` restructured with a "Standalone Mode" quickstart, badges, and
  an updated architecture/scenario overview.

## [1.0.0] — Hackathon submission

- Initial release: Atropos RL environment, `demo/demo_incident.py`,
  `skills/incident-commander/SKILL.md`, 5 incident scenarios, test suite.
