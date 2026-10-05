"""Bounded, deterministic context compaction for Agent prompts."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TypedDict


class SummaryMessage(TypedDict):
    role: str
    content: str


def compact_history(
    messages: Sequence[SummaryMessage],
    *,
    trigger_chars: int,
    keep_recent: int,
    max_summary_chars: int = 4000,
) -> list[SummaryMessage]:
    """Replace older complete turns with a bounded context note.

    This is deliberately local and deterministic: it does not call an LLM and
    therefore cannot block or invent facts. Original messages remain persisted.
    """
    total_chars = sum(len(item.get("content", "")) for item in messages)
    if total_chars <= trigger_chars or len(messages) <= keep_recent:
        return list(messages)

    split = max(0, len(messages) - keep_recent)
    older = messages[:split]
    recent = list(messages[split:])
    facts: list[str] = []
    remaining = max_summary_chars
    for item in older:
        content = " ".join(item.get("content", "").split())
        if not content:
            continue
        fragment = f"{item.get('role', 'message')}: {content}"
        if len(fragment) > remaining:
            fragment = fragment[:remaining].rstrip() + "…"
        facts.append(fragment)
        remaining -= len(fragment) + 1
        if remaining <= 0:
            break
    if not facts:
        return recent
    prefix = "Earlier conversation context:\n"
    return [{"role": "system", "content": (prefix + "\n".join(facts))[: max_summary_chars + len(prefix)]}, *recent]
