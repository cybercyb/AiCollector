#!/usr/bin/env bash
# =============================================================================
# install.sh — AICollector installation script (idempotent & hardened)
#
# Target OS: Ubuntu Server 26.04 LTS
# Usage: sudo bash install.sh [--config /path/to/config.yaml] [--dry-run] [--force]
# =============================================================================

set -euo pipefail

# ── Constants ─────────────────────────────────────────────────────────────────────
APP_NAME="aicollector"
INSTALL_DIR="/opt/${APP_NAME}"
ETC_DIR="/etc/${APP_NAME}"
LIB_DIR="/var/lib/${APP_NAME}"
CACHE_DIR="/var/cache/${APP_NAME}"
LOG_DIR="/var/log/${APP_NAME}"
RUN_DIR="/run/${APP_NAME}"
CONFIG_FILE="${ETC_DIR}/config.yaml"
CRON_MARKER="# AICollector cron"
UNIT_DIR="/etc/systemd/system"
DEFAULT_SCHEDULE="0 */2 * * *"  # Every 2 hours
USER_NAME="${APP_NAME}"
GROUP_NAME="${APP_NAME}"

# ── Colour helpers ──────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# ── Logging functions ────────────────────────────────────────────────────────────
log_info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_fatal() { echo -e "${RED}[FATAL]${NC} $*" >&2; exit 1; }
log_ok()    { echo -e "${GREEN}[OK]${NC}   $*"; }
log_dry()   { echo -e "${CYAN}[DRY]${NC}   $*"; }
log_skip()  { echo -e "${YELLOW}[SKIP]${NC}  $*"; }
log_step()  {
    local n="$1"; local total="$2"
    echo ""
    echo -e "${BOLD}=== Step ${n}/${total}: ${*:3} ===${NC}"
}

# ── CLI parsing ────────────────────────────────────────────────────────────────
DRY_RUN="no"
FORCE="no"
CUSTOM_CONFIG=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --config)
            if [[ -z "${2:-}" ]]; then
                log_fatal "Error: --config requires a path argument"
            fi
            CUSTOM_CONFIG="$2"
            shift 2
            ;;
        --dry-run)  DRY_RUN="yes"; shift ;;
        --force)    FORCE="yes";  shift ;;
        --help|-h)
            echo "Usage: sudo bash $0 [--config /path/to/config.yaml] [--dry-run] [--force]"
            echo ""
            echo "Options:"
            echo "  --config <path>  Path to config.yaml"
            echo "  --dry-run        Show what would be done (no changes)"
            echo "  --force          Continue even if package installation warnings occur"
            echo "  --help           Show this help message"
            exit 0
            ;;
        *)
            log_fatal "Unknown option: $1"
            ;;
    esac
done

# ── Helpers & Pre-checks ────────────────────────────────────────────────────────
require_root() {
    if [[ $EUID -ne 0 ]]; then
        log_fatal "This script must be run as root (use sudo)"
    fi
}

check_disk_space() {
    # Check that at least 100MB are free in /var/lib (or root if not separated)
    local target_dir="/var/lib"
    mkdir -p "${target_dir}"
    local free_kb
    free_kb=$(df -P "${target_dir}" | awk 'NR==2 {print $4}')
    if [[ "${free_kb}" -lt 102400 ]]; then
        log_fatal "Insufficient disk space: less than 100MB free on ${target_dir}."
    fi
}

detect_auditd_active() {
    local config_path="$1"
    AUDITD_ACTIVE="yes"  # Default fallback

    if [[ ! -f "${config_path}" ]]; then
        log_warn "config.yaml not found at ${config_path} — assuming auditd collector is ACTIVE"
        return
    fi

    if command -v python3 >/dev/null 2>&1; then
        local result
        result=$(python3 -c "
import yaml, sys
try:
    with open('${config_path}') as f:
        config = yaml.safe_load(f) or {}
    collectors = config.get('collectors', {})
    enabled = collectors.get('enabled', None)
    disabled = collectors.get('disabled', None)
    print(repr(enabled))
    print(repr(disabled))
except Exception as e:
    print('ERROR:' + str(e), file=sys.stderr)
    sys.exit(1)
" 2>/dev/null || echo "ERROR")

        if [[ "${result}" == "ERROR" ]]; then
            log_warn "Could not parse config.yaml — assuming auditd collector is ACTIVE"
            return
        fi

        local enabled_str disabled_str
        enabled_str=$(echo "${result}" | head -1)
        disabled_str=$(echo "${result}" | tail -1)

        if [[ "${enabled_str}" == \[*\] ]] && [[ "${enabled_str}" != "[]" ]]; then
            if echo "${result}" | grep -q "'auditd'"; then
                AUDITD_ACTIVE="yes"
            else
                AUDITD_ACTIVE="no"
            fi
        else
            if [[ "${disabled_str}" == \[*\] ]] && echo "${disabled_str}" | grep -q "'auditd'"; then
                AUDITD_ACTIVE="no"
            else
                AUDITD_ACTIVE="yes"
            fi
        fi
    fi
}

# ── Step 1: Detect config.yaml ─────────────────────────────────────────────────
install_step1_config() {
    log_step 1 10 "Locating config.yaml"

    if [[ -n "${CUSTOM_CONFIG}" ]]; then
        if [[ ! -f "${CUSTOM_CONFIG}" ]]; then
            log_fatal "Custom config file not found: ${CUSTOM_CONFIG}"
        fi
        CONFIG_FILE_SOURCE="${CUSTOM_CONFIG}"
        log_info "Using custom config: ${CUSTOM_CONFIG}"
    elif [[ -f "./config.yaml" ]]; then
        CONFIG_FILE_SOURCE="./config.yaml"
        log_info "Found ./config.yaml (dev mode source)"
    elif [[ -f "${CONFIG_FILE}" ]]; then
        CONFIG_FILE_SOURCE="${CONFIG_FILE}"
        log_info "Found existing config in production path — preserving"
    else
        CONFIG_FILE_SOURCE=""
    fi

    if [[ -n "${CONFIG_FILE_SOURCE}" ]]; then
        detect_auditd_active "${CONFIG_FILE_SOURCE}"
        if [[ "${AUDITD_ACTIVE}" == "yes" ]]; then
            log_info "Collector 'auditd' is ACTIVE — auditd package installation required"
        else
            log_warn "Collector 'auditd' is DISABLED — skipping auditd installation"
        fi
    else
        AUDITD_ACTIVE="yes"
        log_info "No config found — using defaults (all active, including auditd)"
    fi
}

# ── Step 2: Install system dependencies ────────────────────────────────────────
install_step2_dependencies() {
    log_step 2 10 "Installing system dependencies"

    if [[ "${DRY_RUN}" == "yes" ]]; then
        log_dry "Would install: iproute2, smartmontools, openssl, cron, systemd, python3-yaml"
        return 0
    fi

    log_info "Updating packages cache..."
    apt-get update -qq || log_warn "apt-get update completed with warnings"

    local mandatory_pkgs=(
        "iproute2"
        "smartmontools"
        "openssl"
        "cron"
        "systemd"
        "python3"
        "python3-yaml"
    )

    local install_status=0
    for pkg in "${mandatory_pkgs[@]}"; do
        if dpkg -s "${pkg}" >/dev/null 2>&1; then
            log_skip "${pkg} already installed"
        else
            log_info "Installing: ${pkg}..."
            if apt-get install -y -qq "${pkg}"; then
                log_ok "Installed: ${pkg}"
            else
                log_warn "Failed to install package: ${pkg}"
                ((install_status++))
            fi
        fi
    done

    # Install auditd only if collector is enabled
    if [[ "${AUDITD_ACTIVE}" == "yes" ]]; then
        if dpkg -s "auditd" >/dev/null 2>&1; then
            log_skip "auditd already installed"
        else
            log_info "Installing: auditd..."
            if apt-get install -y -qq auditd; then
                log_ok "Installed: auditd"
            else
                log_warn "Failed to install auditd"
                ((install_status++))
            fi
        fi
    else
        log_skip "auditd (collector 'auditd' disabled in config)"
    fi

    if [[ "${install_status}" -gt 0 ]] && [[ "${FORCE}" != "yes" ]]; then
        log_fatal "Dependency installation failed. Use --force to proceed anyway."
    fi
}

# ── Step 3: Create system group & user ─────────────────────────────────────────
install_step3_user() {
    log_step 3 10 "Creating dedicated system user and group"

    if [[ "${DRY_RUN}" == "yes" ]]; then
        log_dry "Would verify/create system group: ${GROUP_NAME}"
        log_dry "Would verify/create system user: ${USER_NAME} belonging to group ${GROUP_NAME}"
        return 0
    fi

    # Create Group first
    if getent group "${GROUP_NAME}" >/dev/null; then
        log_skip "System group '${GROUP_NAME}' already exists"
    else
        groupadd --system "${GROUP_NAME}"
        log_ok "System group '${GROUP_NAME}' created successfully"
    fi

    # Create User
    if getent passwd "${USER_NAME}" >/dev/null; then
        log_skip "System user '${USER_NAME}' already exists"
    else
        useradd --system \
                --gid "${GROUP_NAME}" \
                --no-create-home \
                --shell /usr/sbin/nologin \
                --comment "AICollector daemon account" \
                "${USER_NAME}"
        log_ok "System user '${USER_NAME}' created successfully"
    fi
}

# ── Step 4: Create directory structure ──────────────────────────────────────────
install_step4_directories() {
    log_step 4 10 "Configuring directory layout"

    local dirs=(
        "${INSTALL_DIR}"
        "${ETC_DIR}"
        "${LIB_DIR}/knowledge"
        "${LIB_DIR}/history"
        "${LIB_DIR}/changes"
        "${LIB_DIR}/cache"
        "${CACHE_DIR}"
        "${LOG_DIR}"
        "${RUN_DIR}"
    )

    for dir in "${dirs[@]}"; do
        if [[ -d "${dir}" ]]; then
            log_skip "Directory already exists: ${dir}"
        else
            if [[ "${DRY_RUN}" == "yes" ]]; then
                log_dry "Would create directory: ${dir}"
            else
                mkdir -p "${dir}"
                log_ok "Created directory: ${dir}"
            fi
        fi
    done
}

# ── Step 5: Deploy application files ───────────────────────────────────────────
install_step5_deploy() {
    log_step 5 10 "Deploying application codebase to /opt"

    local script_dir
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

    if [[ "${DRY_RUN}" == "yes" ]]; then
        log_dry "Would deploy application code to ${INSTALL_DIR}"
        return 0
    fi

    local app_root="${script_dir}"
    if [[ ! -f "${script_dir}/collector.py" && -f "${script_dir}/../collector.py" ]]; then
        app_root="$(cd "${script_dir}/.." && pwd)"
    fi

    if [[ -f "${app_root}/collector.py" ]]; then
        # Exclude deployment, installation scripts & user configs
        rsync -a --delete \
              --exclude='install.sh' \
              --exclude='uninstall.sh' \
              --exclude='check_dependencies.sh' \
              --exclude='config.yaml' \
              --exclude='data/' \
              --exclude='logs/' \
              --exclude='cache/' \
              --exclude='__pycache__/' \
              "${app_root}/" "${INSTALL_DIR}/"
        log_ok "Application deployed to ${INSTALL_DIR}"
    else
        log_fatal "Could not find collector.py in ${app_root}. Check your source repository."
    fi
}

# ── Step 6: Deploy configuration ─────────────────────────────────────────────
install_step6_config() {
    log_step 6 10 "Deploying configuration file"

    if [[ -n "${CONFIG_FILE_SOURCE}" && -f "${CONFIG_FILE_SOURCE}" ]]; then
        if [[ "${CONFIG_FILE_SOURCE}" == "${CONFIG_FILE}" ]]; then
            log_skip "Configuration file is already in place at ${CONFIG_FILE}"
            return 0
        fi
        if [[ "${DRY_RUN}" == "yes" ]]; then
            log_dry "Would copy ${CONFIG_FILE_SOURCE} to ${CONFIG_FILE}"
            return 0
        fi
        cp "${CONFIG_FILE_SOURCE}" "${CONFIG_FILE}"
        log_ok "Configuration deployed to ${CONFIG_FILE}"
    else
        if [[ "${DRY_RUN}" == "yes" ]]; then
            log_dry "Would create default configuration file"
            return 0
        fi
        cat > "${CONFIG_FILE}" << 'DEFAULTCONFIG'
# AICollector configuration file (FHS mode)
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
DEFAULTCONFIG
        log_ok "Default configuration file generated at ${CONFIG_FILE}"
    fi
}

# ── Step 7: Set permissions ─────────────────────────────────────────────────────
install_step7_permissions() {
    log_step 7 10 "Applying secure FHS permissions"

    if [[ "${DRY_RUN}" == "yes" ]]; then
        log_dry "Would apply secure ownership (root:root for opt, aicollector:aicollector for var/etc)"
        return 0
    fi

    # Application binaries (read-only for daemon user)
    chown -R root:root "${INSTALL_DIR}"
    chmod -R 755 "${INSTALL_DIR}"
    chmod +x "${INSTALL_DIR}/collector.py"

    # Configuration (readable but not writable by daemon user)
    chown -R root:${GROUP_NAME} "${ETC_DIR}"
    chmod 750 "${ETC_DIR}"
    chmod 640 "${CONFIG_FILE}"

    # Variable & State Directories
    chown -R ${USER_NAME}:${GROUP_NAME} "${LIB_DIR}"
    chmod -R 750 "${LIB_DIR}"

    chown -R ${USER_NAME}:${GROUP_NAME} "${CACHE_DIR}"
    chmod 750 "${CACHE_DIR}"

    chown -R ${USER_NAME}:${GROUP_NAME} "${LOG_DIR}"
    chmod 750 "${LOG_DIR}"

    log_ok "Permissions successfully restricted"
}

# ── Step 8: Configure scheduler (Cron vs Systemd Timer) ───────────────────────
install_step8_scheduler() {
    log_step 8 10 "Configuring pipeline scheduler"

    local schedule="${DEFAULT_SCHEDULE}"
    local use_timer="false"

    if [[ -f "${CONFIG_FILE}" ]]; then
        local configured_schedule configured_timer
        configured_schedule=$(python3 -c "import yaml; c=yaml.safe_load(open('${CONFIG_FILE}')) or {}; print(c.get('scheduler', {}).get('frequency_cron', '0 */2 * * *'))" 2>/dev/null || echo "0 */2 * * *")
        configured_timer=$(python3 -c "import yaml; c=yaml.safe_load(open('${CONFIG_FILE}')) or {}; print(str(c.get('scheduler', {}).get('use_systemd_timer', False)).lower())" 2>/dev/null || echo "false")
        
        [[ -n "${configured_schedule}" ]] && schedule="${configured_schedule}"
        [[ -n "${configured_timer}" ]] && use_timer="${configured_timer}"
    fi

    # Remove any existing Cron jobs if Timer is preferred (or vice versa) to prevent double schedules
    if [[ "${DRY_RUN}" == "yes" ]]; then
        log_dry "Would clean existing schedule conflicts"
        if [[ "${use_timer}" == "true" ]]; then
            log_dry "Would deploy Systemd Timer running: ${schedule}"
        else
            log_dry "Would deploy Cron schedule: ${schedule}"
        fi
        return 0
    fi

    # Clean legacy cron
    local current_cron
    current_cron=$(crontab -l 2>/dev/null || true)
    if echo "${current_cron}" | grep -q "${CRON_MARKER}"; then
        echo "${current_cron}" | grep -v "${CRON_MARKER}" | crontab - || true
        log_info "Cleaned legacy cron schedules"
    fi

    # Clean legacy systemd files
    if [[ -f "${UNIT_DIR}/${APP_NAME}.timer" ]]; then
        systemctl disable --now "${APP_NAME}.timer" 2>/dev/null || true
        rm -f "${UNIT_DIR}/${APP_NAME}.service" "${UNIT_DIR}/${APP_NAME}.timer"
        systemctl daemon-reload
        log_info "Cleaned legacy systemd timers"
    fi

    if [[ "${use_timer}" == "true" ]]; then
        # ── Systemd Timer Schedule ───────────────────────────────────────
        cat > "${UNIT_DIR}/${APP_NAME}.service" <<EOF
[Unit]
Description=AICollector Server Knowledge Collector Pipeline
After=network.target

[Service]
Type=oneshot
User=${USER_NAME}
Group=${GROUP_NAME}
ExecStart=${INSTALL_DIR}/collector.py run
StandardOutput=journal
StandardError=journal
EOF

        # Convert simple daily/hourly common cron intervals to systemd-style
        local calendar="*-*-* 00,02,04,06,08,10,12,14,16,18,20,22:00:00" # Default 2 hours
        if [[ "${schedule}" == "0 * * * *" ]]; then
            calendar="hourly"
        elif [[ "${schedule}" == "0 0 * * *" ]]; then
            calendar="daily"
        fi

        cat > "${UNIT_DIR}/${APP_NAME}.timer" <<EOF
[Unit]
Description=Run AICollector Pipeline periodically

[Timer]
OnCalendar=${calendar}
AccuracySec=10m
Persistent=true

[Install]
WantedBy=timers.target
EOF

        systemctl daemon-reload
        systemctl enable --now "${APP_NAME}.timer"
        log_ok "Systemd Timer successfully configured & enabled (${calendar})"
    else
        # ── Classic Cron Schedule ───────────────────────────────────────
        local cron_entry="${schedule} ${INSTALL_DIR}/collector.py run ${CRON_MARKER}"
        (crontab -l 2>/dev/null || true; echo "${cron_entry}") | crontab -
        log_ok "Cron scheduler configured successfully (${schedule})"
    fi
}

# ── Step 9: Configure tmpfiles.d ──────────────────────────────────────────────
install_step9_tmpfiles() {
    log_step 9 10 "Setting up runtime volatile directory (tmpfiles.d)"

    if [[ "${DRY_RUN}" == "yes" ]]; then
        log_dry "Would write configuration /etc/tmpfiles.d/${APP_NAME}.conf"
        return 0
    fi

    cat > "/etc/tmpfiles.d/${APP_NAME}.conf" << EOF
# Type Path               Mode UID           GID           Age Argument
d     ${RUN_DIR}           0755 ${USER_NAME} ${GROUP_NAME} - -
EOF
    systemd-tmpfiles --create "/etc/tmpfiles.d/${APP_NAME}.conf" 2>/dev/null || log_warn "Systemd-tmpfiles failed to run immediately (will apply upon reboot)"
    log_ok "tmpfiles.d configuration set up successfully"
}

# ── Step 10: Configure logrotate ──────────────────────────────────────────────
install_step10_logrotate() {
    log_step 10 10 "Configuring log rotation"

    if [[ "${DRY_RUN}" == "yes" ]]; then
        log_dry "Would create /etc/logrotate.d/${APP_NAME}"
        return 0
    fi

    cat > "/etc/logrotate.d/${APP_NAME}" << EOF
${LOG_DIR}/*.log {
    daily
    rotate 14
    missingok
    notifempty
    compress
    delaycompress
    sharedscripts
    create 0640 ${USER_NAME} ${GROUP_NAME}
}
EOF
    log_ok "Logrotate policy configured at /etc/logrotate.d/${APP_NAME}"
}

# ── Main Entrypoint ──────────────────────────────────────────────────────────────
main() {
    echo ""
    echo -e "${BOLD}═══════════════════════════════════════════════════════════════════${NC}"
    echo -e "${BOLD}  AICollector — Hardened Installer [Ubuntu 26.04 LTS]${NC}"
    echo -e "${BOLD}═══════════════════════════════════════════════════════════════════${NC}"
    echo ""

    require_root
    check_disk_space

    if [[ "${DRY_RUN}" == "yes" ]]; then
        echo -e "${CYAN}MODE: DRY-RUN active (simulation mode)${NC}"
        echo ""
    fi

    if [[ "${DRY_RUN}" != "yes" ]]; then
        echo -n "Proceed with production installation? [y/N]: "
        read -r response < /dev/tty
        if [[ ! "${response}" =~ ^[Yy]$ ]]; then
            log_fatal "Installation cancelled by operator."
        fi
    fi

    install_step1_config
    install_step2_dependencies
    install_step3_user
    install_step4_directories
    install_step5_deploy
    install_step6_config
    install_step7_permissions
    install_step8_scheduler
    install_step9_tmpfiles
    install_step10_logrotate

    echo ""
    echo -e "${BOLD}═══════════════════════════════════════════════════════════════════${NC}"
    if [[ "${DRY_RUN}" == "yes" ]]; then
        echo -e "${CYAN}Hardened installation dry-run complete without error.${NC}"
    else
        echo -e "${GREEN}AICollector has been successfully deployed and restricted!${NC}"
        echo ""
        echo "Operational commands:"
        echo "  - Manual Execute:   sudo ${INSTALL_DIR}/collector.py run"
        echo "  - Check Status:     sudo ${INSTALL_DIR}/collector.py check"
        echo "  - View Logs:        tail -f ${LOG_DIR}/aicollector.log"
    fi
    echo -e "${BOLD}═══════════════════════════════════════════════════════════════════${NC}"
    echo ""
}

main "$@"
