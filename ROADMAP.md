# Roadmap

A living backlog of ideas for Hermes Incident Commander. Unlike
[CHANGELOG.md](CHANGELOG.md) (what shipped), this file is for what's next -
it's meant to be edited outside of releases, so the project always has
somewhere to grow into.

Items move from here into a release's `CHANGELOG.md` entry once they ship.
If you want to pick one up, open an issue first so two people don't build
the same thing - see [CONTRIBUTING.md](CONTRIBUTING.md).

## Under consideration

- **Multi-host watchdog** - one watchdog process polling several hosts over
  SSH instead of one process per host. Needs careful thought before it
  touches `SAFETY.md`'s threat model - remote command execution changes the
  risk profile even if remediation stays allow-listed.
- **Slash-command / webhook query into `incident_db.py`** - let a Slack or
  Discord bot answer "have we seen X before?" by querying the SQLite index
  directly, instead of requiring shell access to the host. Needs Slack/Discord
  signature verification designed carefully before it's exposed to the network.
- **`--prune --archive` instead of only delete** - today `--prune --yes`
  deletes old report files outright; an `--archive DIR` option that moves
  them instead (still removing the SQLite/history rows) would suit anyone
  who wants cold storage rather than permanent deletion.
- **Prometheus gauges for flapping/quiet-hours state** - `prometheus_exporter.py`
  exposes cpu/mem/disk/breach today; `hermes_watchdog_flapping` and
  `hermes_watchdog_in_quiet_hours` gauges would let an existing
  Grafana/Prometheus stack show the same suppression state the dashboard
  and reports already do.
- **Scheduled `--prune` via the systemd installer** - `scripts/install-watchdog.sh`
  sets up the watchdog unit; a matching optional timer unit for
  `incident_db.py --prune --yes` would make retention hands-off instead of
  a manual/cron-it-yourself step.

## Explicitly out of scope (for now)

- Anything that would give the watchdog's model arbitrary shell access.
  That's what `demo/demo_incident.py` and the RL environment are for, and
  they're sandbox-only by design - see `SAFETY.md`.
- A hosted/SaaS version of the dashboard. It's intentionally a single
  offline HTML file with no server; that's a feature, not a gap.

## How to propose something new

Open an issue with the `enhancement` label describing the problem it
solves (not just the feature). If it's accepted, it gets added here; once
someone's actively working on it, link the PR from the entry.
