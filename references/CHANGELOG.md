# Praxis — Changelog

## [0.4.4] - 2026-03-18

### Fixed
- **`_run_cmd` 支持 shell 管道**：capability check_command 含 `|`/`&&`/`>`  时自动使用 `shell=True`，修复5个 MCP/Skill 检测命令静默失败
- **`required_capabilities` 持久化**：新增 `track-capability` 命令，Phase 1.5 安装的能力写入 `current_task.json`，`finalize-task` 和方案库正确保存
- **`sanitize` 不再破坏本地路径**：路径脱敏改为正则替换用户名（`/Users/ylzs/` → `/Users/[USER]/`），保留路径结构
- **`locale.getdefaultlocale()` 替换**：两处调用改为 `locale.getlocale()`，兼容 Python 3.13+
- **`current_task.json` 并发安全**：`confirm-task`/`track-progress`/`track-capability`/`save-intent` 均使用 `_atomic_task_update` 文件锁

### Added
- `track-capability` CLI 子命令
- `_atomic_task_update()` 通用文件锁辅助函数

## [0.4.3] - 2026-03-18

### Fixed
- **Critical NameError**：`user_confirmed` 改名 `arg_confirmed` 但下游引用漏改
- **`user_confirmed` 被 argparse 默认值覆盖**：`--user-confirmed` 默认值改为空字符串，保留 `confirm-task` 写入的值
- **Phase 5 遗忘提交**：新增 CRITICAL RULE #7 强制 Phase 5 执行；新增 `finalize-task` 幂等命令（含文件锁）
- **UserPromptSubmit hook**：新增 `hook_user_prompt.py` 做代码级任务检测
- **Phase 3 `confirm-task` 传递 `--based-on`**：方案复用时正确关联原方案 ID
- **两份 SKILL.md 同步**
- **版本号统一到 0.4.3**

### Added
- `finalize-task` CLI 子命令（Phase 5 QUICK PATH）
- `hook_user_prompt.py`（UserPromptSubmit hook）
- `install.sh` 自动注入 CLAUDE.md + 注册 hooks

## [0.3.1] - 2026-03-11

### Fixed
- **Output 数据上传缺失**：`transform_to_api_payload` 现在将 AI 生成的完整输出内容（`output_content`）和交付物列表（`deliverables`）一并上传至服务端，之前这两个字段仅存本地

## [0.3.0] - 2026-03-11

### Added
- **Solution Replay Protocol**: 方案复用时走4阶段结构化流程（依赖检查 → 适配分析 → 逐步执行 → 偏差处理），详见 `references/solution-replay-protocol.md`
- **Adaptive Communication（领域自适应沟通）**: 每次请求自动检测领域水平（beginner/intermediate/expert），调整计划和报告的表达方式，跨会话持久化
- **Auto-Execute Mode（自动执行模式）**: 说"全自动"/"放开权限"等触发词即可跳过 Phase 3 确认，设置持久化到 `~/.ai-praxis/preferences.json`
- **2个新CLI命令**: `get-preferences` / `set-preference`，读写用户偏好（auto_execute、domain_familiarity）

### Changed
- Phase 0 方案选择分支改为调用 Solution Replay Protocol
- Phase 2 之前新增 Adaptive Communication + Auto-Execute 两个章节

## [0.2.0] - 2026-03-11

### Added
- **Phase 0 — Solution Library Search**: 每次执行前搜索历史方案库，相似度≥50%自动推荐，用户可直接复用
- **Phase 1.5 — Capability Auto-Detection & Install**: 根据需求关键词自动识别并静默安装所需工具（pip/MCP/Skill）
- **Phase 5c — Auto Save Solution**: 任务成功后自动存入方案库，带 required_capabilities 字段
- **CAPABILITY_CATALOG**: 内置12种能力映射（playwright/pandas/requests/weasyprint/pillow/openai/sqlite/brave-search/notion/filesystem/write-novel/figma-to-react）
- **6个新CLI命令**: search-solutions / save-solution / detect-capabilities / install-capability / list-capabilities / update-catalog
- **本地方案库**: ~/.ai-praxis/solution_library.json（最多500条）
- **版本管理**: install.sh / uninstall.sh / plugin.json

### Changed
- SKILL.md 新增 Phase 0 和 Phase 1.5 两个执行阶段
- report.py 新增 SOLUTION_LIBRARY_FILE、CAPABILITY_CATALOG_FILE 常量

## [0.1.0] - 2026-03-10

### Initial Release
- 核心执行流程：Intent Analysis → Plan Generation → One-Time Confirmation → Autonomous Execution → Result Report
- Auto-Fix 策略：已知修复 → 自动安装 → 替代方案 → LLM 推理 → 跳过
- Credential 自动解析链（.env → keychain → 环境变量 → 自动注册 → 询问用户）
- Community API 集成：report / query / query-popular / save-intent / update-result / feedback
- send-email 邮件发送（Resend → Mail.app → SMTP → catbox.moe 四级降级）
- Discovery Mode：环境检测 + 推荐 Top 5 场景
- disable / enable / status 开关命令
- 多语言支持（中/英自动检测）
