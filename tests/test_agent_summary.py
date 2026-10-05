from app.agent.summary import compact_history


def test_compact_history_keeps_recent_messages_and_bounds_older_context() -> None:
    messages = [{"role": "user", "content": "old " * 1000}] + [
        {"role": "assistant", "content": "recent"},
        {"role": "user", "content": "question"},
    ]
    compacted = compact_history(messages, trigger_chars=100, keep_recent=2, max_summary_chars=100)
    assert compacted[0]["role"] == "system"
    assert len(compacted[0]["content"]) <= 130
    assert compacted[-2:] == messages[-2:]


def test_compact_history_does_not_change_short_history() -> None:
    messages = [{"role": "user", "content": "hello"}]
    assert compact_history(messages, trigger_chars=100, keep_recent=1) == messages
