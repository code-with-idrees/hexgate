"""Prompt shaping that reduces token spend without changing intent.

Each rule is individually reported so the dashboard can show exactly where the
savings came from rather than a single opaque number.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from hexgate.config import OptimizerConfig
from hexgate.protocol.openai import estimate_tokens, text_of

TRUNCATION_MARKER = "\n\n[hexgate: truncated {removed} characters of tool output]"


@dataclass(slots=True)
class OptimizerReport:
    chars_before: int = 0
    chars_after: int = 0
    actions: list[dict[str, Any]] = field(default_factory=list)

    @property
    def chars_saved(self) -> int:
        return max(self.chars_before - self.chars_after, 0)

    @property
    def tokens_saved(self) -> int:
        return estimate_tokens("x" * self.chars_saved)

    def note(self, rule: str, saved: int) -> None:
        if saved > 0:
            self.actions.append({"rule": rule, "chars": saved})

    def as_dict(self) -> dict[str, Any]:
        return {
            "chars_before": self.chars_before,
            "chars_after": self.chars_after,
            "chars_saved": self.chars_saved,
            "tokens_saved": self.tokens_saved,
            "actions": self.actions,
        }


def _size(messages: list[dict[str, Any]]) -> int:
    total = 0
    for message in messages:
        total += len(text_of(message.get("content")))
        for call in message.get("tool_calls") or []:
            total += len(str((call.get("function") or {}).get("arguments") or ""))
    return total


def optimize(
    body: dict[str, Any], config: OptimizerConfig
) -> tuple[dict[str, Any], OptimizerReport]:
    """Return a shaped copy of an OpenAI-shaped request plus a report."""
    report = OptimizerReport()
    if not config.enabled:
        report.chars_before = report.chars_after = _size(body.get("messages") or [])
        return body, report

    out = dict(body)
    messages = deepcopy(body.get("messages") or [])
    report.chars_before = _size(messages)

    if config.trim_trailing_whitespace:
        for message in messages:
            content = message.get("content")
            if isinstance(content, str):
                message["content"] = "\n".join(line.rstrip() for line in content.split("\n"))

    if config.strip_empty_messages:
        kept: list[dict[str, Any]] = []
        for message in messages:
            content = message.get("content")
            empty = content is None or (isinstance(content, str) and not content.strip())
            if empty and not message.get("tool_calls") and message.get("role") != "assistant":
                continue
            kept.append(message)
        messages = kept

    if config.dedupe_system:
        messages = _merge_system(messages, report)

    if config.max_tool_result_chars > 0:
        messages = _truncate_tools(messages, config.max_tool_result_chars, report)

    out["messages"] = messages
    report.chars_after = _size(messages)
    report.note("whitespace", 0)
    return out, report


def _merge_system(messages: list[dict[str, Any]], report: OptimizerReport) -> list[dict[str, Any]]:
    system_blocks = [
        text_of(m.get("content")) for m in messages if m.get("role") in {"system", "developer"}
    ]
    if len(system_blocks) < 2:
        return messages

    seen: list[str] = []
    for block in system_blocks:
        for chunk in block.split("\n\n"):
            stripped = chunk.strip()
            if stripped and stripped not in seen:
                seen.append(stripped)
    merged = "\n\n".join(seen)
    before = sum(len(block) for block in system_blocks)
    report.note("dedupe-system", max(before - len(merged), 0))

    rest = [
        m
        for m in messages
        if m.get("role") not in {"system", "developer"}
        and not (
            m.get("role") == "assistant"
            and not text_of(m.get("content"))
            and not m.get("tool_calls")
        )
    ]
    return [{"role": "system", "content": merged}, *rest]


def _truncate_tools(
    messages: list[dict[str, Any]], limit: int, report: OptimizerReport
) -> list[dict[str, Any]]:
    saved = 0
    for message in messages:
        if message.get("role") != "tool":
            continue
        content = message.get("content")
        if not isinstance(content, str) or len(content) <= limit:
            continue
        head = int(limit * 0.7)
        tail = limit - head
        removed = len(content) - limit
        message["content"] = (
            content[:head]
            + TRUNCATION_MARKER.format(removed=removed)
            + (content[-tail:] if tail > 0 else "")
        )
        saved += removed
    report.note("truncate-tool-output", saved)
    return messages
