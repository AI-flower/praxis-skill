#!/usr/bin/env python3
"""
Claude Code Hook: UserPromptSubmit handler for praxis.

Detects multi-step task requests in user input and reminds
the LLM to trigger the praxis skill. This is a code-level
safety net — even if the LLM ignores CLAUDE.md instructions,
this hook will inject a reminder.

Receives JSON on stdin with: hook_event_name, session_id, user_prompt, etc.
"""

import json
import re
import sys
from pathlib import Path

CONFIG_DIR = Path.home() / ".ai-praxis"


def _is_praxis_enabled():
    """Check if praxis skill is enabled in config."""
    try:
        config_file = CONFIG_DIR / "config.json"
        if config_file.exists():
            config = json.loads(config_file.read_text())
            if config.get("skill_enabled") is False:
                return False
    except Exception:
        pass
    return True


def _extract_last_user_message(transcript_path):
    """Extract the last user message from JSONL transcript."""
    try:
        lines = Path(transcript_path).read_text(errors="ignore").strip().split("\n")
        for line in reversed(lines):
            try:
                entry = json.loads(line)
                if entry.get("role") == "user":
                    content = entry.get("content", "")
                    if isinstance(content, list):
                        texts = [c.get("text", "") for c in content if c.get("type") == "text"]
                        content = " ".join(texts)
                    if isinstance(content, str) and content.strip():
                        return content.strip()[:500]
            except (json.JSONDecodeError, AttributeError):
                continue
    except Exception:
        pass
    return ""


def _is_excluded(text):
    """Check if the text matches exclusion patterns (not a task)."""
    stripped = text.strip()

    # Too short
    if len(stripped) < 5:
        return True

    # Slash commands
    if stripped.startswith("/"):
        return True

    # Git commands
    if stripped.lower().startswith("git "):
        return True

    # Pure questions without task verbs
    question_only = re.search(r'[?？]\s*$', stripped)
    explanation_patterns = re.compile(
        r'(是什么|什么是|how does|what is|what are|explain|解释一下|解释|啥意思|怎么回事)',
        re.IGNORECASE
    )
    if question_only and explanation_patterns.search(stripped):
        return True

    # Code Q&A: "这段代码/这个函数/这个文件" + question
    code_qa = re.compile(
        r'(这段代码|这个函数|这个文件|这个方法|这个类|this code|this function|this file)'
    )
    if code_qa.search(stripped) and question_only:
        return True

    # Standalone explanation requests (no task verb)
    if explanation_patterns.search(stripped) and not _has_task_verb_zh(stripped) and not _has_task_verb_en(stripped):
        return True

    return False


def _has_task_verb_zh(text):
    """Check for Chinese task verb patterns."""
    return bool(re.search(
        r'(做|写|生成|创建|修复|优化|部署|迁移|改造|搭建|设计|实现|开发|制作|重构|改进|升级|建)',
        text
    ))


def _has_task_verb_en(text):
    """Check for English task verb patterns."""
    return bool(re.search(
        r'\b(create|build|make|fix|optimize|deploy|migrate|write|design|implement|develop|refactor)\b',
        text,
        re.IGNORECASE
    ))


def _is_task(text):
    """Determine if the text is a multi-step task request."""
    # Chinese task patterns
    cn_patterns = [
        # 帮我 + verb
        r'帮我.{0,4}(做|写|生成|创建|修复|优化|部署|迁移|改造|搭建|设计|实现|开发|制作|重构|改进|升级|建)',
        # 帮忙 + verb
        r'帮忙.{0,4}(做|写|生成|创建|修复|优化|部署|迁移|改造|搭建|设计|实现|开发|制作)',
        # verb + 一个/一下
        r'(做|写|生成|创建|建)一[个下]',
        # fix patterns
        r'(修复|fix).{0,10}(bug|问题|错误|issue)',
        # optimize/refactor patterns
        r'(优化|改进|重构).{0,2}\S',
        # deploy/migrate patterns
        r'(部署|迁移|升级).{0,2}\S',
        # migration pattern
        r'从.{1,20}(迁移到|改成|换成|转到)',
    ]

    for pattern in cn_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return True

    # English task patterns
    en_patterns = [
        # help me + verb
        r'\bhelp\s+me\s+(create|build|make|fix|optimize|deploy|migrate|write|design|implement|develop)\b',
        # verb + noun (at start or after common prefixes)
        r'^(create|build|make|fix|optimize|deploy)\s+\w',
        # I need/want ... that/which/to
        r'\bI\s+(need|want)\s+.{3,40}\s+(that|which|to)\b',
    ]

    for pattern in en_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return True

    return False


def main():
    try:
        hook_data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        sys.exit(0)

    # Only handle UserPromptSubmit
    hook_event = hook_data.get("hook_event_name", "")
    if hook_event != "UserPromptSubmit":
        sys.exit(0)

    # Check if praxis is enabled
    if not _is_praxis_enabled():
        sys.exit(0)

    # Get user input text
    user_text = hook_data.get("user_prompt", "").strip()

    # Fallback: try transcript
    if not user_text:
        transcript_path = hook_data.get("transcript_path", "")
        if transcript_path:
            user_text = _extract_last_user_message(transcript_path)

    if not user_text:
        sys.exit(0)

    # Check exclusions first, then task detection
    if _is_excluded(user_text):
        sys.exit(0)

    if _is_task(user_text):
        print("[praxis] 检测到多步任务需求，建议使用 praxis 执行。请调用 Skill tool（skill: \"praxis\", args: 用户原始输入）。")
        sys.exit(0)

    # Not a task, silent exit
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Never block user input
        sys.exit(0)
