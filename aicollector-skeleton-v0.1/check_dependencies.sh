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
#
# Exit codes:
#   0 = All required dependencies satisfied
#   1 = Missing dependencies found
#   2 = Invalid command-line arguments
#   3 = Cannot read config.yaml
# =============================================================================

set -eo pipefail

# ── Constants ─────────────────────────────────────────────────────────────────
readonly APP_NAME="aicollector"
readonly CONFIG_FILE="/etc/aicollector/config.yaml"
readonly INSTALL_DIR="/opt/aicollector"

# ── Colour helpers ─────────────────────────────────────────────────────────────
_colour_setup() {
  if [[ ! -t 1 ]]; then
    C_R=""; C_G=""; C_Y=""; C_C=""; C_B=""; C_N=""
  else
    C_R=$'\033[0;31m'; C_G=$'\033[0;32m'; C_Y=$'\033[0;33m'
    C_C=$'\033[0;36m'; C_B=$'\033[1m';  C_N=$'\033[0m'
  fi
}
_colour_setup

# ── Runtime state ──────────────────────────────────────────────────────────────
AUDITD_MODE="${AUDITD_MODE:-auto}"
MODE="normal"

# ── CLI parsing ───────────────────────────────────────────────────────────────
usage() {
  grep -E '^# -{2}' "$0" | sed 's/^# //'
  exit 0
}

while [[ $# -gt 0 ]]; do
  case "${1}" in
    --mandatory-only) AUDITD_MODE="skip" ;;
    --json)           MODE="json" ;;
    --dry-run)        MODE="dry-run" ;;
    --help)           usage ;;
    *)
      echo "Unknown option: ${1}" >&2
      echo "Run with --help for usage." >&2
      exit 2 ;;
  esac
  shift
done

# ─────────────────────────────────────────────────────────────────────────────
# JSON subcommand trap
# Invoked as:  python3 "$0" __json_emit__ "$@"
# Runs in a completely fresh Python subprocess — NO recursive bash call.
# ─────────────────────────────────────────────────────────────────────────────
_json_emit_python() {
  exec python3 - "$@" << 'PYEOF'
import sys, json
args = sys.argv[1:]
status   = args[0]
total    = int(args[1])
passed   = int(args[2])
failed   = int(args[3])
warnings = int(args[4])
msgs     = [ln for ln in sys.stdin.read().splitlines() if ln]
obj = {
    "status":   status,
    "summary":  {"total": total, "passed": passed,
                 "failed": failed, "warnings": warnings},
    "messages": msgs,
}
print(json.dumps(obj, ensure_ascii=False, indent=2))
PYEOF
}

if [[ "${1:-}" == "__json_emit__" ]]; then
  shift
  _json_emit_python "$@"
  # unreachable
fi

# ── Output helpers ─────────────────────────────────────────────────────────────
sect() {
  local _head="$1"
  [[ "${MODE}" == "json" ]] && return
  echo ""
  echo "${C_B}▶ ${_head}${C_N}"
}
say()  { [[ "${MODE}" == "json" ]] || echo -n "  $*"; }
sayln(){ [[ "${MODE}" == "json" ]] || echo "  $*"; }
pass() { sayln "${C_G}✔${C_N} $*"; }
fail() { sayln "${C_R}✘${C_N} $*"; }
warn() { sayln "${C_Y}⚠${C_N} $*"; }

# ── Auditd config reader ────────────────────────────────────────────────────────
_config_auditd_enabled() {
  local cfg="${1}"
  if [[ ! -f "${cfg}" ]]; then
    return 3
  fi
  if ! command -v python3 > /dev/null 2>&1; then
    if grep -Eo 'name\s*:\s*auditd' "${cfg}" 2>/dev/null | grep -qi 'enabled.*true'; then
      printf 'true'; return 0
    fi
    printf 'false'; return 0
  fi
  local raw es
  raw=$(python3 -c "
import sys, yaml
try:
    with open('${cfg}', 'r') as fh:
        data = yaml.safe_load(fh) or {}
    collectors = data.get('collectors', []) or []
    enabled = any(
        c.get('name', '').lower() == 'auditd'
        for c in collectors
    )
    print('true' if enabled else 'false')
except Exception:
    sys.exit(2)
" 2>/dev/null); es=$?
  case "${es}.${raw}" in
    0.true)  printf 'true';  return 0 ;;
    *)       printf 'false'; return 0 ;;
  esac
}

# ── Single check helper ────────────────────────────────────────────────────────
has_command() { command -v "${1}" > /dev/null 2>&1; }

one_check() {
  local label="${1}" cmd="${2}" hint="${3}"
  if has_command "${cmd}"; then
    [[ "${MODE}" == "json" ]] || pass "${label}"
    return 0
  fi
  [[ "${MODE}" == "json" ]] || fail "${label}"
  if [[ "${MODE}" == "dry-run" && -n "${hint}" ]]; then
    say   "  -> install: ${hint}"
  fi
  return 1
}

python_import_ok() { python3 -c "import ${1}" 2>/dev/null; }

# ── Dependency groups ──────────────────────────────────────────────────────────
check_mandatory_deps() {
  sect "Mandatory dependencies"
  local -i rc=0
  one_check "Python 3.12+" "python3" "sudo apt-get install python3-full" || ((rc++))
  if python_import_ok pydantic; then
    [[ "${MODE}" == "json" ]] || pass "pydantic"
  else
    [[ "${MODE}" == "json" ]] || fail "pydantic"
    [[ "${MODE}" == "dry-run" ]] && say "  -> install: pip install pydantic"
    ((rc++))
  fi
  if python_import_ok yaml; then
    [[ "${MODE}" == "json" ]] || pass "PyYAML"
  else
    [[ "${MODE}" == "json" ]] || fail "PyYAML"
    [[ "${MODE}" == "dry-run" ]] && say "  -> install: pip install pyyaml"
    ((rc++))
  fi
  return ${rc}
}

check_system_collectors() {
  sect "System collectors"
  local -i rc=0
  one_check "systemctl (systemd)" "systemctl" "sudo apt-get install systemd" || ((rc++))
  one_check "ps" "ps" "sudo apt-get install procps" || ((rc++))
  one_check "df" "df" "sudo apt-get install coreutils" || ((rc++))
  one_check "free" "free" "sudo apt-get install procps" || ((rc++))
  return ${rc}
}

check_docker_collector() {
  sect "Docker collector"
  if has_command docker; then
    [[ "${MODE}" == "json" ]] || pass "docker"
    return 0
  fi
  [[ "${MODE}" == "json" ]] || warn "docker (optional)"
  return 0
}

check_auditd_collector() {
  sect "Auditd collector"
  if has_command auditctl; then
    [[ "${MODE}" == "json" ]] || pass "auditd (optional)"
    return 0
  fi
  [[ "${MODE}" == "json" ]] || warn "auditd (optional -- install: sudo apt-get install auditd)"
  return 0
}

check_performance_tools() {
  sect "Performance & monitoring"
  local -i rc=0
  one_check "top" "top" "sudo apt-get install procps" || ((rc++))
  one_check "ip" "ip" "sudo apt-get install iproute2" || ((rc++))
  one_check "journalctl" "journalctl" "sudo apt-get install systemd" || ((rc++))
  return ${rc}
}

check_installation_dirs() {
  sect "Installation directories"
  if [[ -d "${INSTALL_DIR}" ]]; then
    [[ "${MODE}" == "json" ]] || pass "Install dir: ${INSTALL_DIR}"
    return 0
  fi
  [[ "${MODE}" == "json" ]] || warn "Install dir missing: ${INSTALL_DIR} (created by install.sh)"
  return 0
}

# ── Main ────────────────────────────────────────────────────────────────────────
main() {
  local auditd_enabled cfg_status
  local -i rc_mand=0 rc_sys=0 rc_dock=0 rc_audit=0 rc_perf=0 rc_dirs=0
  local -i total=0 passed=0 failed=0 warnings=0

  if [[ "${AUDITD_MODE}" == "auto" ]]; then
    auditd_enabled="$(_config_auditd_enabled "${CONFIG_FILE}")" || cfg_status=$?
    [[ ${cfg_status:-0} -eq 3 ]] && auditd_enabled="false"
  elif [[ "${AUDITD_MODE}" == "force" ]]; then
    auditd_enabled="true"
  else
    auditd_enabled="false"
  fi

  if [[ "${MODE}" != "json" ]]; then
    sayln "Mode  : ${MODE} | Auditd auto-detect: ${auditd_enabled}"
    sayln ""
  fi

  set +e; check_mandatory_deps;    rc_mand=$?; set -e
  set +e; check_system_collectors;  rc_sys=$?;  set -e
  set +e; check_docker_collector;   rc_dock=$?; set -e
  set +e; check_auditd_collector;  rc_audit=$?; set -e
  set +e; check_performance_tools; rc_perf=$?; set -e
  set +e; check_installation_dirs;  rc_dirs=$?; set -e

  total=11
  failed=$(( rc_mand + rc_sys + rc_dock + rc_audit + rc_perf ))
  passed=$(( total - failed ))
  warnings=${rc_dirs}

  if [[ "${MODE}" == "json" ]]; then
    _json_emit_python \
      "$([ ${failed} -eq 0 ] && echo passed || echo failed)" \
      "${total}" "${passed}" "${failed}" "${warnings}" \
      "Python3: $(has_command python3 && echo OK || echo MISSING)" \
      "pydantic: $(python_import_ok pydantic && echo OK || echo MISSING)" \
      "PyYAML: $(python_import_ok yaml && echo OK || echo MISSING)" \
      "systemctl: $(has_command systemctl && echo OK || echo MISSING)" \
      "ps: $(has_command ps && echo OK || echo MISSING)" \
      "df: $(has_command df && echo OK || echo MISSING)" \
      "free: $(has_command free && echo OK || echo MISSING)" \
      "Docker: $(has_command docker && echo OK || echo NOT_FOUND)" \
      "Auditd: $(has_command auditctl && echo OK || echo NOT_FOUND)" \
      "top: $(has_command top && echo OK || echo MISSING)" \
      "ip: $(has_command ip && echo OK || echo MISSING)" \
      "journalctl: $(has_command journalctl && echo OK || echo MISSING)"
    exit $?
  fi

  sayln ""
  if [[ ${failed} -eq 0 ]]; then
    sayln "${C_G}All mandatory dependencies satisfied.${C_N}"
    exit 0
  else
    sayln "Run with --dry-run to see install commands."
    exit 1
  fi
}

main "$@"

