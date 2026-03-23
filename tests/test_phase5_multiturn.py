#!/usr/bin/env python3
"""
Regression checks for the Phase 5 multi-turn persistence bug.

This script validates two critical paths with an isolated HOME:
1. `finalize-task` preserves confirmation/progress/capability data after multiple updates.
2. `hook_post_skill.py` can synthesize/finalize a report when Phase 5 is skipped.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT_SCRIPT = REPO_ROOT / "scripts" / "report.py"
HOOK_SCRIPT = REPO_ROOT / "scripts" / "hook_post_skill.py"


def run_python(script: Path, args: list[str], home: Path, stdin_text: str | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PYTHONUTF8"] = "1"
    return subprocess.run(
        [sys.executable, str(script), *args],
        input=stdin_text,
        text=True,
        capture_output=True,
        cwd=str(REPO_ROOT),
        env=env,
        check=False,
    )


def assert_ok(result: subprocess.CompletedProcess[str], expected_fragment: str) -> None:
    if result.returncode != 0:
      raise AssertionError(
          f"command failed: rc={result.returncode}\nstdout={result.stdout}\nstderr={result.stderr}"
      )
    if expected_fragment not in result.stdout:
      raise AssertionError(
          f"missing expected fragment {expected_fragment!r}\nstdout={result.stdout}\nstderr={result.stderr}"
      )


def load_single_report(home: Path) -> dict:
    pending_dir = home / ".ai-praxis" / "pending_reports"
    reports = sorted(pending_dir.glob("*.json"))
    if len(reports) != 1:
        raise AssertionError(f"expected exactly 1 pending report, got {len(reports)} in {pending_dir}")
    return json.loads(reports[0].read_text())


def test_finalize_task_multiturn() -> None:
    with tempfile.TemporaryDirectory(prefix="praxis-phase5-finalize-") as tmp:
        home = Path(tmp)
        output_file = home / "result.md"
        output_file.write_text("# done\n\nartifact content")

        save_intent = run_python(
            REPORT_SCRIPT,
            [
                "save-intent",
                "--intent", "Verify Phase 5 multiturn persistence",
                "--industry", "software",
                "--category", "regression",
                "--tags", "phase5,test,multiturn",
                "--original-input", "please ensure phase 5 survives multi-turn updates",
                "--tech-stack", "python,praxis",
                "--project-type", "automation",
            ],
            home,
        )
        assert_ok(save_intent, "INTENT_SAVED:")

        current_task = json.loads((home / ".ai-praxis" / "current_task.json").read_text())
        task_id = current_task["task_id"]

        assert_ok(run_python(REPORT_SCRIPT, ["confirm-task", "--based-on", "sol-123"], home), "TASK_CONFIRMED")
        assert_ok(
            run_python(
                REPORT_SCRIPT,
                ["track-progress", "--step-completed", "read source", "--deliverable", "src/app.ts", "--tool-used", "Read"],
                home,
            ),
            "PROGRESS:1",
        )
        assert_ok(
            run_python(
                REPORT_SCRIPT,
                ["track-progress", "--step-completed", "write patch", "--deliverable", "src/app.ts", "--tool-used", "Edit"],
                home,
            ),
            "PROGRESS:2",
        )
        assert_ok(run_python(REPORT_SCRIPT, ["track-capability", "--name", "playwright"], home), "CAPABILITY_TRACKED:playwright")

        finalize = run_python(
            REPORT_SCRIPT,
            [
                "finalize-task",
                "--success", "true",
                "--output-summary", "Patched app.ts after multi-turn analysis.",
                "--output-file", str(output_file),
            ],
            home,
        )
        assert_ok(finalize, "FINALIZED")

        current_task_path = home / ".ai-praxis" / "current_task.json"
        if current_task_path.exists():
            raise AssertionError("current_task.json should be removed after finalize-task")

        report = load_single_report(home)
        assert report["task_id"] == task_id
        assert report["user_confirmed"] is True
        assert report["based_on_solution_id"] == "sol-123"
        assert report["plan"]["steps_count"] == 2
        assert sorted(report["plan"]["tools_used"]) == ["Edit", "Read"]
        assert report["result"]["success"] is True
        assert report["output"]["summary"] == "Patched app.ts after multi-turn analysis."
        assert "src/app.ts" in report["output"]["deliverables"]
        assert report["installed_capabilities"] == ["playwright"]
        assert report["output"]["full_content_file"]
        assert Path(report["output"]["full_content_file"]).exists()


def test_hook_fallback_multiturn() -> None:
    with tempfile.TemporaryDirectory(prefix="praxis-phase5-hook-") as tmp:
        home = Path(tmp)

        assert_ok(
            run_python(
                REPORT_SCRIPT,
                [
                    "save-intent",
                    "--intent", "Hook fallback should persist multi-turn data",
                    "--industry", "software",
                    "--category", "regression",
                    "--tags", "phase5,hook,multiturn",
                    "--original-input", "simulate a skipped phase 5 path",
                ],
                home,
            ),
            "INTENT_SAVED:",
        )
        current_task = json.loads((home / ".ai-praxis" / "current_task.json").read_text())
        task_id = current_task["task_id"]

        assert_ok(run_python(REPORT_SCRIPT, ["confirm-task"], home), "TASK_CONFIRMED")
        assert_ok(
            run_python(
                REPORT_SCRIPT,
                ["track-progress", "--step-completed", "inspect error", "--deliverable", "README.md", "--tool-used", "Read"],
                home,
            ),
            "PROGRESS:1",
        )
        assert_ok(
            run_python(
                REPORT_SCRIPT,
                ["track-progress", "--step-completed", "prepare retry", "--deliverable", "scripts/report.py", "--tool-used", "Edit"],
                home,
            ),
            "PROGRESS:2",
        )

        hook_payload = json.dumps({
            "hook_event_name": "Stop",
            "session_id": "test-session",
            "transcript_path": "",
        })
        hook_result = run_python(HOOK_SCRIPT, [], home, stdin_text=hook_payload)
        if hook_result.returncode != 0:
            raise AssertionError(
                f"hook failed: rc={hook_result.returncode}\nstdout={hook_result.stdout}\nstderr={hook_result.stderr}"
            )

        current_task_path = home / ".ai-praxis" / "current_task.json"
        if current_task_path.exists():
            raise AssertionError("hook fallback should remove current_task.json after finalization")

        report = load_single_report(home)
        assert report["task_id"] == task_id
        assert report["user_confirmed"] is True
        assert report["result"]["success"] is True
        assert report["plan"]["steps_count"] == 2
        assert sorted(report["plan"]["tools_used"]) == ["Edit", "Read"]
        assert "README.md" in report["output"]["deliverables"]
        assert report["status"] in {"completed", "completed_by_hook"}


def main() -> None:
    test_finalize_task_multiturn()
    print("PASS finalize-task multi-turn persistence")
    test_hook_fallback_multiturn()
    print("PASS hook fallback multi-turn persistence")


if __name__ == "__main__":
    main()
