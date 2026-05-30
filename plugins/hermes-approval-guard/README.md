# hermes-approval-guard

Two-stage semantic approval plugin for Hermes Agent via the `pre_tool_call`
hook. Covers ALL 25+ tools — `write_file`, `patch`, `delegate_task`,
`execute_code`, and `terminal` — filling the gap left by the built-in
`approvals.mode` (terminal only). Disabled by default; zero measurable
overhead when off.

## Quick Start

```yaml
# ~/.hermes/config.yaml
plugins:
  enabled:
    - hermes-approval-guard

plugin_guard:
  enabled: true
  provider: zjic
  model: qwen-3.6-27b-fast
  fail_open: true

approvals:
  mode: off   # plugin handles all tools; system HARDLINE remains active
```

Restart Hermes, then verify:

```bash
hermes plugins list | grep approval-guard
```

## Architecture

```
Tool call → pre_tool_call hook
  ├─ SAFE_TOOLS (20+ read/query tools) → ALLOW (0ms)
  ├─ Terminal with no DANGEROUS regex match → ALLOW (0ms, fast-path)
  ├─ Stage 1: LLM fast-classify → ALLOW / ESCALATE (~500ms)
  │   • Uses call_llm(task="approval") with context-aware prompt
  │   • NEVER outputs DENY — DENY reserved for Stage 2 / system HARDLINE
  │   • On ALLOW: calls approve_session() to pre-mark DANGEROUS patterns
  │     → system's check_all_command_guards skips redundant LLM call
  └─ Stage 2: ACP Agent deep review (3-8s, optional)
      • Stateless: no persistent session; all context injected in prompt
      • Context from SessionDB: conversation + full tool call chain
      • Hindsight-backed: session-level + cross-session pattern memory
      • Outputs: ALLOW / DENY / MODIFY with structured JSON feedback
```

**System HARDLINE** (`rm -rf /`, `mkfs`, `dd`, `shutdown`) always active as
safety net — cannot be bypassed by the plugin or `approvals.mode: off`.

## Design Principles

| Principle | Explanation |
|-----------|-------------|
| **No hardcoded DENY in Stage 1** | Risk signals extracted as LLM context only — never hard-block. DENY authority lives in Stage 2 ACP and system HARDLINE. |
| **LLM: ALLOW / ESCALATE only** | Stage 1 prompt mirrors system's `_smart_approve` ("many flagged commands are false positives, e.g. `python -c print`"). Only outputs ALLOW or ESCALATE. |
| **Stateless ACP** | No `--resume`, no persistent session. All context (conversation, tool chain, pattern history) injected into each ACP prompt from SessionDB and Hindsight. Zero shared state → concurrent-safe. |
| **Fail-open** | Broken guard is never more restrictive than no guard. All LLM failures default to ALLOW (`fail_open: true`). |
| **Terminal fast-path** | Commands with no DANGEROUS regex match (e.g. `git status`, `ls`) skip all LLM calls — 0ms overhead. |
| **approve_session pre-marking** | When Stage 1 approves a terminal command, patterns are pre-marked via system's `approve_session()`. System's `check_all_command_guards` runs after and skips redundant LLM. |

## Stage 1 — Context Extraction + LLM

| Path | Latency | Description |
|------|:------:|-------------|
| SAFE_TOOLS bypass | 0ms | `read_file`, `search_files`, `web_search`, `session_search`, etc. |
| Terminal fast-path | 0ms | Commands with zero DANGEROUS regex matches |
| Context extraction | <1ms | Extracts risk signals from system's `detect_dangerous/hardline_command()`; never blocks |
| LLM classify | ~500ms | `call_llm(task="approval")` — ALLOW / ESCALATE only |

## Stage 2 — ACP Agent (optional)

Launches `hermes chat -q --profile approval`. Prompt has 5 sections:

1. **Recent conversation** — User messages + Agent responses (from SessionDB)
2. **Current operation** — Tool name, args, risk signals
3. **Tool call chain** — Full tool history including SAFE_TOOLS (from SessionDB)
4. **Session approval history** — Previous ACP decisions this session (from Hindsight)
5. **Cross-session patterns** — Similar operations' ALLOW/DENY history (from Hindsight)

Enable with `plugin_guard.stage2.enabled: true`.

## Relationship with System approvals.mode

```
Plugin pre_tool_call hook  → ALL tools (semantic review + pre-marking)
System check_all_command_guards → terminal HARDLINE safety net (always active)
```

| Aspect | Built-in `approvals.mode` | This plugin |
|--------|---------------------------|-------------|
| Coverage | `terminal` only | ALL 25+ tools |
| Decision | Regex + zero-context LLM (16 tokens) | Semantic LLM + ACP agent with full session context |
| Memory | Session-level (`_session_approved`) | Hindsight cross-session pattern bank |
| Context | None (command string only) | Conversation + tool chain + historical patterns |
| Denial feedback | "BLOCKED: xxx" | Structured: reason + alternatives + approval_id |

## Failure Modes

| Failure | Behavior |
|---------|----------|
| `plugin_guard.enabled: false` | Handler returns `None` immediately (~0.1ms) |
| Config missing/corrupt | Plugin self-disables; all tools pass |
| Stage 1 LLM unavailable | `fail_open:true` → ALLOW; `fail_open:false` → ESCALATE |
| Stage 2 ACP crash/timeout | `fail_open:true` → ALLOW; `fail_open:false` → DENY |
| Hindsight backend down | Silent skip; tool execution unaffected |
| Module import failure | Caught by Hermes plugin loader; not registered |

## Configuration Reference

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `plugin_guard.enabled` | bool | `false` | Master switch |
| `plugin_guard.provider` | str | — | LLM provider for classification |
| `plugin_guard.model` | str | — | Model name (fast model, e.g. `qwen-3.6-27b-fast`) |
| `plugin_guard.fail_open` | bool | `true` | LLM failure → allow (safe default) |
| `plugin_guard.stage1.timeout` | int | `5` | Seconds for LLM classification |
| `plugin_guard.stage2.enabled` | bool | `false` | Enable ACP deep review |
| `plugin_guard.stage2.profile` | str | `"approval"` | Hermes profile for review agent |
| `plugin_guard.stage2.timeout` | int | `15` | Seconds for deep review |
| `plugin_guard.memory.backend` | str | `"hindsight"` | `hindsight`, `honcho`, or `none` |
| `plugin_guard.memory.bank` | str | `"approval"` | Hindsight bank or Honcho user_id |

Also set `approvals.mode: off` when plugin is enabled — system DANGEROUS
check is redundant; system HARDLINE remains active regardless.

## Files

| File | Purpose |
|------|---------|
| `plugin.yaml` | Manifest (standalone, hook: `pre_tool_call`) |
| `__init__.py` | `PluginContext.register_hook` entry point |
| `guard.py` | Dispatcher + SessionDB context query |
| `stage1_rules.py` | Risk signal extraction (no hard DENY) |
| `stage1_llm.py` | LLM classify: ALLOW/ESCALATE, system prompt style |
| `stage2_acp.py` | Stateless ACP: 5-section prompt, Hindsight integration |
| `feedback.py` | Structured denial messages with alternatives |
| `hindsight_store.py` | Approval memory: session/pattern queries |
| `recommended-config.yaml` | Annotated config template |

## Testing

```bash
cd plugins/hermes-approval-guard

# Integration tests (7 scenarios, 41 cases)
python3 test_integration.py
```

Covers: SAFE_TOOLS bypass, context extraction (no hard DENY), terminal
risk signals, feedback messages, pattern key generation, LLM prompt
structure.

## Compatibility

- Hermes ≥ 0.14.0
- Uses `pre_tool_call` plugin hook (`hermes_cli/plugins.py`)
- Imports `tools.approval` for `detect_dangerous/hardline_command`
- Imports `hermes_state.SessionDB` for conversation context
- Optional: Hindsight HTTP API for approval memory
