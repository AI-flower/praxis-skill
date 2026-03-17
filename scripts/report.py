#!/usr/bin/env python3
"""
Praxis - Community Report Script

Handles:
1. Reporting execution results to community API
2. Querying community for existing solutions
3. Querying popular/trending solutions

Usage:
    python3 report.py report --intent "..." --industry "..." --category "..." ...
    python3 report.py query "search terms"
    python3 report.py query-popular
    python3 report.py query-popular --industry "办公效率"
"""

import argparse
import hashlib
import json
import os
import platform
import sys
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

# --- Config ---

CONFIG_DIR = Path.home() / ".ai-praxis"
CONFIG_FILE = CONFIG_DIR / "config.json"
PENDING_DIR = CONFIG_DIR / "pending_reports"
CACHE_FILE = CONFIG_DIR / "solution_cache.json"
SOLUTION_LIBRARY_FILE = CONFIG_DIR / "solution_library.json"
CAPABILITY_CATALOG_FILE = CONFIG_DIR / "capability_catalog.json"
PREFERENCES_FILE = CONFIG_DIR / "preferences.json"

DEFAULT_CONFIG = {
    "community_api_endpoint": "https://api.ai-praxis.community",
    "api_key": "",
    "report_enabled": False,  # opt-in by default
    "anonymous_id": "",
    "locale": "auto"
}


def get_config():
    """Load or create config."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    PENDING_DIR.mkdir(parents=True, exist_ok=True)

    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE) as f:
                config = json.load(f)
                # Merge with defaults for any missing keys
                for k, v in DEFAULT_CONFIG.items():
                    if k not in config:
                        config[k] = v
                return config
        except json.JSONDecodeError:
            pass

    # First run - create default config
    config = DEFAULT_CONFIG.copy()
    config["anonymous_id"] = hashlib.sha256(
        f"{uuid.getnode()}-{platform.node()}".encode()
    ).hexdigest()[:16]

    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

    return config


def detect_platform():
    """Detect if running in Claude Code or OpenClaw."""
    if os.environ.get("CLAUDE_CODE"):
        return "claude-code"
    if os.environ.get("OPENCLAW_HOME") or os.environ.get("CLAWDBOT_HOME"):
        return "openclaw"
    # Heuristic
    if Path.home().joinpath(".claude").exists():
        return "claude-code"
    if Path.home().joinpath(".openclaw").exists():
        return "openclaw"
    return "unknown"


def detect_locale():
    """Detect user locale."""
    import locale
    try:
        lang = locale.getdefaultlocale()[0] or "en_US"
        return lang.replace("_", "-")
    except Exception:
        return "en-US"


# --- Sanitize (privacy placeholder) ---

def sanitize(data):
    """
    Strip potentially sensitive information from report data.
    TODO: Enhance with more comprehensive PII detection.
    """
    sensitive_patterns = [
        "/Users/", "/home/", "C:\\Users\\",  # file paths
        "sk-", "ghp_", "gho_", "Bearer ",     # API keys
        "@gmail", "@outlook", "@qq.com",       # emails
    ]

    def clean_str(s):
        if not isinstance(s, str):
            return s
        for pattern in sensitive_patterns:
            if pattern in s:
                # Replace with redacted marker
                s = s.replace(pattern, "[REDACTED]/")
        return s

    if isinstance(data, dict):
        return {k: sanitize(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [sanitize(item) for item in data]
    elif isinstance(data, str):
        return clean_str(data)
    return data


# --- HTTP & Transform ---

def _api_post(url, payload, api_key="", timeout=10):
    """POST JSON to API using urllib (zero dependencies). Returns (success, response_data)."""
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        return True, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return False, {"status": e.code, "error": e.read().decode("utf-8", errors="ignore")[:200]}
    except Exception as e:
        return False, {"error": str(e)}


def _api_get(url, params=None, api_key="", timeout=10):
    """GET from API using urllib. Returns (success, response_data)."""
    if params:
        from urllib.parse import urlencode
        url = f"{url}?{urlencode(params)}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        return True, json.loads(resp.read().decode("utf-8"))
    except Exception:
        return False, {}


def transform_to_api_payload(local_report):
    """Transform local report schema to backend API schema.

    Local: {intent, plan, result, output, ...}
    Backend: {task_description, skills[], execution_plan, is_successful}
    """
    intent = local_report.get("intent", {})
    plan = local_report.get("plan", {})
    result = local_report.get("result", {})
    output = local_report.get("output", {})

    # task_description — original_input is the verbatim user request (most useful for backend)
    # Prefer original_input over summary so backend always gets what the user actually typed
    # Multiple fallbacks to ensure this critical field is never empty
    task_description = (
        intent.get("original_input", "")
        or intent.get("summary", "")
        or local_report.get("task_description", "")
        or output.get("summary", "")  # fallback: Phase 5 output summary
        or local_report.get("execution_plan", "")  # fallback: execution plan text
    )
    if not task_description or not task_description.strip():
        # Last resort: synthesize from available data
        parts = []
        if intent.get("industry"):
            parts.append(intent["industry"])
        if intent.get("category"):
            parts.append(intent["category"])
        if intent.get("tags"):
            parts.append(", ".join(intent["tags"]))
        deliverables = output.get("deliverables", [])
        if deliverables:
            parts.append(f"产出: {', '.join(deliverables[:3])}")
        task_description = " - ".join(parts) if parts else "未记录的任务"

    # is_successful
    is_successful = result.get("success", False)

    # execution_plan — synthesize from structured data
    parts = []
    steps = plan.get("steps_count", 0)
    completed = result.get("steps_completed", 0)
    failed = result.get("steps_failed", 0)
    if steps:
        parts.append(f"共{steps}步，完成{completed}步，失败{failed}步")
    tools = plan.get("tools_used", [])
    if tools:
        parts.append(f"使用工具: {', '.join(tools)}")
    auto_fixes = plan.get("auto_fixes", [])
    if auto_fixes:
        parts.append(f"自动修复: {', '.join(auto_fixes)}")
    summary = output.get("summary", "")
    if summary:
        parts.append(f"产出: {summary}")
    execution_plan = "。".join(parts) if parts else "N/A"

    # skills — prefer skills_detail (rich), fallback to skills_used names
    skills = []
    skills_detail = plan.get("skills_detail", [])
    if skills_detail and isinstance(skills_detail, list):
        for s in skills_detail:
            if isinstance(s, dict) and s.get("name"):
                skills.append({
                    "name": s.get("name", ""),
                    "description": s.get("description", s.get("name", "")),
                    "content": s.get("content", ""),
                    "install_command": s.get("install_command") or None,
                    "source": s.get("source") or None,
                })
    if not skills:
        for name in plan.get("skills_used", []):
            skills.append({
                "name": name,
                "description": name,
                "content": ""
            })
    if not skills:
        skills.append({"name": "praxis", "description": "AI task execution engine", "content": ""})

    payload = {
        "task_description": task_description,
        "skills": skills,
        "execution_plan": execution_plan,
        "is_successful": is_successful,
    }

    # error_message for failed tasks
    if not is_successful:
        error_msg = local_report.get("error_message", "")
        if not error_msg:
            # Synthesize from available failure info
            fail_parts = []
            if failed:
                fail_parts.append(f"{failed}步失败")
            if auto_fixes:
                fail_parts.append(f"自动修复: {', '.join(auto_fixes)}")
            status = local_report.get("status", "")
            if status == "incomplete":
                fail_parts.append("任务未完成（中途中断）")
            error_msg = "。".join(fail_parts) if fail_parts else "执行失败"
        payload["error_message"] = error_msg

    # Include extra fields the backend might accept
    if intent.get("industry"):
        payload["industry"] = intent["industry"]
    if intent.get("category"):
        payload["category"] = intent["category"]
    if intent.get("tags"):
        payload["tags"] = intent["tags"]

    # Include generated output content
    out = local_report.get("output", {})
    full_content_file = out.get("full_content_file", "")
    if full_content_file and Path(full_content_file).exists():
        try:
            payload["output_content"] = Path(full_content_file).read_text(errors="ignore")
        except Exception:
            pass
    elif out.get("preview"):
        payload["output_content"] = out["preview"]
    if out.get("deliverables"):
        deliverable_meta = []
        for path_str in out["deliverables"]:
            p = Path(path_str).expanduser()
            entry = {"path": path_str, "name": p.name, "ext": p.suffix.lower()}
            if p.exists():
                stat = p.stat()
                entry["size_bytes"] = stat.st_size
                if p.suffix.lower() in {".py", ".js", ".ts", ".html", ".css", ".json", ".md", ".sh", ".txt", ".yaml", ".toml"}:
                    try:
                        entry["line_count"] = sum(1 for _ in p.open(errors="ignore"))
                    except Exception:
                        pass
            deliverable_meta.append(entry)
        payload["deliverables"] = deliverable_meta

    return payload


def _try_upload(local_report, config):
    """Try to upload a report to the API. Returns solution_id on success, None on failure."""
    if not config.get("report_enabled"):
        return None
    endpoint = config.get("community_api_endpoint", "").rstrip("/")
    if not endpoint:
        return None
    api_payload = transform_to_api_payload(local_report)
    ok, resp = _api_post(f"{endpoint}/api/solutions", api_payload, config.get("api_key", ""))
    if ok:
        # Response: {code: 0, message: "ok", data: {id, ...}}
        data = resp.get("data", {}) if isinstance(resp, dict) else {}
        return data.get("id") or True
    return None


# --- Report ---

def do_report(args):
    """Report execution result to community API."""
    config = get_config()

    payload = {
        "schema_version": "1.0",
        "anonymous_id": config["anonymous_id"],
        "platform": detect_platform(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "locale": detect_locale() if config["locale"] == "auto" else config["locale"],
        "intent": {
            "industry": args.industry or "",
            "category": args.category or "",
            "summary": args.intent or "",
            "tags": [t.strip() for t in (args.tags or "").split(",") if t.strip()]
        },
        "plan": {
            "steps_count": int(args.steps or 0),
            "skills_used": [s.strip() for s in (args.skills_used or "").split(",") if s.strip()],
            "tools_used": [t.strip() for t in (args.tools_used or "").split(",") if t.strip()],
            "auto_fixes": [f.strip() for f in (args.auto_fixes or "").split(",") if f.strip()]
        },
        "result": {
            "success": args.success == "true",
            "steps_completed": int(args.steps_completed or args.steps or 0),
            "steps_failed": int(args.steps_failed or 0),
            "duration_seconds": int(args.duration or 0)
        }
    }

    # Sanitize before sending
    payload = sanitize(payload)

    # Always save locally first
    pending_file = PENDING_DIR / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(pending_file, "w") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    # Try to upload if enabled
    if _try_upload(payload, config):
        pending_file.unlink(missing_ok=True)
        print("REPORTED_OK")
    else:
        print(f"SAVED_LOCALLY:{pending_file}")


# --- Query ---

def do_query(args):
    """Query community for existing solutions (semantic search via POST)."""
    config = get_config()

    if not config["report_enabled"] or not config["community_api_endpoint"]:
        # Check local cache
        if CACHE_FILE.exists():
            try:
                cache = json.load(open(CACHE_FILE))
                query_lower = args.query.lower()
                matches = [
                    s for s in cache.get("solutions", [])
                    if query_lower in json.dumps(s, ensure_ascii=False).lower()
                ]
                if matches:
                    print(json.dumps(matches[:5], indent=2, ensure_ascii=False))
                    return
            except Exception:
                pass
        print("NO_CACHE")
        return

    endpoint = config["community_api_endpoint"].rstrip("/")
    ok, resp = _api_post(
        f"{endpoint}/api/solutions/search",
        {"query": args.query, "limit": 5},
        api_key=config.get("api_key", "")
    )
    if ok:
        # Response: {code, message, data: [{solution, similarity}, ...]}
        results_raw = resp.get("data", []) if isinstance(resp, dict) else []
        # Extract solution objects for display and caching
        solutions = [item.get("solution", item) for item in results_raw if isinstance(item, dict)]
        # Cache results locally
        try:
            cache = json.load(open(CACHE_FILE)) if CACHE_FILE.exists() else {"solutions": []}
            cache["solutions"].extend(solutions)
            cache["solutions"] = cache["solutions"][-500:]
            with open(CACHE_FILE, "w") as f:
                json.dump(cache, f, ensure_ascii=False)
        except Exception:
            pass
        print(json.dumps(results_raw, indent=2, ensure_ascii=False))
    else:
        print("NO_CACHE")


def do_query_popular(args):
    """Query popular/trending solutions via semantic search."""
    config = get_config()

    if not config["report_enabled"] or not config["community_api_endpoint"]:
        print("OFFLINE")
        return

    endpoint = config["community_api_endpoint"].rstrip("/")
    query = args.industry if args.industry else "popular solutions"
    ok, resp = _api_post(
        f"{endpoint}/api/solutions/search",
        {"query": query, "limit": 10},
        api_key=config.get("api_key", "")
    )
    if ok:
        results_raw = resp.get("data", []) if isinstance(resp, dict) else []
        print(json.dumps(results_raw, indent=2, ensure_ascii=False))
    else:
        print("OFFLINE")


# --- Feedback ---

def do_feedback(args):
    """Submit feedback (upvote/downvote) for a solution."""
    config = get_config()

    if not config.get("report_enabled"):
        print("REPORTING_DISABLED")
        return
    endpoint = config.get("community_api_endpoint", "").rstrip("/")
    if not endpoint:
        print("NO_ENDPOINT")
        return

    solution_id = args.solution_id
    feedback_type = args.type  # "upvote" or "downvote"
    if feedback_type not in ("upvote", "downvote"):
        print("INVALID_TYPE")
        return

    ok, resp = _api_post(
        f"{endpoint}/api/solutions/{solution_id}/feedback",
        {"type": feedback_type},
        api_key=config.get("api_key", "")
    )
    if ok:
        print("FEEDBACK_OK")
    else:
        error = resp.get("error", "") if isinstance(resp, dict) else ""
        print(f"FEEDBACK_FAILED:{error}")


# --- Upload pending ---

def do_upload_pending(args):
    """Upload all pending reports."""
    config = get_config()
    if not config["report_enabled"]:
        print("REPORTING_DISABLED")
        return

    pending_files = list(PENDING_DIR.glob("*.json"))
    if not pending_files:
        print("NO_PENDING")
        return

    endpoint = config["community_api_endpoint"].rstrip("/")
    uploaded = 0
    for pf in pending_files:
        try:
            local_report = json.load(open(pf))
            if _try_upload(local_report, config):
                pf.unlink()
                uploaded += 1
        except Exception:
            continue

    print(f"UPLOADED:{uploaded}/{len(pending_files)}")


# --- Save Intent (early recording) ---

CURRENT_TASK_FILE = CONFIG_DIR / "current_task.json"


def _check_relevance(original_input: str, output_summary: str, deliverables: str) -> bool:
    """Returns True if the output appears relevant to the original input.
    Uses character/word overlap to detect AI going off-topic (score < 0.2 = irrelevant).
    Chinese text is tokenized as individual characters (bigrams handled via single chars).
    English text uses 3+ char words."""
    import re
    if not original_input or not output_summary:
        return True  # Can't judge, assume relevant

    cn_stopwords = {
        '的', '了', '是', '在', '我', '你', '他', '她', '它', '们', '个', '一',
        '帮', '做', '写', '给', '用', '把', '从', '和', '或', '也', '都', '很',
        '就', '有', '没', '不', '要', '吧', '啊', '哦', '嗯', '这', '那', '什',
        '么', '为', '以', '可', '请', '让', '去', '来', '将', '把', '被', '跟',
    }
    en_stopwords = {
        'the', 'and', 'for', 'with', 'that', 'this', 'from', 'have', 'are',
        'help', 'make', 'create', 'build', 'write', 'get', 'use', 'can',
    }

    def keywords(text):
        # Chinese: individual characters, filtered by stopwords
        cn = [c for c in re.findall(r'[\u4e00-\u9fff]', text) if c not in cn_stopwords]
        # English: words of 3+ chars
        en = [w for w in re.findall(r'[a-zA-Z]{3,}', text.lower()) if w not in en_stopwords]
        return set(cn + en)

    input_keys = keywords(original_input)
    output_keys = keywords(output_summary + ' ' + deliverables)

    if not input_keys:
        return True

    overlap = len(input_keys & output_keys)
    score = overlap / len(input_keys)
    return score >= 0.2


def do_discard_task(args):
    """Discard current pending task without uploading (Phase 3 rejection or off-topic response)."""
    if CURRENT_TASK_FILE.exists():
        CURRENT_TASK_FILE.unlink(missing_ok=True)
    print("TASK_DISCARDED")


def do_save_intent(args):
    """Save intent early in Phase 1, before plan generation.
    This ensures we capture the user's need even if Phase 5 is skipped."""
    config = get_config()

    task = {
        "schema_version": "2.0",
        "anonymous_id": config["anonymous_id"],
        "platform": detect_platform(),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "locale": detect_locale() if config["locale"] == "auto" else config["locale"],
        "intent": {
            "industry": args.industry or "",
            "category": args.category or "",
            "summary": args.intent or "",
            "original_input": args.original_input or "",
            "tags": [t.strip() for t in (args.tags or "").split(",") if t.strip()],
            "tech_stack": [t.strip() for t in (getattr(args, "tech_stack", None) or "").split(",") if t.strip()],
            "project_type": getattr(args, "project_type", None) or "",
        },
        "status": "started",
        "task_id": str(__import__("uuid").uuid4())
    }

    task = sanitize(task)

    with open(CURRENT_TASK_FILE, "w") as f:
        json.dump(task, f, indent=2, ensure_ascii=False)

    print(f"INTENT_SAVED:{CURRENT_TASK_FILE}")


def do_update_result(args):
    """Update current task with execution results (called in Phase 5).
    Merges result into the intent record and saves as a final report."""
    config = get_config()

    # Load existing intent
    if CURRENT_TASK_FILE.exists():
        try:
            task = json.load(open(CURRENT_TASK_FILE))
        except Exception:
            task = {}
    else:
        task = {
            "schema_version": "2.1",
            "anonymous_id": config["anonymous_id"],
            "platform": detect_platform(),
            "locale": detect_locale(),
            "intent": {"industry": "", "category": "", "summary": "", "tags": []}
        }

    # Ensure intent is not empty — if current_task.json was missing or had empty intent,
    # backfill from available args (output-summary, etc.) so API upload won't fail with 422
    intent = task.get("intent", {})
    if not intent.get("original_input") and not intent.get("summary"):
        # Try to recover intent from output-summary or other args
        if args.output_summary:
            intent["summary"] = args.output_summary
        task["intent"] = intent

    # Add result data
    task["completed_at"] = datetime.now(timezone.utc).isoformat()
    task["status"] = "completed"
    task["report_version"] = "1.5.0"
    task["schema_version"] = "2.1"
    task["plan"] = {
        "steps_count": int(args.steps or 0),
        "skills_used": [s.strip() for s in (args.skills_used or "").split(",") if s.strip()],
        "tools_used": [t.strip() for t in (args.tools_used or "").split(",") if t.strip()],
        "auto_fixes": [f.strip() for f in (args.auto_fixes or "").split(",") if f.strip()],
        "skills_detail": json.loads(args.skills_detail) if getattr(args, "skills_detail", None) and args.skills_detail.strip().startswith("[") else [],
    }
    task["result"] = {
        "success": args.success == "true",
        "steps_completed": int(args.steps_completed or args.steps or 0),
        "steps_failed": int(args.steps_failed or 0),
        "duration_seconds": int(args.duration or 0)
    }
    if args.error_message:
        task["error_message"] = args.error_message
    if getattr(args, "error_detail", None) and args.error_detail:
        task["error_detail"] = args.error_detail
    task["output"] = {
        "summary": args.output_summary or "",
        "deliverables": [d.strip() for d in (args.deliverables or "").split(",") if d.strip()],
        "full_content_file": ""
    }
    if getattr(args, "artifacts_json", None) and args.artifacts_json.strip():
        try:
            task["artifacts"] = json.loads(args.artifacts_json)
        except Exception:
            task["artifacts"] = []
    if getattr(args, "execution_plan", None) and args.execution_plan.strip():
        task["execution_plan"] = args.execution_plan

    # Track if this task was based on a queried solution
    if args.based_on:
        task["based_on_solution_id"] = args.based_on

    # Save full output content to a separate file (keeps JSON report small)
    if args.output_file and Path(args.output_file).exists():
        try:
            content = Path(args.output_file).read_text(errors="ignore")
            if content.strip():
                output_dir = CONFIG_DIR / "outputs"
                output_dir.mkdir(parents=True, exist_ok=True)
                output_filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_output.md"
                output_path = output_dir / output_filename
                with open(output_path, "w") as f:
                    f.write(content)
                task["output"]["full_content_file"] = str(output_path)
                # Also store first 500 chars as preview in JSON for quick browsing
                task["output"]["preview"] = content[:500].strip()
        except Exception:
            pass

    # Record whether user confirmed the plan (Phase 3 approval gate)
    user_confirmed = getattr(args, "user_confirmed", "false") == "true"
    task["user_confirmed"] = user_confirmed

    task = sanitize(task)

    # Upload decision: only upload if user confirmed the plan
    # - user confirmed + success → upload (system learns from wins)
    # - user confirmed + failure → upload (system learns from failures)
    # - user rejected / no confirmation / off-topic → local only, no upload
    should_upload = user_confirmed

    if should_upload:
        # Save as final report
        report_file = PENDING_DIR / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(report_file, "w") as f:
            json.dump(task, f, indent=2, ensure_ascii=False)

        # Try immediate upload
        solution_id = _try_upload(task, config)
        if solution_id:
            report_file.unlink(missing_ok=True)
            print(f"UPLOADED_OK:{solution_id}" if isinstance(solution_id, str) else "UPLOADED_OK")
        else:
            print(f"RESULT_SAVED:{report_file}")
    else:
        print("SKIPPED_UPLOAD:user_not_confirmed")

    # Auto-feedback: if this task was based on a queried solution, send feedback
    based_on = task.get("based_on_solution_id", "")
    if based_on and config.get("report_enabled"):
        endpoint = config.get("community_api_endpoint", "").rstrip("/")
        if endpoint:
            fb_type = "upvote" if task.get("result", {}).get("success") else "downvote"
            _api_post(
                f"{endpoint}/api/solutions/{based_on}/feedback",
                {"type": fb_type},
                config.get("api_key", "")
            )

    # Clean up current task
    try:
        CURRENT_TASK_FILE.unlink()
    except Exception:
        pass


# --- Solution Library ---

def _simple_similarity(query: str, text: str) -> float:
    """Keyword overlap similarity (0.0-1.0). No external deps."""
    import re
    def tokenize(s):
        return set(re.findall(r'[\u4e00-\u9fff]|[a-zA-Z0-9]+', s.lower()))
    q = tokenize(query)
    t = tokenize(text)
    if not q or not t:
        return 0.0
    inter = q & t
    query_cov = len(inter) / len(q)
    jaccard = len(inter) / len(q | t)
    return round(0.7 * query_cov + 0.3 * jaccard, 3)


def do_search_solutions(args):
    """Search local solution library + optional server for similar solutions."""
    query = args.query or ""
    limit = int(args.limit or 3)
    min_score = float(args.min_score or 0.5)

    if not query.strip():
        print("NO_RESULTS")
        return

    config = get_config()
    solutions = []

    # 1. Try server API (if enabled)
    if config.get("report_enabled") and config.get("community_api_endpoint"):
        endpoint = config["community_api_endpoint"].rstrip("/")
        ok, resp = _api_post(
            f"{endpoint}/api/solutions/search",
            {"query": query, "limit": limit * 2},
            api_key=config.get("api_key", "")
        )
        if ok and isinstance(resp, dict):
            for item in resp.get("data", []):
                if not isinstance(item, dict):
                    continue
                sol = item.get("solution", item)
                score = float(item.get("similarity", item.get("score", 0.5)))
                if score >= min_score:
                    solutions.append({
                        "id": sol.get("id", ""),
                        "summary": sol.get("task_description", sol.get("summary", "")),
                        "industry": sol.get("industry", ""),
                        "category": sol.get("category", ""),
                        "tags": sol.get("tags", []),
                        "score": score,
                        "source": "server",
                        "required_capabilities": sol.get("required_capabilities", []),
                    })

    # 2. Local solution library + cache
    local_sols = []
    for fpath in [SOLUTION_LIBRARY_FILE, CACHE_FILE]:
        if fpath.exists():
            try:
                data = json.load(open(fpath))
                if isinstance(data, list):
                    local_sols.extend(data)
                elif isinstance(data, dict):
                    local_sols.extend(data.get("solutions", []))
            except Exception:
                pass

    for sol in local_sols:
        intent_obj = sol.get("intent", {})
        text_parts = [
            sol.get("summary", ""),
            sol.get("task_description", ""),
            sol.get("output_summary", ""),
            intent_obj.get("summary", "") if isinstance(intent_obj, dict) else "",
            sol.get("category", "") or (intent_obj.get("category", "") if isinstance(intent_obj, dict) else ""),
            sol.get("industry", "") or (intent_obj.get("industry", "") if isinstance(intent_obj, dict) else ""),
            " ".join(
                (sol.get("tags") or (intent_obj.get("tags", []) if isinstance(intent_obj, dict) else []))
                if isinstance(sol.get("tags") or (intent_obj.get("tags") if isinstance(intent_obj, dict) else None), list)
                else []
            ),
        ]
        sol_text = " ".join(str(p) for p in text_parts if p)
        score = _simple_similarity(query, sol_text)
        # tech_stack 匹配加分
        sol_tech = sol.get("tech_stack", [])
        if sol_tech and query:
            import re
            q_tokens = set(re.findall(r'[a-zA-Z0-9]+', query.lower()))
            t_tokens = set(t.lower() for t in sol_tech if isinstance(t, str))
            if q_tokens & t_tokens:
                score = min(1.0, score + 0.15)
        # artifacts_summary 匹配加分
        sol_arts = sol.get("artifacts_summary", [])
        if sol_arts and query:
            a_tokens = set(t.lower() for t in sol_arts if isinstance(t, str))
            if q_tokens & a_tokens:
                score = min(1.0, score + 0.10)
        if score >= min_score:
            sol_id = sol.get("id", "")
            existing_ids = {s.get("id") for s in solutions if s.get("id")}
            if sol_id and sol_id in existing_ids:
                continue
            summary = (
                sol.get("summary")
                or sol.get("task_description")
                or (intent_obj.get("summary", "") if isinstance(intent_obj, dict) else "")
            )
            solutions.append({
                "id": sol_id,
                "summary": summary,
                "industry": sol.get("industry", "") or (intent_obj.get("industry", "") if isinstance(intent_obj, dict) else ""),
                "category": sol.get("category", "") or (intent_obj.get("category", "") if isinstance(intent_obj, dict) else ""),
                "tags": sol.get("tags", []) or (intent_obj.get("tags", []) if isinstance(intent_obj, dict) else []),
                "score": score,
                "source": "local",
                "required_capabilities": sol.get("required_capabilities", []),
            })

    # Sort, deduplicate, limit
    solutions.sort(key=lambda x: x["score"], reverse=True)
    seen, deduped = set(), []
    for s in solutions:
        key = (s.get("summary") or "")[:80].strip()
        if key and key not in seen:
            seen.add(key)
            deduped.append(s)

    top = deduped[:limit]
    if not top:
        print("NO_RESULTS")
    else:
        print(f"SOLUTIONS_JSON:{json.dumps(top, ensure_ascii=False)}")


def do_save_solution(args):
    """Save a completed solution to local library and optionally upload."""
    config = get_config()

    solution = {
        "id": str(uuid.uuid4()),
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "summary": args.summary or "",
        "industry": args.industry or "",
        "category": args.category or "",
        "tags": [t.strip() for t in (args.tags or "").split(",") if t.strip()],
        "steps": int(args.steps or 0),
        "success": args.success == "true",
        "output_summary": args.output_summary or "",
        "deliverables": [d.strip() for d in (args.deliverables or "").split(",") if d.strip()],
        "required_capabilities": [c.strip() for c in (args.required_capabilities or "").split(",") if c.strip()],
        "artifacts_summary": [t.strip() for t in (getattr(args, "artifacts_summary", None) or "").split(",") if t.strip()],
        "tech_stack": [t.strip() for t in (getattr(args, "tech_stack", None) or "").split(",") if t.strip()],
        "use_count": 1,
    }
    solution = sanitize(solution)

    # --- Value scoring: skip low-value solutions ---
    score = 0
    # 1. Step complexity (max 25)
    steps = solution.get("steps", 0)
    if steps >= 4:
        score += 25
    elif steps == 3:
        score += 15
    elif steps == 2:
        score += 5
    # 2. Deliverables count (max 20)
    n_deliv = len(solution.get("deliverables", []))
    if n_deliv >= 3:
        score += 20
    elif n_deliv == 2:
        score += 15
    elif n_deliv == 1:
        score += 10
    # 3. Required capabilities (max 15)
    n_caps = len(solution.get("required_capabilities", []))
    if n_caps >= 2:
        score += 15
    elif n_caps == 1:
        score += 8
    # 4. Tag richness (max 10)
    n_tags = len(solution.get("tags", []))
    if n_tags >= 4:
        score += 10
    elif n_tags >= 2:
        score += 5
    # 5. Output summary info density (max 15)
    summary_len = len(solution.get("output_summary", ""))
    if summary_len >= 30:
        score += 15
    elif summary_len >= 10:
        score += 8
    # 6. Tech stack depth (max 15)
    n_tech = len(solution.get("tech_stack", []))
    if n_tech >= 2:
        score += 15
    elif n_tech == 1:
        score += 8

    if score < 40:
        print(f"SOLUTION_SKIPPED:low_value:score={score}")
        return

    # Load, append, save (keep last 500)
    lib = []
    if SOLUTION_LIBRARY_FILE.exists():
        try:
            lib = json.load(open(SOLUTION_LIBRARY_FILE))
        except Exception:
            lib = []
    lib.append(solution)
    lib = lib[-500:]
    SOLUTION_LIBRARY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(SOLUTION_LIBRARY_FILE, "w") as f:
        json.dump(lib, f, indent=2, ensure_ascii=False)

    # Try upload
    solution_id = None
    if config.get("report_enabled") and config.get("community_api_endpoint"):
        api_payload = {
            "task_description": solution["summary"],
            "industry": solution["industry"],
            "category": solution["category"],
            "tags": solution["tags"],
            "skills": [{"name": "praxis", "description": "", "content": ""}],
            "execution_plan": f"共{solution['steps']}步",
            "is_successful": solution["success"],
            "required_capabilities": solution["required_capabilities"],
        }
        endpoint = config["community_api_endpoint"].rstrip("/")
        ok, resp = _api_post(f"{endpoint}/api/solutions", api_payload, config.get("api_key", ""))
        if ok and isinstance(resp, dict):
            solution_id = resp.get("data", {}).get("id")

    if solution_id:
        print(f"SOLUTION_SAVED:uploaded:{solution_id}")
    else:
        print(f"SOLUTION_SAVED:local:{solution['id']}")


# --- Capability Detection & Auto-Install ---

CAPABILITY_CATALOG = {
    "playwright": {
        "description": "自动化浏览器操作（爬虫、截图、表单填写）",
        "triggers": ["爬虫", "爬取", "抓取", "截图", "自动化浏览器", "playwright", "chromium",
                     "scrape", "crawl", "screenshot", "browser automation", "web automation",
                     "登录网站", "模拟点击", "网页自动化", "自动填表"],
        "type": "pip",
        "install_commands": [
            "pip3 install -q playwright",
            "playwright install chromium"
        ],
        "check_command": "python3 -c \"import playwright; print('OK')\" 2>/dev/null",
    },
    "filesystem": {
        "description": "文件系统 MCP（读写任意文件）",
        "triggers": ["读文件", "写文件", "文件操作", "本地文件", "filesystem", "file system",
                     "读取文件夹", "批量处理文件", "文件管理", "批量重命名"],
        "type": "mcp",
        "install_commands": [
            "claude mcp add filesystem -- npx -y @modelcontextprotocol/server-filesystem ~"
        ],
        "check_command": "claude mcp list 2>/dev/null | grep -q filesystem && echo OK",
    },
    "sqlite": {
        "description": "SQLite 数据库操作",
        "triggers": ["数据库", "sqlite", "sql查询", "存储数据", "database", "sql", "db操作",
                     "本地数据库", "数据存储"],
        "type": "pip",
        "install_commands": ["pip3 install -q sqlite-utils"],
        "check_command": "python3 -c \"import sqlite3; print('OK')\"",
    },
    "brave-search": {
        "description": "Brave 搜索 MCP（联网搜索）",
        "triggers": ["搜索网页", "查询最新信息", "获取最新", "brave search", "web search",
                     "搜索最新", "联网搜索", "search web", "实时搜索"],
        "type": "mcp",
        "install_commands": [
            "claude mcp add brave-search --env BRAVE_API_KEY=BSAEZcPWYQkuumZV5bHoNMNVjNNJGSk -- npx -y @modelcontextprotocol/server-brave-search"
        ],
        "check_command": "claude mcp list 2>/dev/null | grep -q brave && echo OK",
    },
    "notion": {
        "description": "Notion MCP（读写 Notion 文档和数据库）",
        "triggers": ["notion", "写notion", "读notion", "notion文档", "notion数据库",
                     "同步notion", "notion笔记"],
        "type": "mcp",
        "install_commands": [
            "claude mcp add notion --env OPENAPI_MCP_HEADERS='{\"Authorization\":\"Bearer NOTION_TOKEN\",\"Notion-Version\":\"2022-06-28\"}' -- npx -y @notionhq/notion-mcp-server"
        ],
        "check_command": "claude mcp list 2>/dev/null | grep -q notion && echo OK",
    },
    "pandas": {
        "description": "数据分析（pandas + numpy + matplotlib）",
        "triggers": ["数据分析", "数据处理", "excel分析", "csv分析", "统计分析", "图表",
                     "data analysis", "pandas", "numpy", "matplotlib", "数据可视化",
                     "分析数据", "统计报告", "数据报告"],
        "type": "pip",
        "install_commands": ["pip3 install -q pandas numpy matplotlib openpyxl seaborn"],
        "check_command": "python3 -c \"import pandas; print('OK')\" 2>/dev/null",
    },
    "requests": {
        "description": "HTTP 请求库（API 调用）",
        "triggers": ["调用api", "http请求", "接口调用", "requests", "api调用",
                     "call api", "http request", "fetch data", "调接口", "调用接口"],
        "type": "pip",
        "install_commands": ["pip3 install -q requests"],
        "check_command": "python3 -c \"import requests; print('OK')\" 2>/dev/null",
    },
    "weasyprint": {
        "description": "HTML/CSS 转 PDF",
        "triggers": ["生成pdf", "导出pdf", "html转pdf", "pdf报告", "pdf生成",
                     "generate pdf", "export pdf", "html to pdf", "转成pdf"],
        "type": "pip",
        "install_commands": ["pip3 install -q weasyprint"],
        "check_command": "python3 -c \"import weasyprint; print('OK')\" 2>/dev/null",
    },
    "pillow": {
        "description": "图像处理（PIL/Pillow）",
        "triggers": ["图片处理", "图像处理", "裁剪图片", "压缩图片", "生成图片", "image processing",
                     "resize image", "图片转换", "批量处理图片", "图片合并"],
        "type": "pip",
        "install_commands": ["pip3 install -q Pillow"],
        "check_command": "python3 -c \"import PIL; print('OK')\" 2>/dev/null",
    },
    "openai": {
        "description": "OpenAI API 客户端",
        "triggers": ["openai", "gpt", "chatgpt", "dall-e", "whisper", "openai api",
                     "调用openai", "gpt-4", "gpt-3"],
        "type": "pip",
        "install_commands": ["pip3 install -q openai"],
        "check_command": "python3 -c \"import openai; print('OK')\" 2>/dev/null",
    },
    "write-novel": {
        "description": "小说写作 Skill",
        "triggers": ["写小说", "小说创作", "写故事", "写作小说", "novel", "fiction writing",
                     "创作小说", "续写小说", "写长篇"],
        "type": "skill",
        "install_commands": ["echo 'write-novel skill already bundled'"],
        "check_command": "ls ~/.claude/skills/write-novel/SKILL.md 2>/dev/null | grep -q . && echo OK",
    },
    "figma-to-react": {
        "description": "Figma 设计稿转 React 组件",
        "triggers": ["figma", "设计稿转代码", "figma转react", "figma转组件",
                     "figma to react", "figma转换", "设计转代码"],
        "type": "skill",
        "install_commands": ["echo 'figma-to-react skill already bundled'"],
        "check_command": "ls ~/.claude/skills/figma-to-react/SKILL.md 2>/dev/null | grep -q . && echo OK",
    },
}


def _get_capability_catalog() -> dict:
    """Return capability catalog, merging local overrides if any."""
    catalog = CAPABILITY_CATALOG.copy()
    if CAPABILITY_CATALOG_FILE.exists():
        try:
            remote = json.load(open(CAPABILITY_CATALOG_FILE))
            if isinstance(remote, dict):
                catalog.update(remote)
        except Exception:
            pass
    return catalog


def do_detect_capabilities(args):
    """Detect required capabilities from intent/tags/input text."""
    combined = " ".join(filter(None, [
        args.intent or "",
        args.tags or "",
        args.input or "",
    ]))

    if not combined.strip():
        print("NO_CAPABILITIES_NEEDED")
        return

    catalog = _get_capability_catalog()
    text_lower = combined.lower()
    needed = []
    for cap_name, cap_info in catalog.items():
        for trigger in cap_info.get("triggers", []):
            if trigger.lower() in text_lower:
                needed.append(cap_name)
                break

    if not needed:
        print("NO_CAPABILITIES_NEEDED")
    else:
        print(f"CAPABILITIES:{','.join(needed)}")


def do_install_capability(args):
    """Install a capability silently. Output: INSTALL_OK, ALREADY_INSTALLED, INSTALL_FAILED."""
    import subprocess

    cap_name = (args.capability or "").strip()
    catalog = _get_capability_catalog()

    if cap_name not in catalog:
        print(f"UNKNOWN_CAPABILITY:{cap_name}")
        return

    cap = catalog[cap_name]
    check_cmd = cap.get("check_command", "")

    import shlex

    def _run_cmd(cmd_str):
        """Run a fixed catalog command safely without shell=True."""
        try:
            parts = shlex.split(cmd_str)
        except ValueError:
            parts = cmd_str.split()
        return subprocess.run(parts, capture_output=True, text=True)

    # Check if already installed
    if check_cmd:
        r = _run_cmd(check_cmd)
        if r.returncode == 0 and "OK" in r.stdout:
            print(f"ALREADY_INSTALLED:{cap_name}")
            return

    # Run install commands
    for cmd in cap.get("install_commands", []):
        if cmd.strip().startswith("#"):
            continue
        r = _run_cmd(cmd)
        if r.returncode != 0:
            err = (r.stderr or r.stdout or "")[:120].strip()
            print(f"INSTALL_FAILED:{cap_name}:{err}")
            return

    # Verify
    if check_cmd:
        r = _run_cmd(check_cmd)
        if r.returncode == 0 and "OK" in r.stdout:
            print(f"INSTALL_OK:{cap_name}")
        else:
            print(f"INSTALL_OK_UNVERIFIED:{cap_name}")
    else:
        print(f"INSTALL_OK:{cap_name}")


def do_list_capabilities(args):
    """List all available capabilities in the catalog."""
    catalog = _get_capability_catalog()
    for name, info in catalog.items():
        print(f"{name}: {info.get('description', '')} [{info.get('type', '?')}]")



# ── Locale detection ──────────────────────────────────────────────────────────
_LOCALE_LANG_MAP = {
    "zh": "zh", "zh_CN": "zh", "zh_TW": "zh_TW", "zh_HK": "zh_TW",
    "ja": "ja", "ko": "ko", "fr": "fr", "de": "de", "es": "es",
    "pt": "pt", "ru": "ru", "ar": "ar", "it": "it",
}

def _detect_locale() -> dict:
    """Detect system locale. Returns dict with lang, region, display_name."""
    import locale, subprocess, os

    lang = None

    # 1. Check saved preference first
    prefs = _get_preferences() if PREFERENCES_FILE.exists() else {}
    if prefs.get("locale"):
        lang = prefs["locale"]

    # 2. macOS: use 'defaults read .GlobalPreferences AppleLocale'
    if not lang:
        try:
            out = subprocess.check_output(
                ["defaults", "read", "-g", "AppleLocale"], timeout=3
            ).decode().strip()
            if out:
                lang = out  # e.g. "zh_CN", "en_US"
        except Exception:
            pass

    # 3. System locale env
    if not lang:
        for var in ("LANG", "LC_ALL", "LC_MESSAGES"):
            val = os.environ.get(var, "")
            if val and val != "C" and val != "POSIX":
                lang = val.split(".")[0]  # strip .UTF-8
                break

    # 4. Python locale
    if not lang:
        try:
            loc = locale.getdefaultlocale()[0] or ""
            if loc and loc not in ("C", "POSIX"):
                lang = loc
        except Exception:
            pass

    lang = lang or "en_US"

    # Normalise: zh_CN -> zh, en_US -> en
    base = lang.split("_")[0].lower()
    region = lang.split("_")[1].upper() if "_" in lang else ""

    # Map to display name
    display_map = {
        "zh": "中文",
        "ja": "日本語",
        "ko": "한국어",
        "fr": "Français",
        "de": "Deutsch",
        "es": "Español",
        "pt": "Português",
        "ru": "Русский",
        "ar": "العربية",
        "it": "Italiano",
        "en": "English",
    }
    display = display_map.get(base, "English")
    return {"lang": base, "region": region, "locale": lang, "display": display}


def do_detect_locale(args):
    """Detect and optionally save system locale."""
    info = _detect_locale()
    if args.save:
        prefs = _get_preferences() if PREFERENCES_FILE.exists() else {}
        prefs["locale"] = info["locale"]
        _save_preferences(prefs)
        print(f"LOCALE_SAVED:{info['locale']}")
    else:
        import json as _json
        print(f"LOCALE:{_json.dumps(info, ensure_ascii=False)}")


def _get_preferences() -> dict:
    """Load user preferences from disk."""
    if PREFERENCES_FILE.exists():
        try:
            return json.load(open(PREFERENCES_FILE))
        except Exception:
            pass
    return {"auto_execute": False, "domain_familiarity": {}}


def _save_preferences(prefs: dict):
    PREFERENCES_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(PREFERENCES_FILE, "w") as f:
        json.dump(prefs, f, indent=2, ensure_ascii=False)


def do_get_preferences(args):
    """Print all preferences, or a single key if --key is given."""
    prefs = _get_preferences()
    key = getattr(args, "key", "") or ""
    if key:
        # Support nested keys like domain_familiarity.tech
        parts = key.split(".", 1)
        val = prefs.get(parts[0])
        if len(parts) == 2 and isinstance(val, dict):
            val = val.get(parts[1])
        print("NOT_SET" if val is None else json.dumps(val, ensure_ascii=False))
    else:
        print(json.dumps(prefs, ensure_ascii=False))


def do_set_preference(args):
    """Set a preference key to a value. Supports nested keys (a.b)."""
    prefs = _get_preferences()
    key = args.key or ""
    raw = args.value or ""

    # Parse value type
    if raw.lower() == "true":
        value = True
    elif raw.lower() == "false":
        value = False
    else:
        try:
            value = json.loads(raw)
        except Exception:
            value = raw

    # Nested key support: "domain_familiarity.tech" → prefs["domain_familiarity"]["tech"]
    parts = key.split(".", 1)
    if len(parts) == 2:
        if parts[0] not in prefs or not isinstance(prefs[parts[0]], dict):
            prefs[parts[0]] = {}
        prefs[parts[0]][parts[1]] = value
    else:
        prefs[key] = value

    _save_preferences(prefs)
    print(f"PREFERENCE_SET:{key}={json.dumps(value, ensure_ascii=False)}")


# ── Task Rollback ──────────────────────────────────────────────────────────────

TASKS_DIR = CONFIG_DIR / "tasks"


def _manifest_path(task_id: str) -> Path:
    return TASKS_DIR / task_id / "manifest.json"


def _load_manifest(task_id: str):
    p = _manifest_path(task_id)
    return json.loads(p.read_text()) if p.exists() else None


def _save_manifest(manifest: dict):
    task_dir = TASKS_DIR / manifest["task_id"]
    task_dir.mkdir(parents=True, exist_ok=True)
    p = _manifest_path(manifest["task_id"])
    p.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))


def do_track_change(args):
    """Record a single change into the task manifest (called by Phase 4)."""
    task_id = args.task_id or ""
    change_type = args.type or ""
    path_str = args.path or ""
    package = args.package or ""

    if not task_id:
        print("TRACK_FAILED:no_task_id"); return

    manifest = _load_manifest(task_id) or {
        "task_id": task_id,
        "intent": "",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "status": "running",
        "files_created": [],
        "files_modified": {},
        "packages_installed": [],
    }

    if change_type == "file_created":
        if path_str and path_str not in manifest["files_created"]:
            manifest["files_created"].append(path_str)

    elif change_type == "file_modified":
        if path_str and path_str not in manifest["files_modified"]:
            # Back up the original file before it gets overwritten
            p = Path(path_str).expanduser()
            if p.exists():
                backup_dir = TASKS_DIR / task_id / "backups"
                backup_dir.mkdir(parents=True, exist_ok=True)
                backup_path = backup_dir / f"{p.name}.bak"
                import shutil
                shutil.copy2(p, backup_path)
                manifest["files_modified"][path_str] = str(backup_path)

    elif change_type == "pip_installed":
        if package:
            # Only track if not already installed before this task
            import subprocess
            result = subprocess.run(
                ["pip3", "show", package], capture_output=True, text=True
            )
            if result.returncode != 0:  # not pre-installed → safe to uninstall on rollback
                if package not in manifest["packages_installed"]:
                    manifest["packages_installed"].append(package)

    _save_manifest(manifest)
    count = (len(manifest["files_created"]) +
             len(manifest["files_modified"]) +
             len(manifest["packages_installed"]))
    print(f"TRACKED:{change_type}:total={count}")


def do_list_tasks(args):
    """List recent tasks with rollback status."""
    if not TASKS_DIR.exists():
        print("TASK_LIST_JSON:[]"); return

    tasks = []
    for d in sorted(TASKS_DIR.iterdir(), reverse=True):
        mp = d / "manifest.json"
        if not mp.exists():
            continue
        try:
            m = json.loads(mp.read_text())
            tasks.append({
                "task_id": m.get("task_id", d.name),
                "started_at": m.get("started_at", ""),
                "intent": m.get("intent", ""),
                "status": m.get("status", "unknown"),
                "files_created": len(m.get("files_created", [])),
                "files_modified": len(m.get("files_modified", {})),
                "packages_installed": len(m.get("packages_installed", [])),
            })
        except Exception:
            continue

    limit = getattr(args, "limit", 20) or 20
    print(f"TASK_LIST_JSON:{json.dumps(tasks[:limit], ensure_ascii=False)}")


def do_rollback(args):
    """Roll back all changes made during a task."""
    import shutil, subprocess

    # Resolve task_id: explicit arg or latest
    task_id = getattr(args, "task_id", None) or ""
    if not task_id or task_id == "last":
        if not TASKS_DIR.exists():
            print("ROLLBACK_FAILED:no_tasks"); return
        dirs = sorted(TASKS_DIR.iterdir(), reverse=True)
        for d in dirs:
            if (d / "manifest.json").exists():
                task_id = d.name; break
        if not task_id:
            print("ROLLBACK_FAILED:no_tasks"); return

    manifest = _load_manifest(task_id)
    if not manifest:
        print(f"ROLLBACK_FAILED:NO_MANIFEST:{task_id}"); return
    if manifest.get("status") == "rolled_back":
        print(f"ROLLBACK_ALREADY_DONE:{task_id}"); return

    dry_run = getattr(args, "dry_run", False)
    reverted, skipped, warnings = 0, 0, 0

    # 1. Delete created files
    for path_str in manifest.get("files_created", []):
        p = Path(path_str).expanduser()
        if dry_run:
            print(f"  [dry] DELETE {path_str}"); reverted += 1; continue
        if p.exists():
            p.unlink()
            print(f"  DELETED {path_str}"); reverted += 1
        else:
            skipped += 1

    # 2. Restore modified files
    for path_str, backup_str in manifest.get("files_modified", {}).items():
        backup = Path(backup_str).expanduser()
        p = Path(path_str).expanduser()
        if dry_run:
            print(f"  [dry] RESTORE {path_str} ← {backup_str}"); reverted += 1; continue
        if backup.exists():
            shutil.copy2(backup, p)
            print(f"  RESTORED {path_str}"); reverted += 1
        else:
            print(f"  WARN: backup missing for {path_str}"); warnings += 1

    # 3. Uninstall packages (only those that were not pre-installed)
    for pkg in manifest.get("packages_installed", []):
        if dry_run:
            print(f"  [dry] PIP UNINSTALL {pkg}"); reverted += 1; continue
        result = subprocess.run(
            ["pip3", "uninstall", "-y", pkg], capture_output=True, text=True
        )
        if result.returncode == 0:
            print(f"  UNINSTALLED {pkg}"); reverted += 1
        else:
            print(f"  WARN: failed to uninstall {pkg}"); warnings += 1

    if not dry_run:
        manifest["status"] = "rolled_back"
        manifest["rolled_back_at"] = datetime.now(timezone.utc).isoformat()
        _save_manifest(manifest)

    status = "ROLLBACK_OK" if warnings == 0 else "ROLLBACK_PARTIAL"
    print(f"{status}:{task_id}:reverted={reverted},skipped={skipped},warnings={warnings}")


def do_update_catalog(args):
    """Pull latest capability catalog from server."""
    config = get_config()
    endpoint = config.get("community_api_endpoint", "").rstrip("/")

    if not endpoint or not config.get("report_enabled"):
        print("CATALOG_LOCAL_ONLY")
        return

    ok, resp = _api_get(f"{endpoint}/api/capability-catalog", api_key=config.get("api_key", ""))
    if ok and isinstance(resp, dict) and resp.get("data"):
        CAPABILITY_CATALOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(CAPABILITY_CATALOG_FILE, "w") as f:
            json.dump(resp["data"], f, indent=2, ensure_ascii=False)
        print(f"CATALOG_UPDATED:{CAPABILITY_CATALOG_FILE}")
    else:
        print("CATALOG_UNCHANGED")


# --- Send Email ---
#
# Architecture: NO backend server needed. The skill ships with a pre-embedded
# Resend API key (free tier: 3000 emails/month). Direct call from user's machine
# to Resend's API. Works for ALL users with zero configuration.
#
# To activate: set SKILL_EMAIL_KEY below (one-time setup by skill author).
# Get a free key at https://resend.com → free plan → API Keys.
# The "from" address must be verified in your Resend account.
#
# Fallback chain when Resend is unavailable:
#   1. Resend API (pre-embedded key)
#   2. macOS Mail.app (if user has accounts)
#   3. Local SMTP credentials from ~/.ai-praxis/.env
#   4. Upload to catbox.moe + open mailto: link

# ─── SKILL AUTHOR CONFIG (set once when packaging) ────────────────────────────
SKILL_EMAIL_KEY = "re_TVLgpPp3_AuCQEN7ar16Gth6UYGyxHEaS"
SKILL_EMAIL_FROM = "Praxis <jason@omnieye.ai>"
# Gmail SMTP (primary — works immediately, no domain verification needed)
SKILL_GMAIL_USER = "jason@omnieye.ai"
SKILL_GMAIL_APP_PWD = "ebtmvpqukgvjcuex"
# ─────────────────────────────────────────────────────────────────────────────


def _make_mime_attachment(file_path):
    """Build a MIME part with correct content-type inferred from file extension."""
    import mimetypes
    from email.mime.base import MIMEBase
    from email.mime.application import MIMEApplication
    from email import encoders as _encoders
    p = Path(file_path).expanduser()
    mime_type, _ = mimetypes.guess_type(str(p))
    if mime_type and "/" in mime_type:
        maintype, subtype = mime_type.split("/", 1)
        part = MIMEBase(maintype, subtype)
        part.set_payload(p.read_bytes())
        _encoders.encode_base64(part)
    else:
        part = MIMEApplication(p.read_bytes())
    part.add_header("Content-Disposition", "attachment", filename=p.name)
    return part


def _send_via_resend(to, subject, body_text, body_html="", attachments=None, api_key=""):
    """Send email via Resend API. Returns (ok: bool, message_id: str)."""
    key = api_key or SKILL_EMAIL_KEY
    if not key:
        return False, "no_key"

    payload = {
        "from": SKILL_EMAIL_FROM,
        "to": [to],
        "subject": subject,
        "text": body_text,
    }
    if body_html:
        payload["html"] = body_html
    if attachments:
        # attachments: [{"filename": "x.html", "content": "<base64>"}]
        payload["attachments"] = attachments

    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=data,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        method="POST"
    )
    try:
        resp = urllib.request.urlopen(req, timeout=20)
        result = json.loads(resp.read().decode("utf-8"))
        return True, result.get("id", "ok")
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="ignore")[:200]
        return False, err
    except Exception as e:
        return False, str(e)[:100]


def _send_via_gmail(to, subject, body_text, body_html="", file_path="", user="", app_pwd=""):
    """Send via Gmail SMTP with App Password. Returns ok: bool."""
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    try:
        msg = MIMEMultipart("alternative")
        msg["From"] = f"Praxis <{user}>"
        msg["To"] = to
        msg["Subject"] = subject
        msg.attach(MIMEText(body_text, "plain", "utf-8"))
        if body_html:
            msg.attach(MIMEText(body_html, "html", "utf-8"))
        if file_path and Path(file_path).expanduser().exists():
            msg.attach(_make_mime_attachment(file_path))
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=15) as s:
            s.ehlo(); s.starttls(); s.login(user, app_pwd)
            s.sendmail(user, [to], msg.as_bytes())
        return True
    except Exception:
        return False


def _send_via_mailapp(to, subject, body_text, file_path=""):
    """Send via macOS Mail.app using AppleScript. Returns ok: bool."""
    import subprocess
    # First check if Mail.app has any accounts
    check = subprocess.run(
        ["osascript", "-e", "tell application \"Mail\" to count accounts"],
        capture_output=True, text=True
    )
    try:
        count = int(check.stdout.strip())
    except Exception:
        count = 0
    if count == 0:
        return False

    script_lines = [
        'tell application "Mail"',
        '  set newMsg to make new outgoing message',
        f'  set subject of newMsg to "{subject}"',
        f'  set content of newMsg to "{body_text}"',
        '  set visible of newMsg to false',
        '  tell newMsg',
        f'    make new to recipient with properties {{address:"{to}"}}',
    ]
    if file_path and Path(file_path).exists():
        script_lines.append(
            f'    make new attachment with properties {{file name:(POSIX file "{file_path}") as alias}}'
        )
    script_lines += ['  end tell', '  send newMsg', 'end tell']

    result = subprocess.run(
        ["osascript", "-e", "\n".join(script_lines)],
        capture_output=True, text=True
    )
    return result.returncode == 0


def _send_via_smtp_env(to, subject, body_text, body_html="", file_path=""):
    """Send via SMTP credentials from ~/.ai-praxis/.env. Returns ok: bool."""
    env_file = CONFIG_DIR / ".env"
    if not env_file.exists():
        return False

    cfg = {}
    for line in env_file.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            cfg[k.strip()] = v.strip()

    host = cfg.get("SMTP_HOST", "")
    user = cfg.get("SMTP_USER", "")
    pwd  = cfg.get("SMTP_PASS", "")
    if not (host and user and pwd):
        return False

    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.mime.base import MIMEBase
    from email import encoders

    port = int(cfg.get("SMTP_PORT", "587"))
    sender = cfg.get("SMTP_FROM", user)

    msg = MIMEMultipart("alternative")
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject
    msg.attach(MIMEText(body_text, "plain", "utf-8"))
    if body_html:
        msg.attach(MIMEText(body_html, "html", "utf-8"))

    if file_path and Path(file_path).exists():
        msg.attach(_make_mime_attachment(file_path))

    try:
        with smtplib.SMTP(host, port, timeout=15) as s:
            s.ehlo()
            s.starttls()
            s.login(user, pwd)
            s.sendmail(sender, [to], msg.as_bytes())
        return True
    except Exception:
        return False


def _upload_to_catbox(file_path):
    """Upload file to catbox.moe, return public URL or ''."""
    fpath = Path(file_path)
    if not fpath.exists():
        return ""
    try:
        boundary = b"----Boundary7MA4YWxkSkill"
        body = (
            b"--" + boundary + b"\r\n"
            b'Content-Disposition: form-data; name="reqtype"\r\n\r\nfileupload\r\n'
            b"--" + boundary + b"\r\n" +
            f'Content-Disposition: form-data; name="fileToUpload"; filename="{fpath.name}"\r\n'.encode() +
            b"Content-Type: application/octet-stream\r\n\r\n" +
            fpath.read_bytes() +
            b"\r\n--" + boundary + b"--\r\n"
        )
        req = urllib.request.Request(
            "https://catbox.moe/user/api.php", data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary.decode()}"},
            method="POST"
        )
        url = urllib.request.urlopen(req, timeout=20).read().decode().strip()
        return url if url.startswith("http") else ""
    except Exception:
        return ""



# Gmail attachment size limit (bytes) — files above this use cloud upload instead
_GMAIL_ATTACH_LIMIT = 20 * 1024 * 1024  # 20 MB


def _upload_to_transfer_sh(file_path):
    """Upload to transfer.sh (free, no account, 10 GB, 14-day expiry). Returns URL or ''."""
    import urllib.request, urllib.error
    fpath = Path(file_path)
    if not fpath.exists():
        return ""
    try:
        fname = urllib.parse.quote(fpath.name)
        req = urllib.request.Request(
            f"https://transfer.sh/{fname}",
            data=fpath.read_bytes(),
            method="PUT",
        )
        req.add_header("Max-Days", "14")
        resp = urllib.request.urlopen(req, timeout=60)
        url = resp.read().decode().strip()
        return url if url.startswith("http") else ""
    except Exception:
        return ""


def _upload_to_gofile(file_path):
    """Upload to GoFile.io (free, no account, auto-expiry on inactivity). Returns URL or ''."""
    import urllib.request
    fpath = Path(file_path)
    if not fpath.exists():
        return ""
    try:
        # Step 1: get best server
        resp = urllib.request.urlopen("https://api.gofile.io/servers", timeout=10)
        data = json.loads(resp.read())
        server = data["data"]["servers"][0]["name"]

        # Step 2: upload
        boundary = b"----GoFileBoundary"
        body = (
            b"--" + boundary + b"\r\n"
            b'Content-Disposition: form-data; name="file"; filename="' +
            fpath.name.encode() + b'"\r\n'
            b"Content-Type: application/octet-stream\r\n\r\n" +
            fpath.read_bytes() +
            b"\r\n--" + boundary + b"--\r\n"
        )
        req = urllib.request.Request(
            f"https://{server}.gofile.io/contents/uploadfile",
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary.decode()}"},
            method="POST",
        )
        resp = urllib.request.urlopen(req, timeout=60)
        result = json.loads(resp.read())
        if result.get("status") == "ok":
            return result["data"].get("downloadPage", "")
        return ""
    except Exception:
        return ""


def do_send_email(args):
    """Send email with full fallback chain. Zero config for end users.

    Priority:
      1. Resend API  (pre-embedded key, multi-user, no setup needed)
      2. macOS Mail.app  (if user has accounts)
      3. SMTP from ~/.ai-praxis/.env  (if user configured it)
      4. catbox.moe upload + open mailto: + open link  (last resort)

    Large-file strategy (>20 MB or video):
      Auto-upload to transfer.sh → GoFile.io → catbox.moe, send link in body.
    """
    import base64, subprocess, urllib.parse

    to      = args.to
    subject = args.subject or "Praxis 输出结果"
    body    = (args.body or "请查看附件中的内容。\n\n— Praxis").replace("\\n", "\n")
    fpath   = args.file or ""
    furl    = args.url or ""

    # ── Large file / video: upload to cloud, send link instead of attachment ──
    VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v", ".flv", ".wmv"}
    if fpath and Path(fpath).expanduser().exists():
        fp = Path(fpath).expanduser()
        is_video = fp.suffix.lower() in VIDEO_EXTS
        is_large = fp.stat().st_size > _GMAIL_ATTACH_LIMIT
        if is_video or is_large:
            cloud_url = (_upload_to_transfer_sh(str(fp))
                         or _upload_to_gofile(str(fp))
                         or _upload_to_catbox(str(fp)))
            if cloud_url:
                size_mb = fp.stat().st_size / 1024 / 1024
                reason = "视频文件" if is_video else f"文件较大（{size_mb:.1f} MB）"
                body = (body.rstrip() +
                        f"\n\n【{reason}，已上传至云端，点击下载】\n{cloud_url}\n\n"
                        "（链接14天内有效）")
                fpath = ""  # don't attach, send link only

    # Build HTML body if file is HTML
    body_html = ""
    if fpath and Path(fpath).expanduser().suffix.lower() in (".html", ".htm"):
        try:
            body_html = Path(fpath).expanduser().read_text(encoding="utf-8", errors="ignore")
        except Exception:
            pass

    # Build Resend attachment
    attachments = []
    if fpath and Path(fpath).expanduser().exists():
        try:
            content_b64 = base64.b64encode(Path(fpath).expanduser().read_bytes()).decode("ascii")
            attachments = [{"filename": Path(fpath).name, "content": content_b64}]
        except Exception:
            pass

    # ── Step 1: Gmail SMTP (primary) ────────────────────────────────────────
    if SKILL_GMAIL_USER and SKILL_GMAIL_APP_PWD:
        ok = _send_via_gmail(to, subject, body, body_html, fpath,
                             SKILL_GMAIL_USER, SKILL_GMAIL_APP_PWD)
        if ok:
            print(f"EMAIL_SENT:gmail:{SKILL_GMAIL_USER}")
            return

    # ── Step 2: Resend API ──────────────────────────────────────────────────
    config = get_config()
    resend_key = config.get("resend_api_key", "") or SKILL_EMAIL_KEY
    if resend_key:
        ok, msg_id = _send_via_resend(to, subject, body, body_html, attachments, resend_key)
        if ok:
            print(f"EMAIL_SENT:resend:{msg_id}")
            return

    # ── Step 3: macOS Mail.app ──────────────────────────────────────────────
    if sys.platform == "darwin":
        ok = _send_via_mailapp(to, subject, body, fpath)
        if ok:
            print("EMAIL_SENT:mailapp")
            return

    # ── Step 3: Local SMTP credentials ─────────────────────────────────────
    ok = _send_via_smtp_env(to, subject, body, body_html, fpath)
    if ok:
        print("EMAIL_SENT:smtp_env")
        return

    # ── Step 4: catbox.moe + mailto ─────────────────────────────────────────
    file_url = furl
    if not file_url and fpath:
        file_url = _upload_to_catbox(fpath)

    mailto_body = body
    if file_url:
        mailto_body = f"在线查看：{file_url}\n\n{body}"

    mailto = (
        f"mailto:{to}"
        f"?subject={urllib.parse.quote(subject)}"
        f"&body={urllib.parse.quote(mailto_body)}"
    )
    try:
        subprocess.run(["open", mailto], check=False)
        if file_url:
            subprocess.run(["open", file_url], check=False)
    except Exception:
        pass
    print(f"EMAIL_FALLBACK:catbox={file_url or 'none'}")


# --- Init / Onboarding ---

def do_disable(args):
    """Disable ai-praxis auto-trigger."""
    config = get_config()
    config["skill_enabled"] = False
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
    print("DISABLED: praxis 已禁用。Claude Code 将直接处理你的请求，不再自动触发 praxis。")
    print("重新启用：python3 report.py enable")


def do_enable(args):
    """Enable ai-praxis auto-trigger."""
    config = get_config()
    config["skill_enabled"] = True
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
    print("ENABLED: praxis 已启用。描述任务时会自动触发。")


def do_status(args):
    """Print current enabled/disabled status."""
    config = get_config()
    enabled = config.get("skill_enabled", True)
    print("ENABLED" if enabled else "DISABLED")


def do_init(args):
    """Initialize config (called during first-run onboarding)."""
    config = get_config()

    if args.enable_reporting:
        config["report_enabled"] = True
    if args.api_endpoint:
        config["community_api_endpoint"] = args.api_endpoint
    if args.api_key:
        config["api_key"] = args.api_key

    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

    print(f"CONFIG_SAVED:{CONFIG_FILE}")


# ── Skill Registry ────────────────────────────────────────────────────────

SKILL_REGISTRY_FILE = CONFIG_DIR / "skill_registry.json"

# Built-in known skills (seed data)
_BUILTIN_SKILLS = [
    {
        "name": "praxis",
        "description": "Describe any task in natural language — Praxis analyzes intent, installs dependencies, and executes automatically. Covers all industries: development, content creation, data analysis, DevOps, marketing, and more.",
        "author": "AI-flower",
        "repo": "https://github.com/AI-flower/praxis-skill",
        "install_url": "https://raw.githubusercontent.com/AI-flower/praxis-skill/main/install.sh",
        "tags": ["praxis", "automation", "task", "ai", "universal", "claude-code-skill"],
        "version": "0.4.4",
        "stars": 1,
        "source": "builtin",
    },
]


def _load_skill_registry() -> list:
    """Load local skill registry, seeding with builtins if empty."""
    if SKILL_REGISTRY_FILE.exists():
        try:
            data = json.loads(SKILL_REGISTRY_FILE.read_text())
            if isinstance(data, list) and data:
                return data
        except Exception:
            pass
    # Seed with builtins
    SKILL_REGISTRY_FILE.write_text(json.dumps(_BUILTIN_SKILLS, indent=2, ensure_ascii=False))
    return list(_BUILTIN_SKILLS)


def _save_skill_registry(skills: list):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    SKILL_REGISTRY_FILE.write_text(json.dumps(skills, indent=2, ensure_ascii=False))


def _search_github_skills(query: str) -> list:
    """Search GitHub for repos tagged with claude-code-skill or claude-skill."""
    results = []
    topics = ["claude-code-skill", "claude-skill", "praxis-skill"]
    seen = set()
    for topic in topics:
        try:
            q = urllib.parse.quote(f"{query} topic:{topic}")
            url = f"https://api.github.com/search/repositories?q={q}&sort=stars&per_page=5"
            req = urllib.request.Request(url, headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "ai-praxis/0.4",
            })
            data = json.loads(urllib.request.urlopen(req, timeout=10).read())
            for item in data.get("items", []):
                repo_url = item["html_url"]
                if repo_url in seen:
                    continue
                seen.add(repo_url)
                results.append({
                    "name": item["name"],
                    "description": item.get("description") or "",
                    "author": item["owner"]["login"],
                    "repo": repo_url,
                    "install_url": f"https://raw.githubusercontent.com/{item['full_name']}/main/install.sh",
                    "tags": item.get("topics", []),
                    "version": "",
                    "stars": item.get("stargazers_count", 0),
                    "source": "github",
                })
        except Exception:
            pass
    return results


def _search_community_skills(query: str) -> list:
    """Query community API for skills registry."""
    config = get_config()
    endpoint = config.get("community_api_endpoint", "").rstrip("/")
    if not endpoint:
        return []
    try:
        q = urllib.parse.quote(query)
        url = f"{endpoint}/api/skills?q={q}&limit=5"
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {config.get('api_key', '')}",
            "Accept": "application/json",
            "User-Agent": "ai-praxis/0.4",
        })
        data = json.loads(urllib.request.urlopen(req, timeout=8).read())
        return data if isinstance(data, list) else data.get("skills", [])
    except Exception:
        return []


def do_find_skill(args):
    """Search for installable skills from multiple sources."""
    query = (args.query or "").strip()
    results = []

    # 1. Local registry (always)
    local = _load_skill_registry()
    for s in local:
        name = s.get("name", "").lower()
        desc = s.get("description", "").lower()
        tags = " ".join(s.get("tags", [])).lower()
        if not query or any(q in name + desc + tags for q in query.lower().split()):
            s["source"] = s.get("source", "local")
            results.append(s)

    # 2. Community API
    community = _search_community_skills(query)
    seen_names = {r["name"] for r in results}
    for s in community:
        if s.get("name") not in seen_names:
            s["source"] = "community"
            results.append(s)
            seen_names.add(s["name"])

    # 3. GitHub (if online flag or no results yet)
    if args.github or not results:
        gh_results = _search_github_skills(query or "praxis")
        for s in gh_results:
            if s.get("name") not in seen_names:
                results.append(s)
                seen_names.add(s["name"])

    if not results:
        print("NO_SKILLS_FOUND")
        return

    # Relevance sort: exact name match first, then tag match, then stars
    q_lower = query.lower() if query else ""
    def _relevance(s):
        name = s.get("name", "").lower()
        tags = " ".join(s.get("tags", [])).lower()
        desc = s.get("description", "").lower()
        exact = 100 if name == q_lower else 0
        prefix = 50 if q_lower and name.startswith(q_lower) else 0
        tag_hit = 20 if q_lower and q_lower in tags else 0
        desc_hit = 10 if q_lower and q_lower in desc else 0
        builtin = 5 if s.get("source") == "builtin" else 0
        stars = min(s.get("stars", 0), 10)
        return exact + prefix + tag_hit + desc_hit + builtin + stars
    results.sort(key=_relevance, reverse=True)

    # Output
    print(f"SKILLS_JSON:{json.dumps(results, ensure_ascii=False)}")

    if args.pretty:
        print(f"\n找到 {len(results)} 个 skill:\n")
        for i, s in enumerate(results, 1):
            stars = f"⭐ {s['stars']}" if s.get("stars") else ""
            ver = f"v{s['version']}" if s.get("version") else ""
            src = s.get("source", "")
            print(f"  {i}. [{s['name']}] {ver} {stars}")
            print(f"     {s.get('description', '')[:80]}")
            print(f"     作者: {s.get('author', '?')}  来源: {src}")
            print(f"     仓库: {s.get('repo', '')}")
            print()


def do_install_skill(args):
    """Install a skill from GitHub repo URL or known name."""
    import subprocess, shutil

    target = (args.target or "").strip()
    skills_dir = Path.home() / ".claude" / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)

    # Resolve target: name → repo URL from registry
    repo_url = ""
    skill_name = ""
    install_sh = ""

    if target.startswith("http"):
        repo_url = target.rstrip("/")
        skill_name = repo_url.rstrip("/").split("/")[-1]
    else:
        # Look up in registry
        registry = _load_skill_registry()
        for s in registry:
            if s["name"].lower() == target.lower():
                repo_url = s.get("repo", "")
                skill_name = s["name"]
                install_sh = s.get("install_url", "")
                break
        if not repo_url:
            # Try GitHub search
            gh = _search_github_skills(target)
            if gh:
                s = gh[0]
                repo_url = s["repo"]
                skill_name = s["name"]
                install_sh = s.get("install_url", "")

    if not repo_url:
        print(f"INSTALL_FAILED:skill not found: {target}")
        return

    dest = skills_dir / skill_name

    # Try install.sh first if available
    if install_sh:
        try:
            req = urllib.request.Request(install_sh, headers={"User-Agent": "ai-praxis/0.4"})
            sh_content = urllib.request.urlopen(req, timeout=10).read().decode()
            sh_path = Path("/tmp") / f"install_{skill_name}.sh"
            sh_path.write_text(sh_content)
            result = subprocess.run(["bash", str(sh_path)], capture_output=True, text=True, timeout=120)
            if result.returncode == 0:
                print(f"INSTALL_OK:{skill_name}:{dest}")
                # Update local registry with this skill
                registry = _load_skill_registry()
                names = [s["name"] for s in registry]
                if skill_name not in names:
                    registry.append({"name": skill_name, "repo": repo_url, "install_url": install_sh,
                                     "description": "", "author": "", "tags": [], "version": "", "stars": 0})
                    _save_skill_registry(registry)
                return
        except Exception as e:
            pass  # Fall through to git clone

    # Fallback: git clone
    if dest.exists():
        shutil.rmtree(str(dest))
    try:
        result = subprocess.run(
            ["git", "clone", "--depth=1", repo_url, str(dest)],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            print(f"INSTALL_FAILED:{result.stderr[:200]}")
            return
        # Run install.sh inside cloned dir if present
        local_install = dest / "install.sh"
        if local_install.exists():
            subprocess.run(["bash", str(local_install)], capture_output=True, timeout=60)
        print(f"INSTALL_OK:{skill_name}:{dest}")
        # Update registry
        registry = _load_skill_registry()
        names = [s["name"] for s in registry]
        if skill_name not in names:
            registry.append({"name": skill_name, "repo": repo_url, "install_url": "",
                             "description": "", "author": "", "tags": [], "version": "", "stars": 0})
            _save_skill_registry(registry)
    except Exception as e:
        print(f"INSTALL_FAILED:{e}")


def do_register_skill(args):
    """Register (publish) a skill to local registry and optionally community API."""
    registry = _load_skill_registry()
    names = [s["name"] for s in registry]
    entry = {
        "name": args.name,
        "description": args.description or "",
        "author": args.author or "",
        "repo": args.repo or "",
        "install_url": args.install_url or "",
        "tags": [t.strip() for t in (args.tags or "").split(",") if t.strip()],
        "version": args.version or "",
        "stars": 0,
        "source": "local",
    }
    if args.name in names:
        registry = [entry if s["name"] == args.name else s for s in registry]
        print(f"SKILL_UPDATED:{args.name}")
    else:
        registry.append(entry)
        print(f"SKILL_REGISTERED:{args.name}")
    _save_skill_registry(registry)

    # Optionally push to community API
    config = get_config()
    endpoint = config.get("community_api_endpoint", "").rstrip("/")
    if endpoint and config.get("report_enabled"):
        try:
            body = json.dumps(entry).encode()
            req = urllib.request.Request(
                f"{endpoint}/api/skills",
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {config.get('api_key', '')}",
                },
                method="POST",
            )
            urllib.request.urlopen(req, timeout=8)
            print("SKILL_PUBLISHED:community")
        except Exception:
            pass  # Community publish is best-effort


# --- CLI ---

def main():
    parser = argparse.ArgumentParser(description="Praxis Community Reporter")
    subparsers = parser.add_subparsers(dest="command")

    # report
    p_report = subparsers.add_parser("report")
    p_report.add_argument("--intent", default="")
    p_report.add_argument("--industry", default="")
    p_report.add_argument("--category", default="")
    p_report.add_argument("--tags", default="")
    p_report.add_argument("--steps", default="0")
    p_report.add_argument("--steps-completed", default="")
    p_report.add_argument("--steps-failed", default="0")
    p_report.add_argument("--success", default="false")
    p_report.add_argument("--skills-used", default="")
    p_report.add_argument("--tools-used", default="")
    p_report.add_argument("--auto-fixes", default="")
    p_report.add_argument("--duration", default="0")

    # query
    p_query = subparsers.add_parser("query")
    p_query.add_argument("query", nargs="?", default="")

    # query-popular
    p_popular = subparsers.add_parser("query-popular")
    p_popular.add_argument("--industry", default="")

    # discard-task (Phase 3 rejection or off-topic — no upload)
    subparsers.add_parser("discard-task")

    # save-intent (Phase 1 early recording)
    p_save = subparsers.add_parser("save-intent")
    p_save.add_argument("--intent", default="")
    p_save.add_argument("--industry", default="")
    p_save.add_argument("--category", default="")
    p_save.add_argument("--tags", default="")
    p_save.add_argument("--original-input", default="")
    p_save.add_argument("--tech-stack", default="", help="Comma-separated tech stack, e.g. Python,FastAPI,React")
    p_save.add_argument("--project-type", default="", help="Project type: web_app/cli_tool/data_pipeline/automation/content/other")

    # update-result (Phase 5 result update)
    p_update = subparsers.add_parser("update-result")
    p_update.add_argument("--steps", default="0")
    p_update.add_argument("--steps-completed", default="")
    p_update.add_argument("--steps-failed", default="0")
    p_update.add_argument("--success", default="false")
    p_update.add_argument("--skills-used", default="")
    p_update.add_argument("--tools-used", default="")
    p_update.add_argument("--auto-fixes", default="")
    p_update.add_argument("--duration", default="0")
    p_update.add_argument("--output-summary", default="")
    p_update.add_argument("--deliverables", default="")
    p_update.add_argument("--output-file", default="")
    p_update.add_argument("--error-message", default="", help="Error description when task failed")
    p_update.add_argument("--based-on", default="", help="Solution ID this task was based on (for auto-feedback)")
    p_update.add_argument("--artifacts-json", default="", help="JSON array of artifact objects from session-recorder v1.5.0")
    p_update.add_argument("--execution-plan", default="", help="Structured execution plan text from Phase 2")
    p_update.add_argument("--error-detail", default="", help="Structured error: [Turn N] error_type: message")
    p_update.add_argument("--skills-detail", default="", help="JSON array of skill detail objects")
    p_update.add_argument("--user-confirmed", default="false", help="Whether user confirmed the plan in Phase 3 (true/false). Only confirmed tasks are uploaded.")

    # feedback
    p_feedback = subparsers.add_parser("feedback")
    p_feedback.add_argument("solution_id", help="Solution UUID to give feedback on")
    p_feedback.add_argument("--type", required=True, choices=["upvote", "downvote"],
                            help="Feedback type: upvote or downvote")

    # upload-pending
    subparsers.add_parser("upload-pending")

    # search-solutions (Phase 0)
    p_search = subparsers.add_parser("search-solutions")
    p_search.add_argument("query", nargs="?", default="")
    p_search.add_argument("--limit", default="3")
    p_search.add_argument("--min-score", default="0.5")

    # save-solution (Phase 5 extra)
    p_save_sol = subparsers.add_parser("save-solution")
    p_save_sol.add_argument("--summary", default="")
    p_save_sol.add_argument("--industry", default="")
    p_save_sol.add_argument("--category", default="")
    p_save_sol.add_argument("--tags", default="")
    p_save_sol.add_argument("--steps", default="0")
    p_save_sol.add_argument("--success", default="true")
    p_save_sol.add_argument("--output-summary", default="")
    p_save_sol.add_argument("--deliverables", default="")
    p_save_sol.add_argument("--required-capabilities", default="")
    p_save_sol.add_argument("--artifacts-summary", default="", help="Comma-separated artifact types, e.g. code,design_spec")
    p_save_sol.add_argument("--tech-stack", default="", help="Comma-separated tech stack, e.g. Python,FastAPI,React")

    # detect-capabilities (Phase 1.5)
    p_detect = subparsers.add_parser("detect-capabilities")
    p_detect.add_argument("--intent", default="")
    p_detect.add_argument("--tags", default="")
    p_detect.add_argument("--input", default="")

    # install-capability (Phase 1.5)
    p_install = subparsers.add_parser("install-capability")
    p_install.add_argument("capability", help="Capability name from CAPABILITY_CATALOG")

    # list-capabilities
    subparsers.add_parser("list-capabilities")

    # update-catalog
    subparsers.add_parser("update-catalog")

    # detect-locale
    p_locale = subparsers.add_parser("detect-locale")
    p_locale.add_argument("--save", action="store_true", help="Save detected locale to preferences")

    # get-preferences
    p_get_prefs = subparsers.add_parser("get-preferences")
    p_get_prefs.add_argument("--key", default="", help="Optional key to retrieve (supports a.b notation)")

    # set-preference
    p_set_pref = subparsers.add_parser("set-preference")
    p_set_pref.add_argument("--key", required=True)
    p_set_pref.add_argument("--value", required=True)

    # task rollback
    p_track = subparsers.add_parser("track-change")
    p_track.add_argument("--task-id", required=True)
    p_track.add_argument("--type", required=True)
    p_track.add_argument("--path", default="")
    p_track.add_argument("--package", default="")

    p_list_tasks = subparsers.add_parser("list-tasks")
    p_list_tasks.add_argument("--limit", type=int, default=20)

    p_rollback = subparsers.add_parser("rollback")
    p_rollback.add_argument("task_id", nargs="?", default="last")
    p_rollback.add_argument("--dry-run", action="store_true")

    # send-email (via backend relay, zero-config for all users)
    p_email = subparsers.add_parser("send-email")
    p_email.add_argument("--to", required=True, help="Recipient email address")
    p_email.add_argument("--subject", default="", help="Email subject")
    p_email.add_argument("--body", default="", help="Plain text body")
    p_email.add_argument("--file", default="", help="File path to attach (HTML/PDF/etc)")
    p_email.add_argument("--url", default="", help="Public URL of file as fallback link")

    # disable / enable / status
    subparsers.add_parser("disable")
    subparsers.add_parser("enable")
    subparsers.add_parser("status")

    # find-skill
    p_find_skill = subparsers.add_parser("find-skill")
    p_find_skill.add_argument("query", nargs="?", default="", help="Search keyword(s)")
    p_find_skill.add_argument("--github", action="store_true", help="Also search GitHub")
    p_find_skill.add_argument("--pretty", action="store_true", help="Human-readable output")

    # install-skill
    p_install_skill = subparsers.add_parser("install-skill")
    p_install_skill.add_argument("target", help="Skill name or GitHub repo URL")

    # register-skill (publish your skill to registry)
    p_reg_skill = subparsers.add_parser("register-skill")
    p_reg_skill.add_argument("--name", required=True)
    p_reg_skill.add_argument("--description", default="")
    p_reg_skill.add_argument("--author", default="")
    p_reg_skill.add_argument("--repo", default="")
    p_reg_skill.add_argument("--install-url", default="")
    p_reg_skill.add_argument("--tags", default="")
    p_reg_skill.add_argument("--version", default="")

    # init
    p_init = subparsers.add_parser("init")
    p_init.add_argument("--enable-reporting", action="store_true")
    p_init.add_argument("--api-endpoint", default="")
    p_init.add_argument("--api-key", default="")

    args = parser.parse_args()

    if args.command == "report":
        do_report(args)
    elif args.command == "save-intent":
        do_save_intent(args)
    elif args.command == "update-result":
        do_update_result(args)
    elif args.command == "query":
        do_query(args)
    elif args.command == "query-popular":
        do_query_popular(args)
    elif args.command == "feedback":
        do_feedback(args)
    elif args.command == "upload-pending":
        do_upload_pending(args)
    elif args.command == "send-email":
        do_send_email(args)
    elif args.command == "disable":
        do_disable(args)
    elif args.command == "enable":
        do_enable(args)
    elif args.command == "status":
        do_status(args)
    elif args.command == "init":
        do_init(args)
    elif args.command == "search-solutions":
        do_search_solutions(args)
    elif args.command == "save-solution":
        do_save_solution(args)
    elif args.command == "detect-capabilities":
        do_detect_capabilities(args)
    elif args.command == "install-capability":
        do_install_capability(args)
    elif args.command == "list-capabilities":
        do_list_capabilities(args)
    elif args.command == "update-catalog":
        do_update_catalog(args)
    elif args.command == "detect-locale":
        do_detect_locale(args)
    elif args.command == "get-preferences":
        do_get_preferences(args)
    elif args.command == "set-preference":
        do_set_preference(args)
    elif args.command == "track-change":
        do_track_change(args)
    elif args.command == "list-tasks":
        do_list_tasks(args)
    elif args.command == "rollback":
        do_rollback(args)
    elif args.command == "find-skill":
        do_find_skill(args)
    elif args.command == "install-skill":
        do_install_skill(args)
    elif args.command == "register-skill":
        do_register_skill(args)
    elif args.command == "discard-task":
        do_discard_task(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
