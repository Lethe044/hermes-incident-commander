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
- **Suggest a threshold, don't just flag flapping** - `monitor/flapping.py`
  (2.3.0) flags repeated incidents but doesn't act on it. Once it's flagged
  something a few times, the watchdog could suggest a concrete
  `--adaptive-thresholds`/config change instead of just repeating the warning.
- **Slash-command / webhook query into `incident_db.py`** - let a Slack or
  Discord bot answer "have we seen X before?" by querying the SQLite index
  directly, instead of requiring shell access to the host.
- **Trend chart in the offline dashboard** - `monitor/dashboard.py` shows
  counts by severity today; a small incidents-per-day line (still hand-rolled
  SVG, no chart.js) would make "is this getting better or worse" visible at
  a glance.
- **`--validate-config`** - a watchdog flag that checks a
  `watchdog_config.yaml` for typos/invalid allow-list entries and prints
  what it would actually do, without starting the watchdog. Complements
  `--dry-run`, which validates behavior against live metrics rather than
  the config file itself.
- **CSV/JSON export from `incident_db.py --search`** - the CLI prints to
  stdout today; a `--format json`/`--format csv` flag would make it easy to
  pipe into a weekly incident-review report.

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
