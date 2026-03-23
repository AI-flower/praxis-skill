# Solution Replay Protocol

When the user selects an existing solution in Phase 0, follow this 4-stage protocol
instead of jumping directly to execution.

---

## Stage 1: Dependency Check

Read `required_capabilities` from the selected solution. For each capability:

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/report.py install-capability {cap} 2>/dev/null
```

- `ALREADY_INSTALLED` → continue
- `INSTALL_OK` → installed, continue
- `INSTALL_FAILED` → log silently, continue (fallback handled in Stage 3)
- `UNKNOWN_CAPABILITY` → skip

---

## Stage 2: Adaptation Analysis

Compare the stored solution's `summary` with the user's current request.
Identify differences and present them **before executing**.

### Detection Rules

| Difference Type | Example | Adaptation |
|----------------|---------|-----------|
| Named entity (city/country/person) | 杭州 → 上海 | Replace parameter |
| Time reference | 明天 → 后天 | Replace parameter |
| Scale/quantity | 3天 → 7天 | Replace parameter |
| Domain shift | 天气 → 股价 | Flag as major — present for confirmation |
| Output format | 文字输出 → PDF | Add output step |

### Presentation (Normal Mode)

```
## 适配分析

基于方案「{solution.summary}」，为你的需求做以下调整：

| 原方案 | 你的需求 | 调整类型 |
|--------|---------|---------|
| {original} | {adapted} | {type} |

直接执行？
```

### Auto-Execute Mode

Skip the presentation. Apply detected adaptations automatically and proceed.

### Major Deviation

If the domain shifts significantly (e.g., weather → stock prices), even in
Auto-Execute mode, show a one-line notice:
`⚠️ 方案与需求差异较大，已适配后执行，如有问题可告知调整。`

---

## Stage 3: Adapted Execution

Execute the solution's steps with adaptations applied:

- Replace all detected parameters with current-context values
- Follow the same tool/API sequence as the original solution
- Record `based_on_solution_id` via `confirm-task --based-on` in Phase 3

If a required tool failed to install in Stage 1, attempt an alternative tool
(follow the standard Auto-Fix strategy from Phase 4).

---

## Stage 4: Deviation Handling

If any step produces output that differs significantly from what the original
solution expected:

1. Note the deviation in the execution log (do NOT surface to user mid-task)
2. Apply best-effort adaptation and continue
3. Include a brief deviation summary in the Phase 5 result report:

```
### 与原方案的差异
- {step}: {what changed and why}
```

---

## Based-On Tracking

Always pass `--based-on {solution.id}` to `confirm-task` in Phase 3 so the
system can send automatic upvote/downvote feedback to the solution community.
Both `finalize-task` and `update-result` will read `based_on_solution_id` from `current_task.json`.
