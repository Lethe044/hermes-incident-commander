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
- **Retention / pruning for old incident reports** - `INCIDENT_DIR` grows
  unbounded today. A `--prune --older-than-days N` (dry-run by default,
  `--yes` to actually delete/archive) for `incident_db.py` would keep disk
  usage bounded on long-running installs without touching the safety model
  (it only ever removes informational markdown reports and old DB/history
  rows, never live remediation).
- **Configurable generic-webhook payload template** - the payload shape is
  fixed today (`source`, `title`, `message`, `severity`); some platforms
  expect a different shape and would need a small Jinja-style template
  option instead of code changes per integration.
- **Delayed "still ongoing" notification after quiet hours end** - if a
  breach is still active when a quiet-hours window closes, send one
  notification then instead of staying silent until the next unrelated
  poll happens to notice.

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
