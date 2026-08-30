# Contributing to Hermes Incident Commander

Contributions are welcome — new incident scenarios, notifier integrations,
dashboard improvements, or bug fixes.

## Getting set up

```bash
git clone https://github.com/Lethe044/hermes-incident-commander.git
cd hermes-incident-commander
pip install -e ".[dev]"
```

## Running checks locally

```bash
# Fast — no API key, no dependencies beyond requirements.txt
python environments/incident_env.py --smoke-test

# Full suite
pytest tests/ -v

# Lint
ruff check .
```

## Adding a new incident scenario

1. Add an `IncidentScenario` entry to `INCIDENT_SCENARIOS` in
   `environments/incident_env.py`. Every scenario needs:
   - a unique, lowercase, hyphenated `id`
   - a valid `severity` (`P0`–`P3`)
   - `setup_commands` that only ever touch `/tmp` or best-effort-installed
     packages, wrapped so they degrade gracefully (`|| true`) on systems
     where the tooling isn't available
   - `success_criteria` (and optionally `partial_criteria`) as bash one-liners
     that exit 0 when — and only when — the incident is actually resolved
2. If it's a common enough scenario, add a matching entry to
   `DEMO_SCENARIOS` in `demo/demo_incident.py` so it's runnable standalone.
3. Add/extend a test in `tests/test_incident_env.py` if you're introducing a
   new category.
4. Run `pytest tests/ -v` — `test_setup_commands_are_valid_bash_syntax` and
   `test_success_criteria_are_valid_bash_syntax` will catch syntax mistakes.

## Adding a new notifier / integration

See `monitor/notifier.py` — add a new `send_*` method or a new webhook type,
keep it dependency-free (stdlib `urllib` only) if possible, and add a test
in `tests/test_monitor.py` that mocks the HTTP call (no real network access
in tests).

## Code style

- Python 3.10+, type hints where practical.
- Keep new runtime dependencies to a minimum — this project intentionally
  stays lightweight so the standalone tools (`monitor/`) work with just
  `pip install psutil anthropic pyyaml`.
- Run `ruff check .` before opening a PR.

## Pull requests

Please fill out the PR template checklist. Small, focused PRs are easier to
review and merge quickly.
