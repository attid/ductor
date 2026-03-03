"""Transport-agnostic composite session key."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SessionKey:
    """Composite session identifier: chat + optional topic/channel.

    For Telegram forum topics, ``topic_id`` is ``message_thread_id``.
    For the WebSocket API, ``topic_id`` maps to ``channel_id``.
    When ``topic_id`` is ``None``, this is a flat (legacy) session key.
    """

    chat_id: int
    topic_id: int | None = None

    @property
    def storage_key(self) -> str:
        """JSON-serializable key for ``sessions.json`` persistence."""
        if self.topic_id is None:
            return str(self.chat_id)
        return f"{self.chat_id}:{self.topic_id}"

    @property
    def lock_key(self) -> tuple[int, int | None]:
        """Hashable key for per-session lock dictionaries."""
        return (self.chat_id, self.topic_id)

    @classmethod
    def parse(cls, raw: str) -> SessionKey:
        """Parse a storage key back to ``SessionKey``.

        Handles both legacy ``"12345"`` and composite ``"12345:99"`` formats.
        """
        if ":" in raw:
            chat_str, topic_str = raw.split(":", 1)
            return cls(chat_id=int(chat_str), topic_id=int(topic_str))
        return cls(chat_id=int(raw))
