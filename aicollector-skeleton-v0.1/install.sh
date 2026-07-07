#!/usr/bin/env bash
# =============================================================================
# install.sh — AICollector installation script (idempotent)
#
# Usage: sudo bash install.sh [--config /path/to/config.yaml] [--dry-run] [--force]
#
# Options:
#   --config <path>  Path to config.yaml (default: ./config.yaml or /etc/aicollector/config.yaml)
#   --dry-run        Show what would be done without making changes
#   --force         Continue even if errors occur
#   --help          Show this help message
#
# Behaviour:
#   • Creates system user 'aicollector'
#   • Deploys application files to /opt/aicollector/
#   • Installs dependencies (system packages + Python optional deps)
#   • Installs auditd ONLY if the 'auditd' collector is active in config.yaml
#   • Copies configuration file
#   • Sets up cron or systemd timer scheduling
#   • Configures logrotate
#   • Sets correct permissions
#
# Idempotence: Safe to run multiple times. Already-configured items are skipped.
# Requires: Root privileges (sudo)
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
CONFIG_FILE_SOURCE=""       # Path to the config.yaml source (set via --config)
CONFIG_FILE="${ETC_DIR}/config.yaml"
LOCKFILE="${RUN_DIR}/${APP_NAME}.lock"
CRON_MARKER="# AICollector cron"
UNIT_DIR="/etc/systemd/system"
DEFAULT_SCHEDULE="0 */2 * * *"  # Every 2 hours
USER_NAME="${APP_NAME}"

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
log_fatal() { echo -e "${RED}[FATAL]${NC} $*"; exit 1; }
log_ok()    { echo -e "${GREEN}[OK]${NC}   $*"; }
log_dry()   { echo -e "${CYAN}[DRY]${NC}   $*"; }
log_skip()  { echo -e "${YELLOW}[SKIP]${NC}  $*"; }
log_step()  {
    local n="$1"; local total="$2"
    echo ""
    echo -e "${BOLD}=== Step ${n}/${total}: ${*} ===${NC}"
}

# ── CLI parsing ────────────────────────────────────────────────────────────────
DRY_RUN="no"
FORCE="no"
CUSTOM_CONFIG=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --config)
            if [[ -z "${2:-}" ]]; then
                echo "Error: --config requires a path argument"
                exit 1
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
            echo "  --config <path>  Path to config.yaml (default: first ./config.yaml, then /etc/aicollector/config.yaml)"
            echo "  --dry-run        Show what would be done (no changes)"
            echo "  --force          Continue even if errors occur"
            echo "  --help           Show this help message"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

# ── Helpers ────────────────────────────────────────────────────────────────────
require_root() {
    if [[ $EUID -ne 0 ]]; then
        log_fatal "This script must be run as root (use sudo)"
    fi
}

# Detect whether the 'auditd' collector is active in config.yaml.
# Logic:
#   1. Parse collectors.enabled (list of explicitly activated collectors)
#      - If NON-EMPTY → whitelist mode: auditd active only if explicitly listed
#   2. Parse collectors.disabled (list of deactivated collectors)
#      - If auditd appears in disabled → NOT active
#      - Otherwise → ACTIVE (default: all collectors are active)
detect_auditd_active() {
    local config_path="$1"
    AUDITD_ACTIVE="yes"  # Default: all collectors active

    if [[ ! -f "${config_path}" ]]; then
        log_warn "config.yaml not found at ${config_path} — assuming auditd collector is ACTIVE"
        return
    fi

    # Use python3 to reliably parse YAML
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
" 2>/dev/null || echo "ERROR|NONE")

        if [[ "${result}" == ERROR:* ]]; then
            log_warn "Could not parse config.yaml — assuming auditd collector is ACTIVE"
            return
        fi

        local enabled_str disabled_str
        enabled_str=$(echo "${result}" | head -1)
        disabled_str=$(echo "${result}" | tail -1)

        # If enabled list is non-empty → whitelist mode
        if [[ "${enabled_str}" == \[*\] ]] && [[ "${enabled_str}" != "[]" ]]; then
            if echo "${result}" | grep -q "'auditd'"; then
                AUDITD_ACTIVE="yes"
            else
                AUDITD_ACTIVE="no"
            fi
        else
            # Blacklist/default mode: check disabled list
            if [[ "${disabled_str}" == \[*\] ]] && echo "${disabled_str}" | grep -q "'auditd'"; then
                AUDITD_ACTIVE="no"
            else
                AUDITD_ACTIVE="yes"
            fi
        fi
    else
        log_warn "python3 not available for config parsing — assuming auditd collector is ACTIVE"
    fi
}

# ── Step 1: Detect config.yaml ─────────────────────────────────────────────────
install_step1_config() {
    log_step 1 9 "Locating config.yaml"

    # Priority: 1. --config argument, 2. ./config.yaml (dev), 3. /etc/aicollector/config.yaml
    if [[ -n "${CUSTOM_CONFIG}" ]]; then
        if [[ ! -f "${CUSTOM_CONFIG}" ]]; then
            log_fatal "Custom config file not found: ${CUSTOM_CONFIG}"
        fi
        CONFIG_FILE_SOURCE="${CUSTOM_CONFIG}"
        log_info "Using custom config: ${CUSTOM_CONFIG}"
    elif [[ -f "./config.yaml" ]]; then
        CONFIG_FILE_SOURCE="./config.yaml"
        log_info "Found ./config.yaml (dev mode) — using as source"
    elif [[ -f "/etc/aicollector/config.yaml" ]]; then
        CONFIG_FILE_SOURCE="/etc/aicollector/config.yaml"
        log_info "Found /etc/aicollector/config.yaml — using as source"
    else
        log_warn "No config.yaml found — a default one will be created"
        CONFIG_FILE_SOURCE=""
    fi

    # Detect auditd activation from the config we found
    if [[ -n "${CONFIG_FILE_SOURCE}" ]]; then
        detect_auditd_active "${CONFIG_FILE_SOURCE}"
        if [[ "${AUDITD_ACTIVE}" == "yes" ]]; then
            log_info "Collecteur 'auditd' is ACTIVE in config — will install auditd package"
        else
            log_warn "Collecteur 'auditd' is DISABLED in config — skipping auditd installation"
        fi
    else
        AUDITD_ACTIVE="yes"
        log_info "No config found — assuming all collectors active (including auditd)"
    fi
}

# ── Step 2: Install system dependencies ────────────────────────────────────────
install_step2_dependencies() {
    log_step 2 9 "Installing system dependencies"

    if [[ "${DRY_RUN}" == "yes" ]]; then
        log_dry "Would install: iproute2, smartmontools, openssl, cron, systemd (always)"
        if [[ "${AUDITD_ACTIVE}" == "yes" ]]; then
            log_dry "Would install: auditd (conditional — auditd collector is active)"
        else
            log_dry "Would SKIP: auditd (auditd collector is disabled in config.yaml)"
        fi
        return 0
    fi

    log_info "Updating package lists..."
    apt-get update -qq 2>/dev/null || log_warn "apt-get update failed"

    # Always required
    local mandatory_pkgs=(
        "iproute2"
        "smartmontools"
        "openssl"
        "cron"
        "systemd"
        "python3"
        "python3-pip"
    )

    log_info "Installing mandatory packages..."
    local install_status=0
    for pkg in "${mandatory_pkgs[@]}"; do
        if dpkg -s "${pkg}" >/dev/null 2>&1; then
            log_skip "${pkg} already installed"
        else
            if apt-get install -y -qq "${pkg}" >/dev/null 2>&1; then
                log_ok "Installed: ${pkg}"
            else
                log_warn "Failed to install: ${pkg}"
                ((install_status++))
            fi
        fi
    done

    # Conditional: auditd only if collector is active
    if [[ "${AUDITD_ACTIVE}" == "yes" ]]; then
        log_info "Installing conditional package: auditd (auditd collector is active)"
        if dpkg -s "auditd" >/dev/null 2>&1; then
            log_skip "auditd already installed (collector active)"
        else
            if apt-get install -y -qq "auditd" >/dev/null 2>&1; then
                log_ok "Installed: auditd (auditd collector is active)"
            else
                log_warn "Failed to install auditd — auditd collector may not work"
                ((install_status++))
            fi
        fi
    else
        log_skip "auditd — SKIPPED (auditd collector is DISABLED in config.yaml)"
    fi

    if [[ "${install_status}" -gt 0 ]] && [[ "${FORCE}" != "yes" ]]; then
        log_fatal "Some packages failed to install. Run with --force to ignore errors."
    fi
}

# ── Step 3: Create system user ─────────────────────────────────────────────────
install_step3_user() {
    log_step 3 9 "Creating system user"

    if id "${USER_NAME}" &>/dev/null; then
        log_skip "User '${USER_NAME}' already exists"
    else
        if [[ "${DRY_RUN}" == "yes" ]]; then
            log_dry "Would create system user: ${USER_NAME}"
            return 0
        fi
        log_info "Creating system user '${USER_NAME}'..."
        useradd --system --no-create-home --shell /usr/sbin/nologin "${USER_NAME}" \
            || { log_warn "Could not create user '${USER_NAME}'"; return 1; }
        log_ok "User '${USER_NAME}' created"
    fi
}

# ── Step 4: Create directory structure ──────────────────────────────────────────
install_step4_directories() {
    log_step 4 9 "Creating directory structure"

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
            log_skip "Directory exists: ${dir}"
        else
            if [[ "${DRY_RUN}" == "yes" ]]; then
                log_dry "Would create: ${dir}"
            else
                mkdir -p "${dir}"
                log_ok "Created: ${dir}"
            fi
        fi
    done
}

# ── Step 5: Deploy application files ───────────────────────────────────────────
install_step5_deploy() {
    log_step 5 9 "Deploying application files"

    local script_dir
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

    if [[ "${DRY_RUN}" == "yes" ]]; then
        log_dry "Would copy application files from ${script_dir} to ${INSTALL_DIR}"
        return 0
    fi

    # Find the application root (parent of scripts/)
    local app_root="${script_dir}"
    if [[ -f "${script_dir}/collector.py" ]]; then
        app_root="${script_dir}"
    elif [[ -f "${script_dir}/../collector.py" ]]; then
        app_root="$(cd "${script_dir}/.." && pwd)"
    fi

    if [[ -f "${app_root}/collector.py" ]]; then
        log_info "Deploying application from ${app_root}..."

        # Copy core application files (exclude scripts themselves)
        rsync -a --exclude='install.sh' --exclude='uninstall.sh' \
              --exclude='check_dependencies.sh' \
              --exclude='config.yaml' \
              "${app_root}/" "${INSTALL_DIR}/" 2>/dev/null \
            || cp -r "${app_root}/." "${INSTALL_DIR}/"

        log_ok "Application files deployed to ${INSTALL_DIR}"
    else
        log_warn "Application source not found at ${app_root} — skipping deployment"
        log_warn "Please ensure collector.py and core/collectors/ are present"
    fi
}

# ── Step 6: Deploy configuration ─────────────────────────────────────────────
install_step6_config() {
    log_step 6 9 "Deploying configuration"

    if [[ -n "${CONFIG_FILE_SOURCE}" ]] && [[ -f "${CONFIG_FILE_SOURCE}" ]]; then
        if [[ "${DRY_RUN}" == "yes" ]]; then
            log_dry "Would copy ${CONFIG_FILE_SOURCE} to ${CONFIG_FILE}"
            return 0
        fi
        cp "${CONFIG_FILE_SOURCE}" "${CONFIG_FILE}"
        log_ok "Config deployed: ${CONFIG_FILE}"
    else
        if [[ "${DRY_RUN}" == "yes" ]]; then
            log_dry "Would create default config at ${CONFIG_FILE}"
            return 0
        fi
        # Create a minimal default config
        cat > "${CONFIG_FILE}" << 'DEFAULTCONFIG'
# AICollector configuration
# Generated by install.sh

collectors:
  # Liste blanche — vide = tous les collecteurs sont actifs
  # Décommentez et modifiez pour activer uniquement certains collecteurs
  # enabled:
  #   - system
  #   - cpu
  #   - ram
  #   - auditd

  # Liste noire — vide = aucun collecteur désactivé
  # Décommentez pour désactiver des collecteurs spécifiques
  # disabled:
  #   - auditd

retention:
  history_versions: 50
  changes_entries: 200
  logs_days: 30

scheduler:
  frequency_cron: "0 */2 * * *"
  use_systemd_timer: false

logging:
  level: INFO
DEFAULTCONFIG
        log_ok "Default config created: ${CONFIG_FILE}"
    fi
}

# ── Step 7: Set permissions ─────────────────────────────────────────────────────
install_step7_permissions() {
    log_step 7 9 "Setting permissions"

    if [[ "${DRY_RUN}" == "yes" ]]; then
        log_dry "Would set ownership of ${LIB_DIR}, ${LOG_DIR}, ${RUN_DIR} to ${USER_NAME}"
        return 0
    fi

    # Set ownership on data/logs directories
    chown -R "${USER_NAME}:${USER_NAME}" "${LIB_DIR}"  2>/dev/null || log_warn "Could not set ownership on ${LIB_DIR}"
    chown -R "${USER_NAME}:${USER_NAME}" "${LOG_DIR}"  2>/dev/null || log_warn "Could not set ownership on ${LOG_DIR}"
    chown -R "${USER_NAME}:${USER_NAME}" "${CACHE_DIR}" 2>/dev/null || log_warn "Could not set ownership on ${CACHE_DIR}"

    # Make collector.py executable
    chmod +x "${INSTALL_DIR}/collector.py" 2>/dev/null || log_warn "Could not chmod collector.py"

    log_ok "Permissions set"
}

# ── Step 8: Configure scheduler ───────────────────────────────────────────────
install_step8_scheduler() {
    log_step 8 9 "Configuring scheduler"

    local schedule="${DEFAULT_SCHEDULE}"
    if [[ -f "${CONFIG_FILE}" ]] && command -v python3 >/dev/null 2>&1; then
        local configured_schedule
        configured_schedule=$(python3 -c "
import yaml, sys
try:
    with open('${CONFIG_FILE}') as f:
        config = yaml.safe_load(f) or {}
    scheduler = config.get('scheduler', {})
    print(scheduler.get('frequency_cron', '0 */2 * * *'))
except:
    print('0 */2 * * *')
" 2>/dev/null || echo "0 */2 * * *")
        [[ -n "${configured_schedule}" ]] && schedule="${configured_schedule}"
    fi

    if [[ "${DRY_RUN}" == "yes" ]]; then
        log_dry "Would add cron entry: ${schedule} ${INSTALL_DIR}/collector.py --run"
        return 0
    fi

    # Add cron entry (idempotent — grep check before adding)
    local current_cron
    current_cron=$(crontab -l 2>/dev/null || echo "")

    if echo "${current_cron}" | grep -q "${CRON_MARKER}"; then
        log_skip "Cron entry already exists"
    else
        local cron_entry="${schedule} ${INSTALL_DIR}/collector.py --run ${CRON_MARKER}"
        (echo "${current_cron}"; echo "${cron_entry}") | crontab - \
            || { log_warn "Could not update crontab"; return 1; }
        log_ok "Cron entry added: ${schedule}"
    fi
}

# ── Step 9: Configure tmpfiles.d ──────────────────────────────────────────────
install_step9_tmpfiles() {
    log_step 9 9 "Configuring tmpfiles.d"

    if [[ "${DRY_RUN}" == "yes" ]]; then
        log_dry "Would create /etc/tmpfiles.d/${APP_NAME}.conf"
        return 0
    fi

    if [[ -f "/etc/tmpfiles.d/${APP_NAME}.conf" ]]; then
        log_skip "tmpfiles.d config already exists"
    else
        cat > "/etc/tmpfiles.d/${APP_NAME}.conf" << EOF
# Type Path               Mode UID           GID           Age Argument
d     ${RUN_DIR}           0755 ${USER_NAME} ${USER_NAME} - -
EOF
        tmpfiles --create "/etc/tmpfiles.d/${APP_NAME}.conf" 2>/dev/null \
            || log_warn "tmpfiles --create failed (may be benign)"
        log_ok "tmpfiles.d configured"
    fi
}

# ── Main ────────────────────────────────────────────────────────────────────────
main() {
    echo ""
    echo -e "${BOLD}═══════════════════════════════════════════════════════════════════${NC}"
    echo -e "${BOLD}  AICollector — Installation Script${NC}"
    echo -e "${BOLD}═══════════════════════════════════════════════════════════════════${NC}"
    echo ""

    require_root

    if [[ "${DRY_RUN}" == "yes" ]]; then
        echo -e "${CYAN}MODE: DRY-RUN (no files will be created or modified)${NC}"
        echo ""
    fi

    echo "This script will:"
    echo "  • Install system dependencies (always)"
    echo "  • Install auditd only if 'auditd' collector is active in config.yaml"
    echo "  • Create system user '${USER_NAME}'"
    echo "  • Create directory structure"
    echo "  • Deploy application files to ${INSTALL_DIR}"
    echo "  • Deploy configuration to ${ETC_DIR}"
    echo "  • Set correct permissions"
    echo "  • Configure scheduling (cron)"
    echo "  • Configure tmpfiles.d"
    echo ""

    if [[ "${DRY_RUN}" != "yes" ]]; then
        echo -n "Proceed with installation? [y/N]: "
        read -r response < /dev/tty
        if [[ ! "${response}" =~ ^[Yy]$ ]]; then
            echo "Aborted."
            exit 0
        fi
    fi

    echo ""
    install_step1_config
    install_step2_dependencies
    install_step3_user
    install_step4_directories
    install_step5_deploy
    install_step6_config
    install_step7_permissions
    install_step8_scheduler
    install_step9_tmpfiles

    echo ""
    echo -e "${BOLD}═══════════════════════════════════════════════════════════════════${NC}"
    if [[ "${DRY_RUN}" == "yes" ]]; then
        echo -e "${CYAN}Installation dry-run completed.${NC}"
        echo -e "${CYAN}Run without --dry-run to perform actual installation.${NC}"
    else
        echo -e "${GREEN}AICollector installed successfully!${NC}"
        echo ""
        echo "Next steps:"
        echo "  1. Review configuration: ${CONFIG_FILE}"
        echo "  2. Run the collector:   sudo ${INSTALL_DIR}/collector.py --run"
        echo "  3. Check logs:          tail -f ${LOG_DIR}/aicollector.log"
        echo "  4. Uninstall:            sudo bash ${INSTALL_DIR}/../uninstall.sh"
    fi
    echo -e "${BOLD}═══════════════════════════════════════════════════════════════════${NC}"
    echo ""
}

main "$@"
