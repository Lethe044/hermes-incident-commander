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
- **Teams Adaptive Card support** - the built-in Teams channel sends a
  classic MessageCard (the incoming-webhook connector format); Microsoft's
  newer Workflows webhooks expect an Adaptive Card instead. Today that's
  reachable via `GENERIC_WEBHOOK_URL` + a custom `GENERIC_WEBHOOK_TEMPLATE`,
  but first-class support would save that setup step.
- **P0 escalation / re-notify** - if a P0 stays unresolved for N minutes
  (config value), send a follow-up notification instead of relying on the
  next poll's own breach to naturally repeat the page - similar in spirit
  to the quiet-hours "still ongoing" notice, but for any P0, not just ones
  that started during quiet hours.
- **Update `docs/assets/grafana-dashboard.json`** - it has CPU/memory/disk
  gauges and a breach timeline; add panels for the `hermes_watchdog_flapping`
  and `hermes_watchdog_in_quiet_hours` gauges added in this release so the
  importable dashboard covers everything `/metrics` now exposes.

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
