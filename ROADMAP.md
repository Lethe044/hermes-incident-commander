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
- **One-command install scripts** - a `curl | bash`-style installer (or
  Ansible role) that sets up the watchdog as a systemd service, on a couple
  of common targets (Ubuntu/Debian on a plain VM, DigitalOcean, Hetzner).
- **Search box in the offline dashboard** - `monitor/dashboard.py` already
  renders a recent-incidents table; wire a small client-side filter (or a
  link that shells out to `monitor/incident_db.py --search`) so you don't
  need a separate terminal to answer "have we seen this before?".
- **Notifier delivery retry/backoff** - Discord/Slack/PagerDuty calls in
  `monitor/notifier.py` currently fire once; a transient network blip during
  a real incident shouldn't mean the page never goes out. A couple of
  retries with backoff, still stdlib-only.
- **Auto-tune `consecutive_breaches_required`** - if the watchdog is
  auto-remediating the same category over and over with no real incident
  (flapping), that's a signal the sensitivity is off; surface it instead of
  silently keeping the noisy default.
- **Slash-command / webhook query into `incident_db.py`** - let a Slack or
  Discord bot answer "have we seen X before?" by querying the SQLite index
  directly, instead of requiring shell access to the host.

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
