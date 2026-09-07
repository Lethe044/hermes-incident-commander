#!/usr/bin/env bash
#
# Hermes Incident Commander - watchdog systemd installer
# ===========================================================
# Installs monitor/watchdog.py as a systemd service so it starts on boot
# and restarts automatically if it crashes.
#
# Safe by design:
#   - Dry run by default. Nothing is written, and no system command that
#     changes state is executed, until you pass --yes.
#   - Idempotent: re-running with --yes overwrites the existing unit/env
#     file with the same content rather than duplicating anything.
#   - Your ANTHROPIC_API_KEY (and optional webhook/PagerDuty secrets) are
#     written to a root-only (chmod 600) EnvironmentFile, never embedded
#     directly in the unit file - the unit file itself is often
#     world-readable and shows up verbatim in `systemctl cat` / `ps aux`.
#   - Refuses to proceed with --yes if systemd isn't actually running as
#     PID 1 (common inside containers) instead of failing halfway through.
#
# Usage:
#   ./scripts/install-watchdog.sh                        # dry run - shows exactly what would happen
#   sudo ./scripts/install-watchdog.sh --yes              # actually install (needs root)
#   sudo ./scripts/install-watchdog.sh --yes --auto-remediate --adaptive-thresholds
#   sudo ./scripts/install-watchdog.sh --uninstall --yes   # stop and remove the service
#
# Anything you pass besides --yes/--uninstall/--help is forwarded verbatim
# to `python3 -m monitor.watchdog` (e.g. --cpu-threshold, --metrics-port).
#
# Advanced/testing overrides (not needed for normal use):
#   HERMES_WATCHDOG_UNIT_PATH, HERMES_WATCHDOG_ENV_DIR - relocate where the
#   unit file / env file are written, e.g. to point at a scratch directory
#   while trying the script out before trusting it with real root paths.

set -euo pipefail

SERVICE_NAME="hermes-watchdog"
UNIT_PATH="${HERMES_WATCHDOG_UNIT_PATH:-/etc/systemd/system/${SERVICE_NAME}.service}"
ENV_DIR="${HERMES_WATCHDOG_ENV_DIR:-/etc/hermes-incident-commander}"
ENV_FILE="${ENV_DIR}/watchdog.env"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_AS_HOME="${HOME:-/root}"

DRY_RUN=1
UNINSTALL=0
WATCHDOG_ARGS=()

for arg in "$@"; do
  case "$arg" in
    --yes) DRY_RUN=0 ;;
    --uninstall) UNINSTALL=1 ;;
    --help|-h)
      grep '^#' "$0" | sed -n '2,40p' | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) WATCHDOG_ARGS+=("$arg") ;;
  esac
done

log() { printf '%s\n' "$*"; }
plan() { printf '  [PLAN] %s\n' "$*"; }

require_root_for_real_run() {
  if [ "$DRY_RUN" -eq 0 ] && [ "$(id -u)" -ne 0 ]; then
    echo "error: --yes requires root (this installs a system-wide systemd service)." >&2
    echo "       Try: sudo $0 --yes" >&2
    exit 1
  fi
}

check_systemd() {
  if ! command -v systemctl >/dev/null 2>&1; then
    echo "error: systemctl not found. This installer targets systemd-based Linux distros only." >&2
    echo "       You can still run the watchdog directly: python3 -m monitor.watchdog" >&2
    exit 1
  fi
  if [ "$DRY_RUN" -eq 0 ]; then
    state="$(systemctl is-system-running 2>/dev/null || true)"
    if [ -z "$state" ]; then
      echo "error: couldn't reach systemd (is it actually running as PID 1 on this host?)." >&2
      echo "       This is common inside containers/WSL - run the watchdog directly instead:" >&2
      echo "       python3 -m monitor.watchdog ${WATCHDOG_ARGS[*]:-}" >&2
      exit 1
    fi
    # "degraded" (some unrelated unit failed) is fine to proceed on; only a
    # genuinely unreachable bus (empty $state above) is treated as fatal.
  fi
}

if [ "$UNINSTALL" -eq 1 ]; then
  require_root_for_real_run
  check_systemd
  log "Uninstalling ${SERVICE_NAME}..."
  if [ "$DRY_RUN" -eq 1 ]; then
    plan "systemctl disable --now ${SERVICE_NAME}"
    plan "rm -f '${UNIT_PATH}'"
    plan "rm -f '${ENV_FILE}'"
    plan "systemctl daemon-reload"
    log ""
    log "Dry run - nothing was changed. Re-run with --yes to actually uninstall."
  else
    systemctl disable --now "${SERVICE_NAME}" 2>/dev/null || true
    rm -f "${UNIT_PATH}"
    rm -f "${ENV_FILE}"
    systemctl daemon-reload
    log "Done."
  fi
  exit 0
fi

check_systemd
require_root_for_real_run

if [ -z "${ANTHROPIC_API_KEY:-}" ] && [ "$DRY_RUN" -eq 0 ]; then
  echo "error: ANTHROPIC_API_KEY is not set in your environment. Export it first:" >&2
  echo "       export ANTHROPIC_API_KEY=sk-ant-..." >&2
  exit 1
fi

PYTHON_BIN="$(command -v python3 || true)"
if [ -z "$PYTHON_BIN" ]; then
  echo "error: python3 not found on PATH." >&2
  exit 1
fi

log "Hermes Incident Commander - watchdog systemd installer"
log "  repo:        ${REPO_DIR}"
log "  python:      ${PYTHON_BIN}"
log "  service:     ${SERVICE_NAME}  (${UNIT_PATH})"
log "  env file:    ${ENV_FILE}  (mode 600, root-only)"
log "  extra args:  ${WATCHDOG_ARGS[*]:-(none)}"
if [ "$DRY_RUN" -eq 1 ]; then
  log "  mode:        DRY RUN - nothing will be written. Pass --yes to actually install."
fi
log ""

ENV_CONTENT="ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY:-REPLACE_ME}"
[ -n "${DISCORD_WEBHOOK_URL:-}" ] && ENV_CONTENT+=$'\n'"DISCORD_WEBHOOK_URL=${DISCORD_WEBHOOK_URL}"
[ -n "${SLACK_WEBHOOK_URL:-}" ] && ENV_CONTENT+=$'\n'"SLACK_WEBHOOK_URL=${SLACK_WEBHOOK_URL}"
[ -n "${PAGERDUTY_ROUTING_KEY:-}" ] && ENV_CONTENT+=$'\n'"PAGERDUTY_ROUTING_KEY=${PAGERDUTY_ROUTING_KEY}"

UNIT_CONTENT="[Unit]
Description=Hermes Incident Commander - standalone watchdog
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
EnvironmentFile=${ENV_FILE}
WorkingDirectory=${REPO_DIR}
ExecStart=${PYTHON_BIN} -m monitor.watchdog ${WATCHDOG_ARGS[*]:-}
Restart=on-failure
RestartSec=10
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=${RUN_AS_HOME}/.hermes

[Install]
WantedBy=multi-user.target
"

if [ "$DRY_RUN" -eq 1 ]; then
  plan "mkdir -p '${ENV_DIR}' && chmod 700 '${ENV_DIR}'"
  plan "write ${ENV_FILE} (chmod 600) with ANTHROPIC_API_KEY and any webhook/PagerDuty vars currently in your shell"
  plan "write ${UNIT_PATH} with the following content:"
  echo "----------------------------------------------------------------"
  printf '%s\n' "$UNIT_CONTENT"
  echo "----------------------------------------------------------------"
  plan "systemctl daemon-reload"
  plan "systemctl enable --now ${SERVICE_NAME}"
  log ""
  log "Dry run complete. Nothing was changed. Re-run with --yes to actually install."
  log "Note: --auto-remediate is NOT passed unless you add it explicitly - see SAFETY.md"
  log "before enabling it on a real server."
else
  mkdir -p "${ENV_DIR}"
  chmod 700 "${ENV_DIR}"
  printf '%s\n' "$ENV_CONTENT" > "${ENV_FILE}"
  chmod 600 "${ENV_FILE}"
  log "+ wrote ${ENV_FILE}"

  printf '%s\n' "$UNIT_CONTENT" > "${UNIT_PATH}"
  log "+ wrote ${UNIT_PATH}"

  systemctl daemon-reload
  systemctl enable --now "${SERVICE_NAME}"
  log ""
  log "Installed and started. Useful commands:"
  log "  systemctl status ${SERVICE_NAME}"
  log "  journalctl -u ${SERVICE_NAME} -f"
  log "  sudo ./scripts/install-watchdog.sh --uninstall --yes   # to remove it later"
fi
