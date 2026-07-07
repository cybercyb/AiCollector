#!/usr/bin/env bash
# =============================================================================
# uninstall.sh — AICollector complete uninstallation script
#
# Usage: sudo bash uninstall.sh [--dry-run] [--force]
#
# Options:
#   --dry-run  Simulate uninstallation without removing anything
#   --force   Continue even if errors occur (e.g., processes running)
#
# What this script removes:
#   - System user 'aicollector' and its home directory
#   - /opt/aicollector/       — Application files
#   - /etc/aicollector/      — Configuration files
#   - /var/lib/aicollector/  — Knowledge base, history, changes
#   - /var/cache/aicollector/ — Cache data
#   - /var/log/aicollector/   — Log files
#   - /run/aicollector/       — Runtime files (PID lock, etc.)
#   - /etc/tmpfiles.d/aicollector.conf — tmpfs volatile directory config
#   - Cron entries tagged "# AICollector cron"
#   - Systemd service & timer units
#
# Idempotence: Safe to run multiple times. Already-removed items are skipped.
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

# Default uninstallation user (must match install.sh default)
USER_NAME="${APP_NAME}"

# ── Colour helpers ──────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'  # No Colour

# ── Logging functions ──────────────────────────────────────────────────────────
log_info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_fatal() { echo -e "${RED}[FATAL]${NC} $*"; exit 1; }
log_ok()    { echo -e "${GREEN}[OK]${NC}   $*"; }
log_dry()   { echo -e "${CYAN}[DRY]${NC}   $*"; }
log_skip()  { echo -e "${YELLOW}[SKIP]${NC}  $*"; }

# ── CLI parsing ────────────────────────────────────────────────────────────────
DRY_RUN="no"
FORCE="no"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run)  DRY_RUN="yes"; shift ;;
        --force)    FORCE="yes";  shift ;;
        --help|-h)
            echo "Usage: sudo bash $0 [--dry-run] [--force]"
            echo ""
            echo "Options:"
            echo "  --dry-run  Simulate uninstallation (no files removed)"
            echo "  --force    Continue even if errors occur"
            echo "  --help     Show this help message"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

# ── Helper: require root privileges ─────────────────────────────────────────────
require_root() {
    if [[ $EUID -ne 0 ]]; then
        log_fatal "This script must be run as root (use sudo)"
    fi
}

# ── Helper: safe remove (idempotent) ───────────────────────────────────────────
# Removes a file or directory only if it exists.
# In dry-run mode, prints what would be done.
# Returns 0 if already absent or successfully removed.
safe_remove() {
    local target="$1"
    local description="${2:-${target}}"

    if [[ -e "${target}" ]] || [[ -L "${target}" ]]; then
        if [[ "${DRY_RUN}" == "yes" ]]; then
            log_dry "Would remove: ${description}"
        else
            rm -rf "${target}" 2>/dev/null || {
                if [[ "${FORCE}" == "yes" ]]; then
                    log_warn "Failed to remove ${description}, continuing with --force..."
                    return 0
                else
                    log_warn "Could not remove ${description} (permissions issue?)"
                    return 1
                fi
            }
            log_ok "Removed: ${description}"
        fi
        return 0
    else
        log_skip "Already absent (not found): ${description}"
        return 0
    fi
}

# ── Helper: kill running processes ──────────────────────────────────────────────
# Stops the collector process if running (via lockfile PID or direct name match).
kill_process() {
    if [[ "${DRY_RUN}" == "yes" ]]; then
        if [[ -f "${LOCKFILE}" ]]; then
            local pid
            pid=$(cat "${LOCKFILE}" 2>/dev/null || echo "")
            if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
                log_dry "Would kill process ${pid} (from lockfile)"
            fi
        fi
        log_dry "Would kill any remaining ${APP_NAME} processes"
        return 0
    fi

    local killed=0

    # Try to stop via lockfile PID first
    if [[ -f "${LOCKFILE}" ]]; then
        local pid
        pid=$(cat "${LOCKFILE}" 2>/dev/null || echo "")
        if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
            log_info "Stopping running collector (PID ${pid})..."
            kill "${pid}" 2>/dev/null && killed=1
            sleep 1  # Brief delay for graceful shutdown
            # Force kill if still running
            kill -0 "${pid}" 2>/dev/null && kill -9 "${pid}" 2>/dev/null && killed=1
        fi
    fi

    # Also kill any orphaned processes by name
    if pgrep -x "python3" >/dev/null 2>&1; then
        local orphans
        orphans=$(pgrep -f "${INSTALL_DIR}/collector.py" 2>/dev/null || echo "")
        for pid in ${orphans}; do
            if [[ -n "${pid}" ]]; then
                log_info "Stopping orphaned collector process (PID ${pid})..."
                kill "${pid}" 2>/dev/null && killed=1
                sleep 1
                kill -0 "${pid}" 2>/dev/null && kill -9 "${pid}" 2>/dev/null && killed=1
            fi
        done
    fi

    if [[ "${killed}" == "1" ]]; then
        log_ok "All processes stopped"
    fi
}

# ── Step 1: Stop systemd timer ──────────────────────────────────────────────────
uninstall_systemd_timer() {
    log_info "=== Step 1/9: Stopping systemd timer ==="

    if [[ "${DRY_RUN}" == "yes" ]]; then
        log_dry "Would stop and disable aicollector.timer"
        log_dry "Would remove: ${UNIT_DIR}/${APP_NAME}.service"
        log_dry "Would remove: ${UNIT_DIR}/${APP_NAME}.timer"
        log_dry "Would run: systemctl daemon-reload"
        return 0
    fi

    # Stop timer if running
    if systemctl is-active --quiet "${APP_NAME}.timer" 2>/dev/null; then
        log_info "Stopping aicollector.timer..."
        systemctl stop "${APP_NAME}.timer" 2>/dev/null \
            || log_warn "Could not stop timer (systemd may not be running)"
        log_ok "Timer stopped"
    else
        log_skip "Timer not active (already stopped or not installed)"
    fi

    # Disable timer
    if systemctl is-enabled --quiet "${APP_NAME}.timer" 2>/dev/null; then
        log_info "Disabling aicollector.timer..."
        systemctl disable "${APP_NAME}.timer" 2>/dev/null \
            || log_warn "Could not disable timer"
        log_ok "Timer disabled"
    else
        log_skip "Timer not enabled"
    fi

    # Remove unit files
    safe_remove "${UNIT_DIR}/${APP_NAME}.service" "systemd service file"
    safe_remove "${UNIT_DIR}/${APP_NAME}.timer" "systemd timer file"

    # Reload systemd daemon
    if command -v systemctl >/dev/null 2>&1; then
        systemctl daemon-reload 2>/dev/null \
            || log_warn "Could not reload systemd daemon"
    fi
}

# ── Step 2: Remove cron entry ───────────────────────────────────────────────────
uninstall_cron() {
    log_info "=== Step 2/9: Removing cron entry ==="

    local current_cron
    current_cron=$(crontab -l 2>/dev/null || echo "")

    if echo "${current_cron}" | grep -q "${CRON_MARKER}"; then
        if [[ "${DRY_RUN}" == "yes" ]]; then
            log_dry "Would remove cron entry marked with '${CRON_MARKER}'"
            return 0
        fi

        log_info "Removing AICollector cron entry..."
        echo "${current_cron}" | grep -v "${CRON_MARKER}" > /tmp/crontab.tmp \
            && crontab /tmp/crontab.tmp && rm -f /tmp/crontab.tmp \
            || {
                log_warn "Could not update crontab"
                rm -f /tmp/crontab.tmp
                return 1
            }
        log_ok "Cron entry removed"
    else
        log_skip "No AICollector cron entry found"
    fi
}

# ── Step 3: Stop running processes ─────────────────────────────────────────────
uninstall_processes() {
    log_info "=== Step 3/9: Stopping running processes ==="
    kill_process
}

# ── Step 4: Remove application directory ───────────────────────────────────────
uninstall_install_dir() {
    log_info "=== Step 4/9: Removing application directory ==="
    safe_remove "${INSTALL_DIR}" "Application directory (/opt/aicollector)"
}

# ── Step 5: Remove configuration directory ─────────────────────────────────────
uninstall_etc_dir() {
    log_info "=== Step 5/9: Removing configuration directory ==="
    safe_remove "${ETC_DIR}" "Configuration directory (/etc/aicollector)"
}

# ── Step 6: Remove data directories ───────────────────────────────────────────
uninstall_data_dirs() {
    log_info "=== Step 6/9: Removing data directories ==="
    safe_remove "${LIB_DIR}"   "Data library (/var/lib/aicollector)"
    safe_remove "${CACHE_DIR}" "Cache directory (/var/cache/aicollector)"
    safe_remove "${LOG_DIR}"   "Log directory (/var/log/aicollector)"
    safe_remove "${RUN_DIR}"   "Runtime directory (/run/aicollector)"
}

# ── Step 7: Remove tmpfiles.d configuration ────────────────────────────────────
uninstall_tmpfiles_d() {
    log_info "=== Step 7/9: Removing tmpfiles.d configuration ==="
    safe_remove "/etc/tmpfiles.d/${APP_NAME}.conf" "tmpfiles.d configuration"
}

# ── Step 8: Remove system user ─────────────────────────────────────────────────
uninstall_user() {
    log_info "=== Step 8/9: Removing system user ==="

    if id "${USER_NAME}" &>/dev/null; then
        if [[ "${DRY_RUN}" == "yes" ]]; then
            log_dry "Would remove system user '${USER_NAME}' and its home directory"
            return 0
        fi

        log_info "Removing system user '${USER_NAME}'..."
        # userdel removes the user and its home directory by default
        userdel --remove-home "${USER_NAME}" 2>/dev/null || {
            if [[ "${FORCE}" == "yes" ]]; then
                log_warn "userdel failed, attempting to remove user anyway..."
                # Fallback: manually remove user from system databases
                userdel "${USER_NAME}" 2>/dev/null || true
            else
                log_warn "Could not remove user '${USER_NAME}'"
                return 1
            fi
        }
        log_ok "User '${USER_NAME}' removed"
    else
        log_skip "User '${USER_NAME}' does not exist (already removed)"
    fi
}

# ── Step 9: Final cleanup and verification ─────────────────────────────────────
uninstall_final_cleanup() {
    log_info "=== Step 9/9: Final verification ==="

    if [[ "${DRY_RUN}" == "yes" ]]; then
        log_dry "Would verify no residual files remain"
        log_info ""
        log_info "Dry-run complete. Run without --dry-run to actually uninstall."
        return 0
    fi

    local remaining=()

    # Check for any remaining files/directories
    for dir in "${INSTALL_DIR}" "${ETC_DIR}" "${LIB_DIR}" "${CACHE_DIR}" "${LOG_DIR}" "${RUN_DIR}"; do
        [[ -e "${dir}" ]] && remaining+=("${dir}")
    done

    # Check for remaining user
    id "${USER_NAME}" &>/dev/null && remaining+=("user:${USER_NAME}")

    # Check for remaining systemd units
    for unit in "${UNIT_DIR}/${APP_NAME}.service" "${UNIT_DIR}/${APP_NAME}.timer"; do
        [[ -e "${unit}" ]] && remaining+=("${unit}")
    done

    # Check for tmpfiles.d
    [[ -e "/etc/tmpfiles.d/${APP_NAME}.conf" ]] && remaining+=("tmpfiles.d")

    if [[ ${#remaining[@]} -eq 0 ]]; then
        log_ok "All AICollector components removed"
    else
        log_warn "Some components may still remain:"
        for item in "${remaining[@]}"; do
            echo "  - ${item}"
        done
        echo ""
        log_warn "You may need to manually remove remaining items with root privileges."
    fi
}

# ── Main ────────────────────────────────────────────────────────────────────────
main() {
    echo ""
    echo -e "${BOLD}═══════════════════════════════════════════════════════════════════${NC}"
    echo -e "${BOLD}  AICollector — Complete Uninstallation Script${NC}"
    echo -e "${BOLD}═══════════════════════════════════════════════════════════════════${NC}"
    echo ""

    # Require root privileges
    require_root

    # Display mode
    if [[ "${DRY_RUN}" == "yes" ]]; then
        echo -e "${CYAN}MODE: DRY-RUN (no files will be modified)${NC}"
        echo ""
    fi
    if [[ "${FORCE}" == "yes" ]]; then
        echo -e "${YELLOW}MODE: FORCE (errors will be reported but script will continue)${NC}"
        echo ""
    fi

    echo "This script will remove:"
    echo "  • System user: ${USER_NAME}"
    echo "  • Application: ${INSTALL_DIR}"
    echo "  • Configuration: ${ETC_DIR}"
    echo "  • Data: ${LIB_DIR}"
    echo "  • Cache: ${CACHE_DIR}"
    echo "  • Logs: ${LOG_DIR}"
    echo "  • Runtime: ${RUN_DIR}"
    echo "  • Cron entries"
    echo "  • Systemd timer/service"
    echo "  • tmpfiles.d configuration"
    echo ""

    # Ask for confirmation in non-dry-run mode
    if [[ "${DRY_RUN}" != "yes" ]]; then
        echo -n "Proceed with uninstallation? [y/N]: "
        read -r response < /dev/tty
        if [[ ! "${response}" =~ ^[Yy]$ ]]; then
            echo "Aborted."
            exit 0
        fi
    fi

    echo ""
    echo "Starting uninstallation..."
    echo ""

    # Run uninstallation steps in order
    uninstall_processes      # Must stop processes before removing files
    uninstall_systemd_timer  # Stop services first
    uninstall_cron           # Remove scheduled tasks
    uninstall_install_dir    # Application files
    uninstall_etc_dir        # Configuration
    uninstall_data_dirs      # Data, cache, logs, runtime
    uninstall_tmpfiles_d     # tmpfiles config
    uninstall_user           # Remove system user last
    uninstall_final_cleanup  # Final verification

    echo ""
    echo -e "${BOLD}═══════════════════════════════════════════════════════════════════${NC}"
    if [[ "${DRY_RUN}" == "yes" ]]; then
        echo -e "${CYAN}Uninstallation dry-run completed successfully.${NC}"
        echo -e "${CYAN}Run again without --dry-run to perform actual uninstallation.${NC}"
    else
        echo -e "${GREEN}AICollector has been completely uninstalled.${NC}"
        echo ""
        echo "Thank you for using AICollector."
    fi
    echo -e "${BOLD}═══════════════════════════════════════════════════════════════════${NC}"
    echo ""
}

# Run main function
main "$@"
