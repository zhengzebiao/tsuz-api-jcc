"""Events emitted by the in-process Agent runtime."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class AgentEvent:
    name: str
    data: dict[str, object]
    event_id: str | None = None

    def sse(self) -> str:
        identifier = f"id: {self.event_id}\n" if self.event_id else ""
        return f"{identifier}event: {self.name}\ndata: {json.dumps(self.data, ensure_ascii=False, separators=(',', ':'))}\n\n"


def event(name: str, *, message_id: str, run_id: str, **data: object) -> AgentEvent:
    return AgentEvent(name=name, data={"message_id": message_id, "run_id": run_id, **data})


def heartbeat() -> str:
    timestamp = datetime.now(UTC).isoformat()
    return f": heartbeat {timestamp}\n\n"
