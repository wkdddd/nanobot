# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

nanobot is a lightweight, open-source AI agent framework written in Python with a React/TypeScript WebUI. It centers around a small agent loop that receives messages from chat channels, invokes an LLM provider, executes tools, and manages session memory.

## Development Commands

```bash
# Setup
pip install -e ".[dev]"

# Python: run single test / lint
pytest tests/test_openai_api.py::test_function -v
ruff check nanobot/

# Format ONLY files you changed (never the whole codebase — see Gotchas)
ruff format <files-you-changed>

# Review WebUI: dev server (proxies API/WS to gateway :8765), build
cd review-webui && bun run dev      # or NANOBOT_API_URL=... bun run dev
cd review-webui && bun run build

# Gateway
nanobot gateway
```

## High-Level Architecture

### Core Data Flow

Messages flow through an async `MessageBus` (`nanobot/bus/queue.py`) that decouples chat channels from the agent core:

1. **Channels** (`nanobot/channels/`) receive messages from external platforms and publish `InboundMessage` events to the bus.
2. **`AgentLoop`** (`nanobot/agent/loop.py`) consumes inbound messages, builds context, and coordinates the turn.
3. **`AgentRunner`** (`nanobot/agent/runner.py`) handles the actual LLM conversation loop: send messages to the provider, receive tool calls, execute tools, and stream responses.
4. Responses are published as `OutboundMessage` events back to the appropriate channel.

### Key Subsystems

- **Agent Core** (`nanobot/agent/loop.py`, `runner.py`, `subagent.py`): The critical path.
  - `AgentLoop` is a state machine (`RESTORE→COMPACT→COMMAND→BUILD→RUN→SAVE→RESPOND→DONE`) driven by a `_TRANSITIONS` table. Changes here should be minimal and justified.
  - `AgentRunner` is the pure LLM-tool executor — takes `AgentRunSpec`, returns `AgentRunResult`. No product logic lives here.
  - `SubagentManager` (`subagent.py`) spawns concurrent subagents as background tasks; results are injected mid-turn via the message bus into pending queues.
- **Hooks** (`nanobot/agent/hooks/`): Observer-pattern lifecycle callbacks for the runner.
  - `AgentHook` base: `before_iteration`, `on_stream`, `before_execute_tools`, `after_iteration`, `finalize_content`.
  - `CompositeHook` fans out to multiple hooks with per-hook error isolation (`reraise` flag).
  - `AgentProgressHook` (`hooks/progress.py`) translates runner events into channel UI signals (streaming deltas, tool hints, reasoning blocks).
  - `ReviewFinalizerHook` (`hooks/review_finalizer.py`) ingests subagent findings and renders the final review report via `finalize_content`.
  - To add behavior: subclass `AgentHook`, pass instance to `AgentLoop(..., hooks=[...])`.
- **Review Pipeline** (`nanobot/agent/review/`): Multi-subagent code review system.
  - `beforeplan/`: normalizes user inputs (`normalizers.py`), maps depth to policy (`policy.py`: quick=2 subagents, full=4, deep=6+), extracts GitHub/local targets (`targets.py`).
  - `planner.py`: builds `ReviewPlan` from target + depth + focus.
  - `finalizer.py`: ingests subagent findings, applies `ReviewJudge` for confidence filtering, renders final markdown report.
  - `tools/review_submit.py`: subagent-scoped tool for structured findings (required: severity/file/line/title/evidence/impact/recommendation).
  - Review subagent system prompt: `nanobot/templates/agent/review_subagent_system.md`.
  - Flow: plan → spawn subagents → each calls `review_submit` → `ReviewFinalizerHook` ingests → judge → render.
- **LLM Providers** (`nanobot/providers/`): Provider implementations built on a common base (`base.py`). `factory.py` and `registry.py` handle instantiation and model discovery.
- **Channels** (`nanobot/channels/`): Platform integrations auto-discovered via `pkgutil` scan + entry-point plugins. `manager.py` discovers and coordinates them. Each channel file should be self-contained.
- **Tools** (`nanobot/agent/tools/`): Agent capabilities exposed to the LLM, auto-discovered via `pkgutil` scan + entry-point plugins.
- **Memory** (`nanobot/agent/memory.py`): Session history persistence with atomic writes (temp file + fsync + rename). Do not replace with plain `open(..., "w")`.
- **Session Management** (`nanobot/session/`): Per-session history, context compaction, TTL-based auto-compaction, and sustained goal state tracking.
- **Config** (`nanobot/config/schema.py`, `loader.py`): Pydantic-based configuration loaded from `~/.nanobot/config.json`. Supports `${VAR}` env-var substitution (no default-value syntax; missing var raises `ValueError`).
- **Prompt Templates** (`nanobot/templates/`): Jinja2 markdown files that define agent behavior. Changes here alter agent behavior as directly as changing Python code.
- **Skills** (`nanobot/skills/`): Built-in skill definitions (markdown + YAML frontmatter). Agent know-how should be added as skills, not hardcoded into the agent loop.
- **Review WebUI** (`review-webui/`): Vite + React + Tailwind SPA. Build outputs to `nanobot/web/dist/` (bundled into the Python wheel).
- **API Server** (`nanobot/api/server.py`): OpenAI-compatible HTTP API (`/v1/chat/completions`, `/v1/models`).

### Entry Points

- **CLI**: `nanobot/cli/commands.py`
- **Python SDK**: `nanobot/nanobot.py`

## Design Constraints

- **Core stays small; extend at the edges.** New capabilities go in `channels/`, `tools/`, skills, or MCP servers — not inlined into `agent/loop.py` or `runner.py`.
- **Prefer duplication over premature abstraction.** Channels and providers may repeat similar logic. Do not introduce complex base classes just to DRY them.
- **Explicit over magical.** Config must be declared in Pydantic models. Provider resolution must be traceable from factory to concrete class.
- **Minimal change that solves the real problem.** Do not bundle unrelated refactors into a bugfix.

## Security Rules

- All filesystem tools must resolve paths through `_resolve_path` (`agent/tools/filesystem.py`) which enforces workspace boundaries.
- All outbound HTTP from tools must pass through `validate_url_target` (`security/network.py`) — blocks private addresses and cloud metadata endpoints. Do not add direct `httpx.get`/`requests.get` in tools.
- Shell execution respects `restrict_to_workspace`; if enabled, commands outside workspace are rejected before execution.

## Gotchas

- **Do NOT run `ruff format` on the whole codebase.** It destroys git blame history. Only format files you actually changed.
- **Windows compatibility is required.** `ExecTool` uses `cmd /c` on Windows. CLI forces UTF-8 stdout/stderr. MCP paths are normalized. Always use `pathlib.Path`; do not assume `/` separators.
- **Prompt templates are runtime code.** Changes to `nanobot/templates/*.md` alter agent behavior directly. Treat them like code: keep changes narrow, add regression tests.
- **Context pollution persists.** Anything written into memory/session history can be replayed into future LLM calls. Sanitize metadata before it becomes a model example.
- **Heartbeat uses virtual tool calls.** The heartbeat service injects a structured `heartbeat` tool (`action: skip | run`), not free-text parsing. Follow this pattern for new periodic checks.
- **Atomic session writes.** `agent/memory.py` uses temp file + fsync + rename for crash safety. Do not simplify to plain file writes.

## Branching Strategy

| Your Change | Target Branch |
|-------------|---------------|
| New feature | `nightly` |
| Bug fix | `main` |
| Documentation | `main` |
| Refactoring | `nightly` |
| Unsure | `nightly` |

See [`CONTRIBUTING.md`](./CONTRIBUTING.md) for details on the two-branch model and cherry-pick workflow.

## Code Style

- Python 3.11+, asyncio throughout.
- Line length: 100.
- Linting: `ruff check` with rules E, F, I, N, W (E501 ignored).
- pytest with `asyncio_mode = "auto"`.
- Tests mirror the `nanobot/` package structure.

## Collaboration Guidelines

- 遇到不合适的设计要给出具体建议；不清楚的地方必须提问，不要猜测
- 给出具体的修改位置和修改思路，不只是描述问题
- 不需要兼容旧参数/旧配置，直接改
- 审视时覆盖前后端细节、文档、配置，不要只看单个文件
- 日志要完整（错误信息 + 关键节点 info），但不要泛滥
