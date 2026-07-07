#!/usr/bin/env bash
# =============================================================================
# check_dependencies.sh — AICollector dependency checker
#
# Usage: bash check_dependencies.sh [--mandatory-only] [--json] [--dry-run]
#
# Options:
#   --mandatory-only  Skip auditd check (use when auditd collector is disabled)
#   --json            Output results in JSON format
#   --dry-run         Show what would be installed (no changes)
#   --help            Show this help message
#
# The script auto-detects auditd activation status from config.yaml.
# Exit codes:
#   0 = All required dependencies satisfied
#   1 = Missing dependencies found
#   2 = Invalid arguments
#   3 = Cannot read config.yaml
# =============================================================================

set -eo pipefail

# ── Constants ─────────────────────────────────────────────────────────────────
APP_NAME="aicollector"
CONFIG_FILE="/etc/aicollector/config.yaml"
INSTALL_DIR="/opt/aicollector"

# Colours
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# ── CLI parsing ───────────────────────────────────────────────────────────────
MANDATORY_ONLY="no"
JSON_OUTPUT="no"
DRY_RUN="no"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --mandatory-only) MANDATORY_ONLY="yes"; shift ;;
        --json)           JSON_OUTPUT="yes"; shift ;;
        --dry-run)        DRY_RUN="yes"; shift ;;
        --help|-h)
            echo "Usage: bash $0 [--mandatory-only] [--json] [--dry-run]"
            echo ""
            echo "Options:"
            echo "  --mandatory-only  Skip auditd check (use when collector is disabled)"
            echo "  --json            Output results in JSON format"
            echo "  --dry-run         Show what would be installed (no changes)"
            echo "  --help            Show this help message"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            echo "Use --help for usage information"
            exit 2
            ;;
    esac
done

# ── Logging functions ──────────────────────────────────────────────────────────
log_info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }
log_skip()  { echo -e "${CYAN}[SKIP]${NC}  $*"; }

# ── JSON output helpers ───────────────────────────────────────────────────────
declare -a MISSING_PACKAGES=()
declare -a MISSING_TOOLS=()
declare -a WARNINGS=()
JSON_EXIT_CODE=0

add_missing_package() {
    MISSING_PACKAGES+=("$1")
}

add_missing_tool() {
    MISSING_TOOLS+=("$1")
}

add_warning() {
    WARNINGS+=("$1")
}

print_json() {
    local status="ok"
    [[ ${#MISSING_PACKAGES[@]} -gt 0 ]] || [[ ${#MISSING_TOOLS[@]} -gt 0 ]] && status="missing"

    echo "{"
    echo "  \"status\": \"${status}\","
    echo "  \"mandatory_only\": \"${MANDATORY_ONLY}\","
    echo "  \"auditd_collector_active\": \"${AUDITD_ACTIVE}\","
    echo "  \"config_file_checked\": \"${CONFIG_CHECKED}\","

    # Missing packages
    echo "  \"missing_packages\": ["
    local first=true
    for pkg in "${MISSING_PACKAGES[@]}"; do
        [[ -n "$first" ]] && first=false || echo ","
        echo -n "    \"${pkg}\""
    done
    echo ""
    echo "  ],"

    # Missing tools
    echo "  \"missing_tools\": ["
    first=true
    for tool in "${MISSING_TOOLS[@]}"; do
        [[ -n "$first" ]] && first=false || echo ","
        echo -n "    \"${tool}\""
    done
    echo ""
    echo "  ],"

    # Warnings
    echo "  \"warnings\": ["
    first=true
    for warn in "${WARNINGS[@]}"; do
        [[ -n "$first" ]] && first=false || echo ","
        echo "    \"${warn}\""
    done
    echo ""
    echo "  ]"
    echo "}"
}

# ── Config detection ──────────────────────────────────────────────────────────
# Detects whether auditd collector is active by parsing config.yaml.
# Logic:
#   1. If collectors.enabled is defined and NON-EMPTY → only those are active
#   2. If collectors.enabled is EMPTY or UNDEFINED:
#      - If collectors.disabled is defined and contains 'auditd' → NOT active
#      - Otherwise (empty or auditd NOT in disabled) → ACTIVE (default: all active)
AUDITD_ACTIVE="yes"   # Default: all collectors active
CONFIG_CHECKED="no"

detect_auditd_status() {
    local config_path="${1:-${CONFIG_FILE}}"

    # Try to find config.yaml in dev mode too
    if [[ ! -f "${config_path}" ]]; then
        if [[ -f "./config.yaml" ]]; then
            config_path="./config.yaml"
        elif [[ -f "${INSTALL_DIR}/config.yaml" ]]; then
            config_path="${INSTALL_DIR}/config.yaml"
        fi
    fi

    if [[ ! -f "${config_path}" ]]; then
        # Config not found — use default (all active, including auditd)
        add_warning "config.yaml not found at ${CONFIG_FILE} — assuming auditd is active"
        CONFIG_CHECKED="no"
        return
    fi

    CONFIG_CHECKED="yes"

    # Use python3 to parse YAML reliably
    if command -v python3 >/dev/null 2>&1; then
        local enabled_val disabled_val
        enabled_val=$(python3 -c "
import yaml, sys
try:
    with open('${config_path}') as f:
        config = yaml.safe_load(f) or {}
    collectors = config.get('collectors', {})
    enabled = collectors.get('enabled', None)
    disabled = collectors.get('disabled', None)
    # Empty list in YAML is None when not present, or [] when explicit
    print(repr(enabled))
    print(repr(disabled))
except Exception as e:
    print('ERROR:' + str(e), file=sys.stderr)
    sys.exit(1)
" 2>/dev/null || echo "ERROR")

        if [[ "${enabled_val}" == ERROR:* ]]; then
            add_warning "Could not parse config.yaml — assuming auditd is active"
            AUDITD_ACTIVE="yes"
            return
        fi

        # Parse the two-line output
        local enabled_line disabled_line
        enabled_line=$(echo "${enabled_val}" | head -1)
        disabled_line=$(echo "${enabled_val}" | tail -1)

        # If enabled is a non-empty list → whitelist mode
        if [[ "${enabled_line}" == \[*\] ]] && [[ "${enabled_line}" != "[]" ]]; then
            # Whitelist mode: check if 'auditd' is explicitly listed
            if echo "${enabled_val}" | grep -q "'auditd'"; then
                AUDITD_ACTIVE="yes"
            else
                AUDITD_ACTIVE="no"
            fi
        else
            # Blacklist/default mode: check if 'auditd' is in disabled list
            if [[ "${disabled_line}" == \[*\] ]] && echo "${disabled_line}" | grep -q "'auditd'"; then
                AUDITD_ACTIVE="no"
            else
                AUDITD_ACTIVE="yes"
            fi
        fi
    else
        # Fallback: simple grep-based detection (less reliable)
        add_warning "python3 not available — using fallback YAML detection"
        if grep -q "^\s*enabled:" "${config_path}" 2>/dev/null; then
            # Check if auditd appears in the enabled list
            if grep -A 20 "^\s*enabled:" "${config_path}" 2>/dev/null | grep -qE "^\s+-\s+auditd"; then
                AUDITD_ACTIVE="yes"
            else
                AUDITD_ACTIVE="no"
            fi
        else
            # Check disabled list
            if grep -qE "^\s*disabled:" "${config_path}" 2>/dev/null && \
               grep -A 20 "^\s*disabled:" "${config_path}" 2>/dev/null | grep -qE "^\s+-\s+auditd"; then
                AUDITD_ACTIVE="no"
            else
                AUDITD_ACTIVE="yes"
            fi
        fi
    fi
}

# ── Package checking ───────────────────────────────────────────────────────────
check_package() {
    local pkg="$1"
    local required="${2:-yes}"

    if dpkg -s "${pkg}" >/dev/null 2>&1; then
        return 0
    else
        if [[ "${required}" == "yes" ]]; then
            add_missing_package "${pkg}"
            return 1
        else
            add_warning "Optional package '${pkg}' not installed"
            return 0
        fi
    fi
}

# ── Tool checking ─────────────────────────────────────────────────────────────
check_tool() {
    local tool="$1"
    local reason="$2"

    if command -v "${tool}" >/dev/null 2>&1; then
        return 0
    else
        add_missing_tool "${tool} (${reason})"
        return 1
    fi
}

# ── Main check ───────────────────────────────────────────────────────────────────
main() {
    local total_errors=0
    local total_warnings=0

    echo ""
    echo -e "${BOLD}═══════════════════════════════════════════════════════════════════${NC}"
    echo -e "${BOLD}  AICollector — Dependency Checker${NC}"
    echo -e "${BOLD}═══════════════════════════════════════════════════════════════════${NC}"
    echo ""

    if [[ "${DRY_RUN}" == "yes" ]]; then
        echo -e "${CYAN}MODE: DRY-RUN (no changes will be made)${NC}"
        echo ""
    fi

    # ── Step 1: Detect auditd activation ───────────────────────────────────────
    echo -e "${BOLD}[1/3] Detecting collector activation status...${NC}"

    if [[ "${MANDATORY_ONLY}" == "yes" ]]; then
        AUDITD_ACTIVE="no"
        echo -e "${YELLOW}  --mandatory-only specified: auditd check skipped${NC}"
        CONFIG_CHECKED="forced_skip"
    else
        detect_auditd_status
    fi

    if [[ "${AUDITD_ACTIVE}" == "yes" ]]; then
        echo -e "${GREEN}  ✓ Collecteur 'auditd' is ACTIVE — will verify auditd dependencies${NC}"
    else
        echo -e "${YELLOW}  ✓ Collecteur 'auditd' is DISABLED — auditd check skipped${NC}"
    fi
    echo ""

    # ── Step 2: Check system packages ──────────────────────────────────────────
    echo -e "${BOLD}[2/3] Checking system packages...${NC}"

    # Mandatory packages (always required)
    local mandatory_pkgs=(
        "iproute2"
        "smartmontools"
        "openssl"
        "cron"
        "systemd"
    )

    local pkg_status=0
    for pkg in "${mandatory_pkgs[@]}"; do
        if check_package "${pkg}"; then
            echo -e "  ${GREEN}✓${NC} ${pkg}"
        else
            echo -e "  ${RED}✗${NC} ${pkg} — MISSING"
            ((pkg_status++))
        fi
    done

    # Conditional: auditd (only if collector is active)
    if [[ "${AUDITD_ACTIVE}" == "yes" ]]; then
        if check_package "auditd"; then
            echo -e "  ${GREEN}✓${NC} auditd (conditional — collector active)"
        else
            echo -e "  ${RED}✗${NC} auditd — MISSING (required: collector 'auditd' is active in config.yaml)"
            ((pkg_status++))
        fi
    else
        echo -e "  ${CYAN}—${NC} auditd — SKIPPED (collector 'auditd' is disabled in config.yaml)"
    fi

    echo ""
    ((total_errors += pkg_status))

    # ── Step 3: Check command-line tools ────────────────────────────────────────
    echo -e "${BOLD}[3/3] Checking command-line tools...${NC}"

    local tool_status=0

    # Always required tools
    local mandatory_tools=(
        "ip:iproute2"
        "ss:iproute2"
        "smartctl:smartmontools"
        "openssl:openssl"
        "lsblk:util-linux"
        "df:coreutils"
        "nproc:coreutils"
        "hostname:hostname"
        "uname:coreutils"
        "grep:grep"
        "awk:gawk"
        "sort:coreutils"
        "cut:coreutils"
        "wc:coreutils"
        "stat:coreutils"
        "id:coreutils"
        "uptime:procps"
        "free:procps"
        "ps:procps"
        "mount:util-linux"
    )

    for entry in "${mandatory_tools[@]}"; do
        local tool="${entry%%:*}"
        local reason="${entry##*:}"
        if check_tool "${tool}" "${reason}"; then
            echo -e "  ${GREEN}✓${NC} ${tool}"
        else
            echo -e "  ${RED}✗${NC} ${tool} — MISSING (package: ${reason})"
            ((tool_status++))
        fi
    done

    # Conditional: auditctl (only if collector is active)
    if [[ "${AUDITD_ACTIVE}" == "yes" ]]; then
        if check_tool "auditctl" "auditd"; then
            echo -e "  ${GREEN}✓${NC} auditctl (conditional — collector active)"
        else
            echo -e "  ${RED}✗${NC} auditctl — MISSING (package: auditd — required: collector 'auditd' is active)"
            ((tool_status++))
        fi
    else
        echo -e "  ${CYAN}—${NC} auditctl — SKIPPED (collector 'auditd' is disabled)"
    fi

    echo ""
    ((total_errors += tool_status))

    # ── Python check ─────────────────────────────────────────────────────────────
    echo -e "${BOLD}[Bonus] Checking Python...${NC}"

    if command -v python3 >/dev/null 2>&1; then
        local py_version
        py_version=$(python3 -c "import sys; print(sys.version_info[0])" 2>/dev/null)
        local py_major py_minor
        py_major=$(python3 -c "import sys; print(sys.version_info[0])" 2>/dev/null)
        py_minor=$(python3 -c "import sys; print(sys.version_info[1])" 2>/dev/null)
        echo -e "  ${GREEN}✓${NC} python3 version: ${py_major}.${py_minor}"

        if [[ "${py_major}" -lt 3 ]] || [[ "${py_major}" -eq 3 && "${py_minor}" -lt 12 ]]; then
            add_warning "Python >= 3.12 recommended, found ${py_major}.${py_minor}"
            echo -e "  ${YELLOW}!${NC} Python ${py_major}.${py_minor} detected (>= 3.12 recommended)"
            ((total_warnings++))
        fi
    else
        echo -e "  ${RED}✗${NC} python3 — MISSING"
        add_warning "python3 not found"
        ((total_errors++))
    fi

    echo ""
    echo -e "${BOLD}═══════════════════════════════════════════════════════════════════${NC}"

    # ── Summary ─────────────────────────────────────────────────────────────────
    if [[ "${JSON_OUTPUT}" == "yes" ]]; then
        print_json
        if [[ ${total_errors} -gt 0 ]]; then
            exit 1
        fi
        exit 0
    fi

    if [[ ${total_errors} -gt 0 ]]; then
        echo -e "${RED}${BOLD}RESULT: ${total_errors} dependency issue(s) found${NC}${BOLD}${NC}"
        echo ""
        echo "Missing packages can be installed with:"
        echo -e "  ${CYAN}sudo apt install -y ${MISSING_PACKAGES[*]}${NC}"
        echo ""
        echo "Missing tools indicate packages that need to be installed."
        echo ""
        exit 1
    fi

    if [[ ${total_warnings} -gt 0 ]]; then
        echo -e "${YELLOW}${BOLD}RESULT: All dependencies satisfied (${total_warnings} warning(s))${NC}${BOLD}${NC}"
        echo ""
        exit 0
    fi

    echo -e "${GREEN}${BOLD}RESULT: All dependencies satisfied ✓${NC}${BOLD}${NC}"
    echo ""
    exit 0
}

main "$@"
