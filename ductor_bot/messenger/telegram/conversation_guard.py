"""Per-chat hop counter to break runaway bot↔bot conversations.

When several Telegram bots share a group, our bot's reply (sent via
:class:`StreamEditor`) is now a real Telegram-reply, so other bots see it
as addressed to them and respond. To prevent infinite back-and-forth, we
track consecutive bot-authored messages per chat: each one increments a
counter; once it exceeds ``max_hops`` further bot-authored messages are
silently dropped until the counter resets.

The counter resets on:
- a user-authored message in the same chat key;
- inactivity longer than ``idle_reset_seconds``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

ChatKey = tuple[int, int | None]
"""``(chat_id, topic_id)`` pair identifying a conversation."""


@dataclass
class _Entry:
    count: int
    last_seen: float


class BotConversationGuard:
    """In-memory hop counter for bot-to-bot conversations."""

    def __init__(self, *, max_hops: int, idle_reset_seconds: float) -> None:
        if max_hops < 0:
            raise ValueError("max_hops must be >= 0")
        if idle_reset_seconds <= 0:
            raise ValueError("idle_reset_seconds must be > 0")
        self._max_hops = max_hops
        self._idle = idle_reset_seconds
        self._entries: dict[ChatKey, _Entry] = {}

    def observe_user(self, chat_key: ChatKey) -> None:
        """Reset the hop counter for *chat_key* — a human just posted."""
        self._entries.pop(chat_key, None)

    def should_drop_bot(self, chat_key: ChatKey) -> bool:
        """Increment the hop counter and return True if the limit is exceeded."""
        now = time.monotonic()
        entry = self._entries.get(chat_key)
        if entry is None or (now - entry.last_seen) > self._idle:
            entry = _Entry(count=0, last_seen=now)
            self._entries[chat_key] = entry
        entry.count += 1
        entry.last_seen = now
        return entry.count > self._max_hops
