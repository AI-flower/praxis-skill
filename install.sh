#!/usr/bin/env bash
# ============================================================================
#  praxis — One-click installer for Claude Code
#
#  Usage:
#    bash install.sh            # Install / upgrade
#    bash install.sh --check    # Check current installation status
#
#  What it does:
#    1. Copies plugin files to ~/.claude/skills/praxis/
#    2. Sets correct file permissions
#    3. Verifies installation
#
#  Requirements: bash 4+, python3
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
PLUGIN_VERSION="0.4.4"

# Source: where install.sh lives (the repo/distribution directory)
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Target: Claude Code skills directory (correct path for skill discovery)
TARGET_DIR="${HOME}/.claude/skills/${PLUGIN_NAME}"

# ── Pre-flight checks ──────────────────────────────────────────────────────
preflight() {
    # Check bash version
    if [[ "${BASH_VERSINFO[0]}" -lt 4 ]]; then
        warn "Bash 4+ recommended (you have ${BASH_VERSION}). Proceeding anyway..."
    fi

    # Check python3
    if ! command -v python3 &>/dev/null; then
        error "python3 is required (used by scripts/report.py)."
        error "Install Python 3 and try again."
        exit 1
    fi

    # Check source files exist
    if [[ ! -f "${SOURCE_DIR}/SKILL.md" ]]; then
        error "SKILL.md not found in ${SOURCE_DIR}"
        error "Run this script from the praxis directory."
        exit 1
    fi

    if [[ ! -d "${SOURCE_DIR}/scripts" ]]; then
        error "scripts/ directory not found in ${SOURCE_DIR}"
        exit 1
    fi

    # Check Claude Code directory exists
    if [[ ! -d "${HOME}/.claude" ]]; then
        warn "${HOME}/.claude does not exist. Creating it..."
        mkdir -p "${HOME}/.claude"
    fi
}

# ── Check mode ──────────────────────────────────────────────────────────────
check_installation() {
    echo -e "${BOLD}=== Praxis Installation Status ===${NC}"
    echo ""

    # Check plugin directory
    if [[ -d "${TARGET_DIR}" ]]; then
        success "Plugin directory exists: ${TARGET_DIR}"

        if [[ -f "${TARGET_DIR}/SKILL.md" ]]; then
            local ver
            ver=$(grep -o 'version: [0-9.]*' "${TARGET_DIR}/SKILL.md" 2>/dev/null | head -1 | awk '{print $2}')
            success "SKILL.md found (version: ${ver:-unknown})"
        else
            error "SKILL.md missing"
        fi

        if [[ -f "${TARGET_DIR}/scripts/report.py" ]]; then
            success "scripts/report.py found"
        else
            error "scripts/report.py missing"
        fi

        if [[ -d "${TARGET_DIR}/references" ]]; then
            success "references/ directory found"
        else
            warn "references/ directory missing"
        fi

        if [[ -d "${TARGET_DIR}/templates" ]]; then
            success "templates/ directory found"
        else
            warn "templates/ directory missing"
        fi

        if [[ -f "${TARGET_DIR}/.claude-plugin/plugin.json" ]]; then
            success ".claude-plugin/plugin.json found"
        else
            error ".claude-plugin/plugin.json missing"
        fi
    else
        error "Plugin directory not found: ${TARGET_DIR}"
    fi

    echo ""
}

# ── Copy plugin files ───────────────────────────────────────────────────────
copy_files() {
    info "Copying plugin files to ${TARGET_DIR} ..."

    # Create target directory structure
    mkdir -p "${TARGET_DIR}/scripts"
    mkdir -p "${TARGET_DIR}/references"
    mkdir -p "${TARGET_DIR}/templates"

    # Copy core files
    cp "${SOURCE_DIR}/SKILL.md" "${TARGET_DIR}/SKILL.md"
    cp "${SOURCE_DIR}/skills.json" "${TARGET_DIR}/skills.json"
    cp "${SOURCE_DIR}/install.sh" "${TARGET_DIR}/install.sh"
    cp "${SOURCE_DIR}/uninstall.sh" "${TARGET_DIR}/uninstall.sh"

    # Copy scripts
    for f in "${SOURCE_DIR}/scripts/"*; do
        [[ -f "$f" ]] && cp "$f" "${TARGET_DIR}/scripts/"
    done

    # Copy references
    for f in "${SOURCE_DIR}/references/"*; do
        [[ -f "$f" ]] && cp "$f" "${TARGET_DIR}/references/"
    done

    # Copy templates
    if [[ -d "${SOURCE_DIR}/templates" ]]; then
        for f in "${SOURCE_DIR}/templates/"*; do
            [[ -f "$f" ]] && cp "$f" "${TARGET_DIR}/templates/"
        done
    fi

    # Set permissions
    chmod +x "${TARGET_DIR}/scripts/"*.sh 2>/dev/null || true
    chmod +x "${TARGET_DIR}/scripts/"*.py 2>/dev/null || true
    chmod +x "${TARGET_DIR}/install.sh"
    chmod +x "${TARGET_DIR}/uninstall.sh"

    success "Plugin files copied."
}

# ── Handle old version cleanup ──────────────────────────────────────────────
cleanup_old_versions() {
    # Clean up legacy plugins/cache/local path if it exists
    local legacy_dir="${HOME}/.claude/plugins/cache/local/${PLUGIN_NAME}"
    if [[ -d "${legacy_dir}" ]]; then
        warn "Found legacy install at ${legacy_dir}, removing..."
        rm -rf "${legacy_dir}"
        success "Removed legacy install."
    fi
    return
    # (version subdirectory logic no longer needed — skills/ is flat)
    local cache_dir="${HOME}/.claude/skills/${PLUGIN_NAME}"
    if [[ ! -d "${cache_dir}" ]]; then
        return
    fi

    for version_dir in "${cache_dir}"/*/; do
        local dir_version
        dir_version=$(basename "${version_dir}")

        # Skip current version
        if [[ "${dir_version}" == "${PLUGIN_VERSION}" ]]; then
            continue
        fi

        # Skip if not a version-like directory
        if [[ ! "${dir_version}" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
            continue
        fi

        warn "Found old version: ${dir_version}"
        read -rp "  Remove old version ${dir_version}? [y/N] " answer
        if [[ "${answer}" =~ ^[Yy]$ ]]; then
            rm -rf "${version_dir}"
            success "  Removed ${dir_version}"
        else
            info "  Kept ${dir_version}"
        fi
    done
}

# ── Inject CLAUDE.md auto-trigger ──────────────────────────────────────────
inject_claude_md() {
    local claude_md="${HOME}/.claude/CLAUDE.md"
    local marker_start="<!-- praxis-auto -->"
    local marker_end="<!-- /praxis-auto -->"

    info "Injecting auto-trigger config into CLAUDE.md ..."

    # Ensure directory exists
    mkdir -p "$(dirname "${claude_md}")"

    # The content to inject (between markers, inclusive)
    local inject_content
    inject_content=$(cat <<'BLOCK'
<!-- praxis-auto -->
## AI Praxis (auto-active)

When the user describes a task or need in natural language (not simple code questions or git commands),
automatically follow the praxis workflow:

**Before triggering, check if praxis is enabled:**
Run `python3 ~/.claude/skills/praxis/scripts/report.py status` (or read `~/.ai-praxis/config.json`).
- If output is `DISABLED` or config has `"skill_enabled": false` → skip praxis, respond normally as Claude Code.
- If output is `ENABLED` or key is missing/true → proceed with auto-trigger as usual.

**Disable/enable commands** (user can say these at any time):
- "禁用 ai-praxis" / "关掉自动执行" / "disable praxis" → run `python3 ~/.claude/skills/praxis/scripts/report.py disable`
- "启用 ai-praxis" / "开启自动执行" / "enable praxis" → run `python3 ~/.claude/skills/praxis/scripts/report.py enable`

1. **Detect**: If the user's message describes a task that requires **multiple steps to complete**
   (analysis → planning → implementation → verification), invoke the praxis skill by running:
   use the Skill tool with skill "praxis" and the user's message as args.
   This includes: creating, fixing, optimizing, deploying, migrating, refactoring, building, designing, etc.
2. **Skip**: If the task can be done in **one simple action** (a single command, a quick answer, a short explanation),
   respond normally without invoking the skill.

**Quick rule**: Does the task need a plan? → trigger praxis. Can you just do it? → do it directly.

Examples that SHOULD auto-trigger praxis:
- "帮我生成一个宣传片" → trigger (multi-step creation)
- "帮我写一个爬虫" → trigger (multi-step development)
- "做一个数据分析报告" → trigger (multi-step analysis)
- "help me create a landing page" → trigger (multi-step design+code)
- "修复 praxis 的 bug" → trigger (analysis → design → implementation → test)
- "优化这个系统的性能" → trigger (profiling → planning → implementation)
- "帮我部署到生产环境" → trigger (multi-step deployment)
- "把这个项目从 JS 迁移到 TS" → trigger (multi-step migration)

Examples that should NOT trigger:
- "这段代码有什么问题" → normal (single-step review/explanation)
- "git status" → normal (single command)
- "解释一下这个函数" → normal (single-step explanation)
- "把这个变量名改成驼峰" → normal (trivial single-step fix)
- "帮我看看这个报错" → normal (single-step diagnosis)
<!-- /praxis-auto -->
BLOCK
)

    if [[ ! -f "${claude_md}" ]]; then
        # File doesn't exist — create with injected content
        echo "${inject_content}" > "${claude_md}"
        success "Created ${claude_md} with auto-trigger config."
    elif grep -qF "${marker_start}" "${claude_md}"; then
        # Marker exists — replace the block (supports upgrade)
        python3 -c "
import re, sys
p = sys.argv[1]
with open(p, 'r') as f:
    text = f.read()
new_block = sys.argv[2]
text = re.sub(r'<!-- praxis-auto -->.*?<!-- /praxis-auto -->', new_block, text, flags=re.DOTALL)
with open(p, 'w') as f:
    f.write(text)
" "${claude_md}" "${inject_content}"
        success "Updated existing auto-trigger block in CLAUDE.md."
    else
        # File exists but no marker — append
        echo "" >> "${claude_md}"
        echo "${inject_content}" >> "${claude_md}"
        success "Appended auto-trigger config to CLAUDE.md."
    fi
}

# ── Register hooks in settings.json ───────────────────────────────────────
register_hooks() {
    info "Registering hooks in settings.json ..."

    python3 - "${TARGET_DIR}" <<'PYEOF'
import json, sys
from pathlib import Path

target_dir = sys.argv[1]
settings_file = Path.home() / ".claude" / "settings.json"

# Load existing or create empty
settings = {}
if settings_file.exists():
    try:
        settings = json.loads(settings_file.read_text())
    except:
        settings = {}

hooks = settings.setdefault("hooks", {})

# Define hooks to register
praxis_hooks = [
    {
        "event": "Stop",
        "entry": {"hooks": [{"type": "command", "command": f"python3 {target_dir}/scripts/hook_post_skill.py", "timeout": 30}]},
    },
    {
        "event": "PostToolUse",
        "entry": {"matcher": "Skill", "hooks": [{"type": "command", "command": f"python3 {target_dir}/scripts/hook_post_skill.py", "timeout": 15}]},
    },
    {
        "event": "UserPromptSubmit",
        "entry": {"hooks": [{"type": "command", "command": f"python3 {target_dir}/scripts/hook_user_prompt.py", "timeout": 5}]},
    },
]

for ph in praxis_hooks:
    event = ph["event"]
    entry = ph["entry"]
    cmd = entry["hooks"][0]["command"]

    event_hooks = hooks.setdefault(event, [])
    # Check if already registered (by command string)
    already = any(
        cmd in h.get("hooks", [{}])[0].get("command", "")
        for h in event_hooks
        if h.get("hooks")
    )
    if not already:
        event_hooks.append(entry)

settings_file.write_text(json.dumps(settings, indent=2, ensure_ascii=False))
print("HOOKS_REGISTERED")
PYEOF

    if [[ $? -eq 0 ]]; then
        success "Hooks registered in settings.json."
    else
        warn "Failed to register hooks. You may need to add them manually."
    fi
}

# ── Install PUA skill (dependency) ─────────────────────────────────────────
install_pua_skill() {
    local pua_dir="${HOME}/.claude/skills/pua"

    if [[ -d "${pua_dir}" && -f "${pua_dir}/skills/pua/SKILL.md" ]]; then
        success "PUA skill already installed at ${pua_dir}"
        return 0
    fi

    info "Installing PUA skill (anti-give-up methodology)..."

    # Try git clone first
    if command -v git &>/dev/null; then
        if git clone --depth 1 https://github.com/tanweai/pua.git "${pua_dir}" &>/dev/null; then
            success "PUA skill installed via git clone."
            return 0
        else
            warn "git clone failed, trying curl fallback..."
        fi
    fi

    # Fallback: download SKILL.md via curl
    if command -v curl &>/dev/null; then
        mkdir -p "${pua_dir}/skills/pua"
        if curl -fsSL -o "${pua_dir}/skills/pua/SKILL.md" \
            "https://raw.githubusercontent.com/tanweai/pua/main/skills/pua/SKILL.md" 2>/dev/null; then
            success "PUA skill installed via curl (SKILL.md only)."
            return 0
        fi
    fi

    warn "Could not install PUA skill automatically. Praxis will use built-in methodology."
    return 0
}

# ── Main ────────────────────────────────────────────────────────────────────
main() {
    echo ""
    echo -e "${BOLD}╔══════════════════════════════════════════════╗${NC}"
    echo -e "${BOLD}║   Praxis installer v${PLUGIN_VERSION}              ║${NC}"
    echo -e "${BOLD}╚══════════════════════════════════════════════╝${NC}"
    echo ""

    # Check mode
    if [[ "${1:-}" == "--check" ]]; then
        check_installation
        exit 0
    fi

    # Pre-flight
    preflight

    # Check for existing installation
    if [[ -d "${TARGET_DIR}" ]]; then
        warn "Existing installation found at ${TARGET_DIR}"
        read -rp "Overwrite? [Y/n] " answer
        if [[ "${answer}" =~ ^[Nn]$ ]]; then
            info "Installation cancelled."
            exit 0
        fi
    fi

    # Clean up old versions
    cleanup_old_versions

    # Copy files
    copy_files

    # Inject CLAUDE.md auto-trigger
    inject_claude_md

    # Register hooks
    register_hooks

    # Install PUA skill (dependency)
    install_pua_skill

    # Done
    echo ""
    echo -e "${BOLD}╔══════════════════════════════════════════════╗${NC}"
    echo -e "${BOLD}║   Installation Complete ✅                   ║${NC}"
    echo -e "${BOLD}╚══════════════════════════════════════════════╝${NC}"
    echo ""
    success "Praxis installed to: ${TARGET_DIR}"
    echo ""
    info "First time install? Open a new Claude Code window to activate."
    info "Upgrading?          Changes take effect immediately."
    echo ""
}

main "$@"
