"""Hermes Approval Guard — two-stage tool call approval plugin.

Stage 1: Rule engine (<1ms) + LLM fast classification (~500ms)
Stage 2: ACP Agent deep review (3-8s)

Covers all tools, provides structured denial feedback, Hindsight memory learning.
Disabled by default; requires plugin_guard.enabled: true."""

from __future__ import annotations

import threading
import logging

from .guard import pre_tool_call_handler

logger = logging.getLogger(__name__)

# Thread-local storage for session_id injection.
# When AIAgent._invoke_tool calls the pre_tool_call hook it does not pass
# session_id (upstream bug).  We monkey-patch _invoke_tool to stash
# agent.session_id here so guard.py can retrieve it when the hook parameter
# is empty.  Remove this once the upstream fix is merged.
_session_id_local = threading.local()


def _get_session_id_from_agent() -> str:
    """Read session_id that was stashed by the monkey-patched _invoke_tool."""
    return getattr(_session_id_local, "value", None) or ""


def _patch_invoke_tool():
    """Monkey-patch AIAgent._invoke_tool to stash session_id before the
    pre_tool_call hook fires, so plugins can discover it via this module."""
    try:
        from run_agent import AIAgent

        _original = AIAgent._invoke_tool

        def _patched_invoke_tool(
            self, function_name, function_args, effective_task_id,
            tool_call_id=None, messages=None, pre_tool_block_checked=False,
        ):
            sid = getattr(self, "session_id", None) or ""
            _session_id_local.value = sid
            try:
                return _original(
                    self, function_name, function_args, effective_task_id,
                    tool_call_id=tool_call_id, messages=messages,
                    pre_tool_block_checked=pre_tool_block_checked,
                )
            finally:
                _session_id_local.value = None

        AIAgent._invoke_tool = _patched_invoke_tool
        logger.info("approval-guard: patched AIAgent._invoke_tool for session_id injection")
    except Exception as exc:
        logger.warning("approval-guard: unable to patch _invoke_tool: %s", exc)


def register(ctx) -> None:
    """Register pre_tool_call hook and apply session-id workaround."""
    _patch_invoke_tool()
    ctx.register_hook("pre_tool_call", pre_tool_call_handler)
