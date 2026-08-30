# Changelog

All notable changes to this project are documented here.
This project follows [Semantic Versioning](https://semver.org/).

## [2.0.0] — Unreleased

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
