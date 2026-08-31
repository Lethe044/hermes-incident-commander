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
- **SQLite + full-text search over incident history** - replace/augment
  `history.jsonl` with a small SQLite database so "have we seen this
  before?" queries (mentioned in the Hermes-integrated `SKILL.md`) work for
  the standalone watchdog too, without a Hermes install.
- **Time-of-day-aware thresholds** - instead of one static
  `cpu_threshold`, learn a simple per-hour baseline from recent history so
  "95% CPU" doesn't page you for a nightly batch job that's always been
  fine.
- **One-command install scripts** - a `curl | bash`-style installer (or
  Ansible role) that sets up the watchdog as a systemd service, on a couple
  of common targets (Ubuntu/Debian on a plain VM, DigitalOcean, Hetzner).
- **More cloud-native scenarios** - e.g. an ECS task stuck in a
  deploy/rollback loop, or a Lambda cold-start/timeout spike - to round out
  `kubernetes`/`docker` with a couple of managed-platform equivalents.
- **Grafana dashboard JSON** to go with the new `/metrics` endpoint, so
  `--metrics-port` users don't have to build their own panels from scratch.

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
