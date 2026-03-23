#!/usr/bin/env bash
# ============================================================================
#  praxis — Uninstaller for Claude Code
#
#  Usage:
#    bash uninstall.sh    # Remove praxis skill
#
# ============================================================================

set -euo pipefail

# ── Colors ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

info()    { echo -e "${CYAN}[INFO]${NC} $*"; }
success() { echo -e "${GREEN}[OK]${NC} $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*"; }

# ── Configuration ───────────────────────────────────────────────────────────
PLUGIN_NAME="praxis"
TARGET_DIR="${HOME}/.claude/skills/${PLUGIN_NAME}"

# ── Main ────────────────────────────────────────────────────────────────────
main() {
    echo ""
    echo -e "${BOLD}╔══════════════════════════════════════════════╗${NC}"
    echo -e "${BOLD}║   Praxis uninstaller                     ║${NC}"
    echo -e "${BOLD}╚══════════════════════════════════════════════╝${NC}"
    echo ""

    if [[ ! -d "${TARGET_DIR}" ]]; then
        error "Praxis is not installed at ${TARGET_DIR}"
        exit 1
    fi

    echo -e "About to remove: ${BOLD}${PLUGIN_NAME}${NC}"
    echo -e "  Path: ${TARGET_DIR}"
    echo ""

    read -rp "Proceed with uninstall? [y/N] " answer
    if [[ ! "${answer}" =~ ^[Yy]$ ]]; then
        info "Uninstall cancelled."
        exit 0
    fi

    # ── Clean up CLAUDE.md praxis injection ──────────────────────────────────
    CLAUDE_MD="${HOME}/.claude/CLAUDE.md"
    if [[ -f "${CLAUDE_MD}" ]]; then
        if grep -q '<!-- praxis-auto -->' "${CLAUDE_MD}"; then
            sed -i '' '/<!-- praxis-auto -->/,/<!-- \/praxis-auto -->/d' "${CLAUDE_MD}"
            # Remove file if empty (only whitespace left)
            if [[ ! -s "${CLAUDE_MD}" ]] || [[ -z "$(tr -d '[:space:]' < "${CLAUDE_MD}")" ]]; then
                rm -f "${CLAUDE_MD}"
                success "Removed empty ${CLAUDE_MD}"
            else
                success "Cleaned praxis block from ${CLAUDE_MD}"
            fi
        else
            info "No praxis block found in ${CLAUDE_MD}, skipping"
        fi
    else
        info "${CLAUDE_MD} not found, skipping"
    fi

    # ── Clean up settings.json praxis hooks ────────────────────────────────
    SETTINGS_JSON="${HOME}/.claude/settings.json"
    if [[ -f "${SETTINGS_JSON}" ]]; then
        output=$(python3 -c "
import json, sys

path = sys.argv[1]
with open(path, 'r') as f:
    data = json.load(f)

changed = False
for key in list(data.keys()):
    if isinstance(data[key], dict):
        for event in list(data[key].keys()):
            if isinstance(data[key][event], list):
                original = data[key][event]
                filtered = [h for h in original if not (isinstance(h, dict) and 'command' in h and 'skills/praxis/' in h['command'])]
                if len(filtered) != len(original):
                    data[key][event] = filtered
                    changed = True

if changed:
    with open(path, 'w') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write('\n')
print('CLEANED' if changed else 'NO_PRAXIS_HOOKS')
" "${SETTINGS_JSON}" 2>&1)
        if [[ "${output}" == "CLEANED" ]]; then
            success "Removed praxis hooks from ${SETTINGS_JSON}"
        elif [[ "${output}" == "NO_PRAXIS_HOOKS" ]]; then
            info "No praxis hooks found in ${SETTINGS_JSON}, skipping"
        else
            warn "Failed to clean ${SETTINGS_JSON}: ${output}"
        fi
    else
        info "${SETTINGS_JSON} not found, skipping"
    fi

    # ── Remove skill directory ─────────────────────────────────────────────
    rm -rf "${TARGET_DIR}"
    success "Removed ${TARGET_DIR}"

    echo ""
    success "Uninstall complete."
    echo ""
}

main "$@"
