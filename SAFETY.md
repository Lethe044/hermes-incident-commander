# Safety & Threat Model

Hermes Incident Commander can run in two very different modes, with very
different risk profiles. Read this before pointing anything at a real
machine you care about.

## 1. Demo mode (`demo/demo_incident.py`) and RL environment

These give the model **full, unrestricted terminal access** (`terminal`,
`read_file`, `write_file` tools) inside whatever environment you run them in.
This is intentional - it's how the agent is trained and evaluated to
diagnose and fix arbitrary incidents.

**Only run these in a disposable sandbox, VM, or container.** Never point
`demo_incident.py` or the Atropos training environment at a production host,
your laptop's real filesystem, or anything you can't afford to lose. The
built-in scenarios only touch `/tmp` and best-effort-installed packages, but
the *agent's own remediation attempts* are not sandboxed by this project -
that's the RL environment's job (`terminal_backend: docker` in
`environments/incident_config.yaml`).

## 2. Standalone watchdog (`monitor/watchdog.py`)

This is designed to be safe to run on a real host, including in
`--auto-remediate` mode. It achieves that with three deliberate design
constraints:

1. **The model never gets shell access.** `monitor/watchdog.py` collects
   metrics itself (via `psutil` and `systemctl is-failed`, both read-only)
   and sends only numbers to Claude. Claude's response is parsed as JSON -
   there is no code path where model output becomes a shell command.

2. **Remediation is allow-listed, not model-directed.** Even when
   `auto_remediate: true`, `safe_remediate()` only ever performs two kinds
   of action, and only for names/paths YOU configured in
   `remediation_allowlist`:
   - `systemctl restart <service>` for a service you explicitly listed
   - Deleting files older than `max_log_age_days` inside a directory you
     explicitly listed
   The model's `recommended_actions` are only used to *decide whether* one
   of your pre-approved actions applies - it cannot introduce a new command,
   service, or path.

3. **Auto-remediation defaults to off.** Out of the box, the watchdog only
   observes, diagnoses, writes reports, and sends alerts. You must pass
   `--auto-remediate` (or set `auto_remediate: true` in your config) to let
   it act.

### What could still go wrong

- A misconfigured allow-list (e.g. listing a critical service under
  `restart_services`) means the watchdog *will* restart it when triggered.
  Review your allow-list like you'd review a cron job that runs as root.
- `clean_log_dirs` deletes files matching `*.log*` older than the configured
  age - point it only at directories that exclusively contain logs.
- Notifications (Discord/Slack webhooks, PagerDuty events) include metrics
  and root-cause text in plaintext/custom_details. Don't put secrets in your
  incident descriptions.
- API keys, webhook URLs, and the PagerDuty routing key should be set via
  environment variables (`ANTHROPIC_API_KEY`, `DISCORD_WEBHOOK_URL`,
  `SLACK_WEBHOOK_URL`, `PAGERDUTY_ROUTING_KEY`) - never commit them. See
  `.env.example`.
- A PagerDuty routing key only lets `monitor/notifier.py` trigger/resolve
  events on the single service it's bound to via PagerDuty's Events API v2 -
  it cannot read or modify anything else in your PagerDuty account.
- `monitor/prometheus_exporter.py` (`--metrics-port`) is read-only and binds
  to `127.0.0.1` by default - it only ever serves numbers the watchdog has
  already collected and cannot be used to control the watchdog or reach
  anything else on the host. If you need a remote Prometheus server to
  scrape it, put it behind your own reverse proxy or firewall rule rather
  than binding it to `0.0.0.0`.
- `monitor/incident_db.py`'s SQLite database (`~/.hermes/incidents/incidents.db`)
  and `monitor/baseline.py`'s learned baseline
  (`~/.hermes/incidents/baseline.json`) are plain local files with the same
  sensitivity as `history.jsonl` itself - incident root-cause text, not
  secrets. Neither module opens a network port or executes anything; both
  only ever read/write files under `~/.hermes/incidents/`.
- `scripts/install-watchdog.sh` writes `ANTHROPIC_API_KEY` and any
  configured webhook/PagerDuty secrets to a dedicated `EnvironmentFile`
  (`/etc/hermes-incident-commander/watchdog.env`, `chmod 600`, owned by
  root) rather than embedding them in the systemd unit file - unit files
  are frequently world-readable and show up verbatim in `systemctl cat` /
  `ps aux`, which a plain `Environment=` directive would expose. The
  installed service also runs with `NoNewPrivileges=true` and
  `ProtectSystem=strict`. The script is dry-run by default; it only writes
  files or touches systemd once you pass `--yes`, and refuses to do either
  as a non-root user or when systemd isn't actually reachable.

## Reporting a security issue

Please open a private report via GitHub's "Report a vulnerability" flow on
this repository (Security tab) rather than a public issue.
