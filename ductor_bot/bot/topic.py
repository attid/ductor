"""Forum topic support utilities."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ductor_bot.session.key import SessionKey

if TYPE_CHECKING:
    from aiogram.types import Message


def get_thread_id(message: Message | None) -> int | None:
    """Extract ``message_thread_id`` from a forum topic message.

    Returns the thread ID only when the message originates from a forum
    topic (``is_topic_message is True``).  Mirrors aiogram's internal
    logic in ``Message.answer()``.
    """
    if message is None:
        return None
    if message.is_topic_message:
        return message.message_thread_id
    return None


def get_session_key(message: Message) -> SessionKey:
    """Build a transport-agnostic ``SessionKey`` from a Telegram message.

    Forum topic messages get per-topic keys (``topic_id=message_thread_id``).
    Regular chats and non-topic supergroup messages get flat keys
    (``topic_id=None``).
    """
    topic_id = message.message_thread_id if message.is_topic_message else None
    return SessionKey(chat_id=message.chat.id, topic_id=topic_id)
