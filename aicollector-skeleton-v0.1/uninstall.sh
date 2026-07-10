#!/usr/bin/env bash
# =============================================================================
# uninstall.sh — AICollector complete, safe, and idempotent uninstallation
#
# Target: Ubuntu Server 26.04 LTS (Production Hardened)
# Requires: Root privileges (sudo)
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
CRON_MARKER="# AICollector cron"
UNIT_DIR="/etc/systemd/system"
CONFIG_FILE="${ETC_DIR}/config.yaml"
USER_NAME="${APP_NAME}"

# ── Colour helpers ────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# ── State variables ───────────────────────────────────────────────────────────
AUDITD_WAS_ACTIVE="no"
DRY_RUN="no"
FORCE="no"

# ── Logging helpers ───────────────────────────────────────────────────────────
log_info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_fatal() { echo -e "${RED}[FATAL]${NC} $*"; exit 1; }
log_ok()    { echo -e "${GREEN}[OK]${NC}   $*"; }
log_dry()   { echo -e "${CYAN}[DRY]${NC}   $*"; }
log_skip()  { echo -e "${YELLOW}[SKIP]${NC}  $*"; }
log_step()  {
    local n="$1"; local total="$2"
    echo ""
    echo -e "${BOLD}=== Step ${n}/${total}: ${*:3} ===${NC}"
}

# ── CLI parsing ────────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run)  DRY_RUN="yes"; shift ;;
        --force)    FORCE="yes";  shift ;;
        --help|-h)
            echo "Usage: sudo bash $0 [--dry-run] [--force]"
            echo ""
            echo "Options:"
            echo "  --dry-run  Simulate uninstallation (no files modified)"
            echo "  --force    Continue even if non-critical errors occur"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

# ── Safety Checks ─────────────────────────────────────────────────────────────
require_root() {
    if [[ $EUID -ne 0 ]]; then
        log_fatal "This script must be run with root privileges (sudo)."
    fi
}

# Détection de l'activation du collecteur auditd
detect_auditd_status() {
    AUDITD_WAS_ACTIVE="no"
    if [[ ! -f "${CONFIG_FILE}" ]]; then
        return
    fi

    if command -v python3 >/dev/null 2>&1; then
        local result
        result=$(python3 -c "
import yaml, sys
try:
    with open('${CONFIG_FILE}') as f:
        config = yaml.safe_load(f) or {}
    collectors = config.get('collectors', {})
    enabled = collectors.get('enabled', None) or []
    disabled = collectors.get('disabled', None) or []
    if 'auditd' in disabled:
        print('DISABLED')
    elif enabled and 'auditd' not in enabled:
        print('DISABLED')
    else:
        print('ACTIVE')
except Exception:
    print('ERROR')
" 2>/dev/null || echo "ERROR")

        if [[ "${result}" == "ACTIVE" ]]; then
            AUDITD_WAS_ACTIVE="yes"
        fi
    fi
}

# Suppression sécurisée de fichiers et dossiers
safe_remove() {
    local target="$1"
    local desc="$2"

    if [[ -e "${target}" ]] || [[ -L "${target}" ]]; then
        if [[ "${DRY_RUN}" == "yes" ]]; then
            log_dry "Would remove: ${desc} (${target})"
        else
            rm -rf "${target}" 2>/dev/null || {
                if [[ "${FORCE}" == "yes" ]]; then
                    log_warn "Failed to remove ${target}, forcing continuation..."
                else
                    log_fatal "Failed to remove ${target}. Check directory locks."
                fi
            }
            log_ok "Removed: ${desc}"
        fi
    else
        log_skip "Already absent: ${desc}"
    fi
}

# ── Step 1: Arrêt des processus actifs (Priorité absolue) ─────────────────────
uninstall_step1_processes() {
    log_step 1 10 "Stopping running processes and background runs"

    if [[ "${DRY_RUN}" == "yes" ]]; then
        log_dry "Would check lockfile ${LOCKFILE} and send SIGTERM/SIGKILL if active."
        return 0
    fi

    # 1. Utilisation du lockfile
    if [[ -f "${LOCKFILE}" ]]; then
        local pid
        pid=$(cat "${LOCKFILE}" 2>/dev/null || echo "")
        if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
            log_info "Sending SIGTERM to active run (PID ${pid})..."
            kill -15 "${pid}" 2>/dev/null || true
            sleep 2
            if kill -0 "${pid}" 2>/dev/null; then
                log_warn "Process ${pid} did not exit. Sending SIGKILL..."
                kill -9 "${pid}" 2>/dev/null || true
            fi
        fi
    fi

    # 2. Nettoyage des processus orphelins résiduels
    local pids
    pids=$(pgrep -f "collector.py" || echo "")
    if [[ -n "${pids}" ]]; then
        log_info "Stopping orphaned collector processes..."
        for p in ${pids}; do
            kill -9 "${p}" 2>/dev/null || true
        done
        log_ok "All processes stopped."
    else
        log_skip "No active AICollector processes found."
    fi
}

# ── Step 2: Désactivation et retrait de la planification Cron ──────────────────
uninstall_step2_cron() {
    log_step 2 10 "Removing Scheduled Cron Tasks"

    local current_cron
    current_cron=$(crontab -l 2>/dev/null || echo "")

    if echo "${current_cron}" | grep -q "${CRON_MARKER}"; then
        if [[ "${DRY_RUN}" == "yes" ]]; then
            log_dry "Would filter and rewrite crontab without ${CRON_MARKER} entries."
            return 0
        fi

        local filtered_cron
        filtered_cron=$(echo "${current_cron}" | grep -v "${CRON_MARKER}" || echo "")

        if [[ -z "${filtered_cron//[[:space:]]/}" ]]; then
            # Si le cron résultant est vide, on supprime la crontab proprement
            crontab -r || true
            log_ok "Crontab was empty, successfully removed table."
        else
            echo "${filtered_cron}" | crontab -
            log_ok "AICollector task removed from crontab."
        fi
    else
        log_skip "No AICollector tasks found in crontab."
    fi
}

# ── Step 3: Désactivation et retrait des unités Systemd ────────────────────────
uninstall_step3_systemd() {
    log_step 3 10 "Removing Systemd services and timers"

    if [[ "${DRY_RUN}" == "yes" ]]; then
        log_dry "Would stop and disable ${APP_NAME}.timer"
        log_dry "Would remove ${UNIT_DIR}/${APP_NAME}.service and timer files."
        return 0
    fi

    if systemctl list-unit-files | grep -q "${APP_NAME}.timer"; then
        log_info "Stopping and disabling systemd timer..."
        systemctl stop "${APP_NAME}.timer" 2>/dev/null || true
        systemctl disable "${APP_NAME}.timer" 2>/dev/null || true
    fi

    safe_remove "${UNIT_DIR}/${APP_NAME}.service" "Systemd Service unit"
    safe_remove "${UNIT_DIR}/${APP_NAME}.timer" "Systemd Timer unit"
    systemctl daemon-reload || true
}

# ── Step 4: Nettoyage du répertoire applicatif ────────────────────────────────
uninstall_step4_app() {
    log_step 4 10 "Removing application workspace (/opt)"
    safe_remove "${INSTALL_DIR}" "Application Core Workspace"
}

# ── Step 5: Nettoyage de la configuration utilisateur ─────────────────────────
uninstall_step5_config() {
    log_step 5 10 "Removing configuration files (/etc)"
    safe_remove "${ETC_DIR}" "Configuration directories and files"
}

# ── Step 6: Nettoyage de la base de connaissances et de l'historique ──────────
uninstall_step6_data() {
    log_step 6 10 "Removing local data persistent stores (/var/lib)"
    safe_remove "${LIB_DIR}" "Knowledge base, history and manifest stores"
}

# ── Step 7: Nettoyage des répertoires temporaires et caches ────────────────────
uninstall_step7_cache() {
    log_step 7 10 "Removing caches and temporary directories"
    safe_remove "${CACHE_DIR}" "Internal pipeline caches"
    safe_remove "${RUN_DIR}" "Volatile PID runtime locks"
}

# ── Step 8: Nettoyage des journaux d'exécution (Logs et Logrotate) ────────────
uninstall_step8_logs() {
    log_step 8 10 "Removing execution logs"
    safe_remove "${LOG_DIR}" "Application event logs"
    safe_remove "/etc/logrotate.d/${APP_NAME}" "Logrotate log cycling config"
    safe_remove "/etc/tmpfiles.d/${APP_NAME}.conf" "Tmpfiles dynamic run structure creation config"
}

# ── Step 9: Désinstallation du collecteur d'audit système (auditd) ─────────────
uninstall_step9_auditd() {
    log_step 9 10 "Evaluating conditional package cleanup (auditd)"

    if [[ "${AUDITD_WAS_ACTIVE}" == "yes" ]]; then
        if [[ "${DRY_RUN}" == "yes" ]]; then
            log_dry "Would check reverse dependencies and try to uninstall auditd package."
            return 0
        fi

        log_info "AICollector configuration utilized system auditd. Checking safety..."
        # Vérification qu'aucun autre paquet d'audit ou de sécurité n'en dépend activement
        if dpkg -s "auditd" >/dev/null 2>&1; then
            # S'il n'y a pas d'autres dépendances fortes actives sur le système
            log_info "Removing auditd tool package..."
            apt-get remove -y -qq auditd 2>/dev/null || log_warn "Apt refused to purge auditd. Keeping it on system."
            log_ok "Auditd package cleanup executed."
        else
            log_skip "Auditd was not installed on system. Skipping."
        fi
    else
        log_skip "Auditd collector was not active or already disabled. Package retained on system."
    fi
}

# ── Step 10: Retrait sécurisé de l'utilisateur système ────────────────────────
uninstall_step10_user() {
    log_step 10 10 "Removing restricted system user account"

    if id "${USER_NAME}" &>/dev/null; then
        if [[ "${DRY_RUN}" == "yes" ]]; then
            log_dry "Would delete system user: ${USER_NAME}"
            return 0
        fi

        log_info "Deleting system user: ${USER_NAME}..."
        # Suppression SANS --remove-home car l'utilisateur a été créé sans home folder
        userdel "${USER_NAME}" 2>/dev/null || {
            if [[ "${FORCE}" == "yes" ]]; then
                log_warn "Failed to delete user database entry. Continuing."
            else
                log_fatal "Could not clean up user ${USER_NAME}."
            fi
        }
        log_ok "User database cleaned up."
    else
        log_skip "System user already deleted."
    fi
}

# ── Main ──────────────────────────────────────────────────────────────────────
main() {
    echo ""
    echo -e "${BOLD}═══════════════════════════════════════════════════════════════════${NC}"
    echo -e "${BOLD}  AICollector — Hardened Uninstallation Script${NC}"
    echo -e "${BOLD}═══════════════════════════════════════════════════════════════════${NC}"
    echo ""

    require_root
    detect_auditd_status

    if [[ "${DRY_RUN}" == "yes" ]]; then
        echo -e "${CYAN}MODE: DRY-RUN ACTIVE (simulation only)${NC}"
        echo ""
    fi

    if [[ "${DRY_RUN}" != "yes" ]]; then
        echo -n "This action is destructive. Proceed with full uninstall? [y/N]: "
        read -r response < /dev/tty
        if [[ ! "${response}" =~ ^[Yy]$ ]]; then
            log_fatal "Uninstallation aborted."
        fi
    fi

    # Séquence stricte : Arrêt → Programmation → Fichiers → Utilisateurs
    uninstall_step1_processes
    uninstall_step2_cron
    uninstall_step3_systemd
    uninstall_step4_app
    uninstall_step5_config
    uninstall_step6_data
    uninstall_step7_cache
    uninstall_step8_logs
    uninstall_step9_auditd
    uninstall_step10_user

    echo ""
    echo -e "${BOLD}═══════════════════════════════════════════════════════════════════${NC}"
    if [[ "${DRY_RUN}" == "yes" ]]; then
        log_ok "Safe uninstallation dry-run complete without system modification."
    else
        log_ok "AICollector completely and safely uninstalled."
    fi
    echo -e "${BOLD}═══════════════════════════════════════════════════════════════════${NC}"
    echo ""
}

main "$@"
