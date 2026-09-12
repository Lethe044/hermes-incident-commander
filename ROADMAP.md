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
- **CSV/JSON export for `incident_db.py --stats`** - `--stats` currently
  supports `text`/`json`; a `--format csv` for the by-severity/by-category
  breakdown would make it easy to chart in a spreadsheet.
- **Category chart drill-down in the dashboard** - clicking a bar in
  "Incidents by Category" could filter the recent-incidents table to that
  category, reusing the existing client-side search box's filtering logic.
- **Maintenance windows / quiet hours** - let a config mark a time range
  (e.g. a known nightly batch job) where breaches still get logged and
  indexed, but don't send a notification - broader than flapping throttling,
  which only kicks in after a category has already started repeating.

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
