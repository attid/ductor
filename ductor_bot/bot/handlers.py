"""Message and command handler functions for the Telegram bot."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from ductor_bot.bot.sender import SendRichOpts, send_rich
from ductor_bot.bot.topic import get_session_key, get_thread_id
from ductor_bot.bot.typing import TypingContext
from ductor_bot.text.response_format import new_session_text, stop_text

if TYPE_CHECKING:
    from aiogram import Bot
    from aiogram.types import Message

    from ductor_bot.orchestrator.core import Orchestrator

logger = logging.getLogger(__name__)


async def handle_abort(
    orchestrator: Orchestrator | None,
    bot: Bot,
    *,
    chat_id: int,
    message: Message,
) -> bool:
    """Kill active CLI processes and send feedback.

    Returns True if handled, False if orchestrator not ready.
    """
    if orchestrator is None:
        return False

    killed = await orchestrator.abort(chat_id)
    logger.info("Abort requested killed=%d", killed)
    text = stop_text(bool(killed), orchestrator.active_provider_name)
    await send_rich(
        bot,
        chat_id,
        text,
        SendRichOpts(reply_to_message_id=message.message_id, thread_id=get_thread_id(message)),
    )
    return True


async def handle_abort_all(
    orchestrator: Orchestrator | None,
    bot: Bot,
    *,
    chat_id: int,
    message: Message,
    abort_all_callback: Callable[[], Awaitable[int]] | None = None,
) -> bool:
    """Kill active CLI processes on THIS agent AND all other agents.

    Returns True if handled, False if orchestrator not ready.
    """
    if orchestrator is None:
        return False

    # Kill local processes first
    killed = await orchestrator.abort(chat_id)

    # Kill processes on all other agents via the supervisor callback
    if abort_all_callback is not None:
        killed += await abort_all_callback()

    logger.info("Abort ALL requested killed=%d", killed)
    if killed:
        text = f"Stopped {killed} process(es) across all agents."
    else:
        text = "No active processes found on any agent."
    await send_rich(
        bot,
        chat_id,
        text,
        SendRichOpts(reply_to_message_id=message.message_id, thread_id=get_thread_id(message)),
    )
    return True


async def handle_command(orchestrator: Orchestrator, bot: Bot, message: Message) -> None:
    """Route an orchestrator command (e.g. /status, /model)."""
    if not message.text:
        return
    key = get_session_key(message)
    chat_id = key.chat_id
    thread_id = get_thread_id(message)
    logger.info("Command dispatched cmd=%s", message.text.strip()[:40])
    async with TypingContext(bot, chat_id, thread_id=thread_id):
        result = await orchestrator.handle_message(key, message.text.strip())
    await send_rich(
        bot,
        chat_id,
        result.text,
        SendRichOpts(
            reply_to_message_id=message.message_id,
            reply_markup=result.reply_markup,
            thread_id=thread_id,
        ),
    )


async def handle_new_session(orchestrator: Orchestrator, bot: Bot, message: Message) -> None:
    """Handle /new: reset session."""
    logger.info("Session reset requested")
    key = get_session_key(message)
    chat_id = key.chat_id
    thread_id = get_thread_id(message)
    async with TypingContext(bot, chat_id, thread_id=thread_id):
        provider = await orchestrator.reset_active_provider_session(key)
    await send_rich(
        bot,
        chat_id,
        new_session_text(provider),
        SendRichOpts(reply_to_message_id=message.message_id, thread_id=thread_id),
    )


def strip_mention(text: str, bot_username: str | None) -> str:
    """Remove @botusername from message text (case-insensitive)."""
    if not bot_username:
        return text
    tag = f"@{bot_username}"
    lower = text.lower()
    if tag in lower:
        idx = lower.index(tag)
        stripped = (text[:idx] + text[idx + len(tag) :]).strip()
        return stripped or text
    return text
