#!/usr/bin/env bash
# =============================================================================
# install.sh — AICollector idempotent installation script
#
# Usage: sudo bash install.sh [--uninstall] [--user USER] [--cron "EXPR"]
#
# Options:
#   --uninstall          Remove all AICollector files and stop services
#   --user USER          Operating user (default: aicollector)
#   --cron "EXPR"        Cron expression (default: "0 */2 * * *")
#   --systemd-timer      Install systemd timer instead of cron (default: no)
#   --force              Re-run installation even if already installed
#
# Idempotence: Running this script multiple times is safe.
# =============================================================================

set -euo pipefail

# ── Constants ─────────────────────────────────────────────────────────────────
APP_NAME="aicollector"
INSTALL_DIR="/opt/${APP_NAME}"
ETC_DIR="/etc/${APP_NAME}"
LIB_DIR="/var/lib/${APP_NAME}"
CACHE_DIR="/var/cache/${APP_NAME}"
RUN_DIR="/run/${APP_NAME}"
LOG_DIR="/var/log/${APP_NAME}"
LOCKFILE="${RUN_DIR}/${APP_NAME}.lock"
USER_NAME="${APP_NAME}"
DEFAULT_CRON="0 */2 * * *"
CRON_EXPR="${DEFAULT_CRON}"
USE_SYSTEMD_TIMER="no"
UNIT_DIR="/etc/systemd/system"

# Colour helpers
RED='\\033[0;31m'; GREEN='\\033[0;32m'; YELLOW='\\033[0;33m'
BOLD='\\033[1m'; NC='\\033[0m'  # No Colour

log_info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_fatal() { echo -e "${RED}[FATAL]${NC} $*"; exit 1; }
log_ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }

# ── CLI parsing ────────────────────────────────────────────────────────────────
UNINSTALL="no"
FORCE="no"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --uninstall)      UNINSTALL="yes"; shift ;;
        --user)            USER_NAME="$2"; shift 2 ;;
        --cron)            CRON_EXPR="$2"; shift 2 ;;
        --systemd-timer)   USE_SYSTEMD_TIMER="yes"; shift ;;
        --force)           FORCE="yes"; shift ;;
        *)                 echo "Unknown option: $1"; exit 1 ;;
    esac
done

# ── Helpers ────────────────────────────────────────────────────────────────────
is_installed() { [[ -f "${INSTALL_DIR}/collector.py" ]]; }

require_root() {
    if [[ $EUID -ne 0 ]]; then
        log_fatal "This script must be run as root (use sudo)"
    fi
}

create_user() {
    if ! id "${USER_NAME}" &>/dev/null; then
        log_info "Creating system user '${USER_NAME}'..."
        useradd --system --no-create-home --shell /usr/sbin/nologin "${USER_NAME}" \
            || log_warn "Could not create user '${USER_NAME}' (may already exist)"
    else
        log_ok "User '${USER_NAME}' already exists"
    fi
}

create_directories() {
    log_info "Creating FHS directory structure..."
    mkdir -p "${INSTALL_DIR}"
    mkdir -p "${ETC_DIR}"
    mkdir -p "${LIB_DIR}"/{knowledge,history,changes}
    mkdir -p "${CACHE_DIR}/cache"
    mkdir -p "${RUN_DIR}"
    mkdir -p "${LOG_DIR}"
    chown -R "${USER_NAME}:${USER_NAME}" "${LIB_DIR}" "${CACHE_DIR}" "${RUN_DIR}" "${LOG_DIR}"
    chmod 750 "${LIB_DIR}" "${CACHE_DIR}" "${LOG_DIR}"
    chmod 755 "${RUN_DIR}"
    chmod 755 "${INSTALL_DIR}"
    chmod 750 "${ETC_DIR}"
    log_ok "Directory structure created"
}

copy_files() {
    log_info "Copying application files to ${INSTALL_DIR}..."
    # Copy everything except install.sh from the current directory
    rsync -a --exclude='install.sh' --exclude='*.pyc' --exclude='__pycache__' \
        --exclude='.git' --exclude='*.egg-info' \
        "$(cd "$(dirname "$0")" && pwd)/" "${INSTALL_DIR}/"
    chown -R root:root "${INSTALL_DIR}"
    chmod 755 "${INSTALL_DIR}"/*.py 2>/dev/null || true
    chmod 644 "${INSTALL_DIR}"/*.toml 2>/dev/null || true
    chmod 644 "${INSTALL_DIR}"/VERSION 2>/dev/null || true
    log_ok "Application files copied"
}

install_config() {
    if [[ ! -f "${ETC_DIR}/config.yaml" ]]; then
        log_info "Installing default config to ${ETC_DIR}/config.yaml..."
        if [[ -f "${INSTALL_DIR}/config.yaml" ]]; then
            cp "${INSTALL_DIR}/config.yaml" "${ETC_DIR}/config.yaml"
        else
            cat > "${ETC_DIR}/config.yaml" << 'CONFIG_EOF'
# AICollector configuration — /etc/aicollector/config.yaml
server_uuid: null
logging_level: INFO

retention:
  history_versions: 50
  changes_entries: 200
  logs_days: 30

scheduler:
  frequency_cron: "0 */2 * * *"
  use_systemd_timer: false

collectors:
  enabled: []
  disabled: []
  timeout_seconds: 30
  parallel: false
  root_required_behavior: skip

security:
  allowed_commands: []
  exclude_paths: []
  redact_patterns: []

paths:
  base_dir: /var/lib/aicollector
  config_dir: /etc/aicollector
  cache_dir: /var/cache/aicollector
  log_dir: /var/log/aicollector
  lockfile_path: /run/aicollector/aicollector.lock
  knowledge_subdir: knowledge
  history_subdir: history
  changes_subdir: changes
  cache_subdir: cache
CONFIG_EOF
        fi
        chown root:"${USER_NAME}" "${ETC_DIR}/config.yaml"
        chmod 640 "${ETC_DIR}/config.yaml"
    else
        log_ok "Config already exists at ${ETC_DIR}/config.yaml (preserved)"
    fi
}

install_tmpfiles_d() {
    log_info "Installing tmpfiles.d for volatile directories..."
    cat > /etc/tmpfiles.d/${APP_NAME}.conf << 'TMP_EOF'
# Type  Path                     Mode  UID           GID      Age  Argument
d      /run/aicollector          0755  aicollector   aicollector  -   -
TMP_EOF
    systemd-tmpfiles --create /etc/tmpfiles.d/${APP_NAME}.conf 2>/dev/null \
        || log_warn "Could not apply tmpfiles.d (systemd may not be running)"
    log_ok "tmpfiles.d installed"
}

install_cron() {
    log_info "Installing cron job (${CRON_EXPR})..."
    CRON_MARKER="# AICollector cron"
    # Remove any existing entry
    (crontab -l 2>/dev/null | grep -v "${CRON_MARKER}" || true) | crontab -
    # Add new entry
    (crontab -l 2>/dev/null; echo "${CRON_EXPR} ${CRON_MARKER} python3 ${INSTALL_DIR}/collector.py --run >> /var/log/aicollector/cron.log 2>&1") \
        | crontab - 2>/dev/null || log_warn "Could not install cron entry"
    log_ok "Cron installed"
}

uninstall_cron() {
    CRON_MARKER="# AICollector cron"
    (crontab -l 2>/dev/null | grep -v "${CRON_MARKER}" || true) | crontab -
    log_info "Cron entry removed"
}

install_systemd_timer() {
    log_info "Installing systemd timer..."
    cat > "${UNIT_DIR}/${APP_NAME}.service" << SERVICE_EOF
[Unit]
Description=AICollector — server knowledge collector
After=network.target

[Service]
Type=oneshot
User=${USER_NAME}
ExecStart=/usr/bin/python3 ${INSTALL_DIR}/collector.py run
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
SERVICE_EOF

    cat > "${UNIT_DIR}/${APP_NAME}.timer" << TIMER_EOF
[Unit]
Description=AICollector periodic collection timer

[Timer]
OnCalendar=${CRON_EXPR}
Persistent=true
RandomizedDelaySec=300

[Install]
WantedBy=timers.target
TIMER_EOF

    systemctl daemon-reload 2>/dev/null \
        || log_warn "Could not reload systemd (systemd may not be running)"
    systemctl enable --now "${APP_NAME}.timer" 2>/dev/null \
        || log_warn "Could not enable timer (systemd may not be running)"
    log_ok "Systemd timer installed and enabled"
}

uninstall_systemd() {
    systemctl stop "${APP_NAME}.timer" 2>/dev/null \
        || log_warn "Timer not running"
    systemctl disable "${APP_NAME}.timer" 2>/dev/null \
        || log_warn "Timer not enabled"
    rm -f "${UNIT_DIR}/${APP_NAME}.service" "${UNIT_DIR}/${APP_NAME}.timer"
    log_info "Systemd unit files removed"
}

# ── Main ──────────────────────────────────────────────────────────────────────

if [[ "${UNINSTALL}" == "yes" ]]; then
    require_root
    log_info "Uninstalling AICollector..."
    uninstall_cron
    uninstall_systemd
    rm -rf "${INSTALL_DIR}" "${LIB_DIR}" "${CACHE_DIR}" "${LOG_DIR}"
    rm -f /etc/tmpfiles.d/${APP_NAME}.conf
    userdel "${USER_NAME}" 2>/dev/null || true
    log_ok "AICollector uninstalled"
    exit 0
fi

require_root

if is_installed && [[ "${FORCE}" != "yes" ]]; then
    log_info "AICollector is already installed. Use --force to re-run."
    exit 0
fi

log_info "=== AICollector installation ==="
log_info "Version : $(cat "${INSTALL_DIR}/VERSION" 2>/dev/null || echo 'unknown')"
log_info "User    : ${USER_NAME}"
log_info "Mode    : ${USE_SYSTEMD_TIMER:+systemd timer}${USE_SYSTEMD_TIMER:=cron} — ${CRON_EXPR}"

create_user
create_directories
copy_files
install_config
install_tmpfiles_d

if [[ "${USE_SYSTEMD_TIMER}" == "yes" ]]; then
    uninstall_cron
    install_systemd_timer
else
    uninstall_systemd
    install_cron
fi

log_info ""
log_info "Installation complete."
log_info "  Config : ${ETC_DIR}/config.yaml"
log_info "  Data   : ${LIB_DIR}/"
log_info "  Logs   : ${LOG_DIR}/"
log_info ""
log_info "Run manually:  python3 ${INSTALL_DIR}/collector.py run"
log_info "Check config:  python3 ${INSTALL_DIR}/collector.py check"
