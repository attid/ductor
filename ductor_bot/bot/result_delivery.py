"""Delivery handlers for background results: sessions, cron, heartbeat, webhooks, inter-agent."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ductor_bot.background import BackgroundResult
from ductor_bot.bot.buttons import extract_buttons_for_session
from ductor_bot.bot.sender import SendRichOpts, send_rich
from ductor_bot.bot.typing import TypingContext
from ductor_bot.log_context import set_log_context
from ductor_bot.multiagent.bus import AsyncInterAgentResult
from ductor_bot.session.key import SessionKey
from ductor_bot.tasks.models import TaskResult
from ductor_bot.text.response_format import SEP, fmt

if TYPE_CHECKING:
    from ductor_bot.bot.app import TelegramBot

logger = logging.getLogger(__name__)

_CRON_ACK_MARKERS = ("message sent successfully", "delivered to telegram")


def _is_cron_transport_ack_line(line: str) -> bool:
    """Return True for known transport confirmation lines from task tools."""
    normalized = " ".join(line.lower().split())
    return all(marker in normalized for marker in _CRON_ACK_MARKERS)


def _sanitize_cron_result_text(result: str) -> str:
    """Strip tool transport confirmations from cron result text."""
    if not result:
        return ""
    lines = [line for line in result.splitlines() if not _is_cron_transport_ack_line(line)]
    return "\n".join(lines).strip()


async def deliver_session_result(
    bot: TelegramBot,
    result: BackgroundResult,
) -> None:
    """Handle background task result as a NEW message (triggers notification)."""
    elapsed = f"{result.elapsed_seconds:.0f}s"

    if result.session_name:
        # Update named session registry
        bot._orch.named_sessions.update_after_response(
            result.chat_id, result.session_name, result.session_id
        )
        await _deliver_named_session_result(bot, result, elapsed)
    else:
        await _deliver_stateless_result(bot, result, elapsed)


async def _deliver_named_session_result(
    bot: TelegramBot,
    result: BackgroundResult,
    elapsed: str,
) -> None:
    """Deliver a named-session background result with session tag."""
    name = result.session_name
    if result.status == "aborted":
        text = fmt(f"**[{name}] Cancelled**", SEP, f"_{result.prompt_preview}_")
    elif result.status.startswith("error:"):
        text = fmt(
            f"**[{name}] Failed** ({elapsed})",
            SEP,
            result.result_text[:2000] if result.result_text else "_No output._",
        )
    else:
        text = fmt(
            f"**[{name}] Complete** ({elapsed})",
            SEP,
            result.result_text or "_No output._",
        )

    cleaned, markup = extract_buttons_for_session(text, name)
    roots = bot.file_roots(bot._orch.paths)
    await send_rich(
        bot.bot_instance,
        result.chat_id,
        cleaned,
        SendRichOpts(
            reply_to_message_id=result.message_id,
            reply_markup=markup,
            allowed_roots=roots,
            thread_id=result.thread_id,
        ),
    )


async def _deliver_stateless_result(
    bot: TelegramBot,
    result: BackgroundResult,
    elapsed: str,
) -> None:
    """Deliver a legacy stateless background result."""
    if result.status == "aborted":
        text = fmt(
            "**Background Task Cancelled**",
            SEP,
            f"Task `{result.task_id}` was cancelled.\nPrompt: _{result.prompt_preview}_",
        )
    elif result.status.startswith("error:"):
        text = fmt(
            f"**Background Task Failed** ({elapsed})",
            SEP,
            f"Task `{result.task_id}` failed ({result.status}).\n"
            f"Prompt: _{result.prompt_preview}_\n\n"
            + (result.result_text[:2000] if result.result_text else "_No output._"),
        )
    else:
        text = fmt(
            f"**Background Task Complete** ({elapsed})",
            SEP,
            result.result_text or "_No output._",
        )

    roots = bot.file_roots(bot._orch.paths)
    await send_rich(
        bot.bot_instance,
        result.chat_id,
        text,
        SendRichOpts(
            reply_to_message_id=result.message_id,
            allowed_roots=roots,
            thread_id=result.thread_id,
        ),
    )


async def deliver_cron_result(bot: TelegramBot, title: str, result: str, status: str) -> None:
    """Send cron job result to all allowed users."""
    clean_result = _sanitize_cron_result_text(result)
    if result and not clean_result and status == "success":
        logger.debug(
            "Cron result only had transport confirmations; skipping broadcast task=%s", title
        )
        return
    text = (
        f"**TASK: {title}**\n\n{clean_result}"
        if clean_result
        else f"**TASK: {title}**\n\n_{status}_"
    )
    await bot.broadcast(text, SendRichOpts(allowed_roots=bot.file_roots(bot._orch.paths)))


async def deliver_heartbeat_result(
    bot: TelegramBot,
    chat_id: int,
    text: str,
) -> None:
    """Send heartbeat alert to the user."""
    logger.debug("Heartbeat delivery chars=%d", len(text))
    await send_rich(
        bot.bot_instance,
        chat_id,
        text,
        SendRichOpts(allowed_roots=bot.file_roots(bot._orch.paths)),
    )
    logger.info("Heartbeat delivered")


async def deliver_webhook_result(bot: TelegramBot, result: object) -> None:
    """Send webhook cron_task result to all allowed users.

    Wake mode results are already sent to Telegram by ``handle_webhook_wake``.
    """
    from ductor_bot.webhook.models import WebhookResult

    if not isinstance(result, WebhookResult):
        return
    if result.mode == "wake":
        return
    if result.result_text:
        text = f"**WEBHOOK (CRON TASK): {result.hook_title}**\n\n{result.result_text}"
    else:
        text = f"**WEBHOOK (CRON TASK): {result.hook_title}**\n\n_{result.status}_"
    await bot.broadcast(text, SendRichOpts(allowed_roots=bot.file_roots(bot._orch.paths)))


async def handle_webhook_wake(bot: TelegramBot, chat_id: int, prompt: str) -> str | None:
    """Process webhook wake prompt through the normal message pipeline.

    Acquires the per-chat lock (queues behind active conversations),
    processes the prompt through the standard orchestrator path, and
    sends the response to Telegram like a normal message.
    """
    set_log_context(operation="wh", chat_id=chat_id)
    key = SessionKey(chat_id=chat_id)
    lock = bot.sequential.get_lock(key.lock_key)
    async with lock:
        result = await bot._orch.handle_message(key, prompt)
    roots = bot.file_roots(bot._orch.paths)
    await send_rich(bot.bot_instance, chat_id, result.text, SendRichOpts(allowed_roots=roots))
    return result.text


async def deliver_interagent_result(
    bot: TelegramBot,
    result: AsyncInterAgentResult,
) -> None:
    """Handle async inter-agent result: inject into active main session.

    On error: sends the error notification to the primary user.
    On success: acquires the chat lock, resumes the current active session
    with a self-contained prompt (original task + result), and sends the
    orchestrator's response to Telegram.
    """
    chat_id = bot.config.allowed_user_ids[0] if bot.config.allowed_user_ids else 0
    if not chat_id:
        logger.warning("No chat_id available for async interagent result delivery")
        return

    set_log_context(operation="ia-async", chat_id=chat_id)
    roots = bot.file_roots(bot._orch.paths)

    logger.debug(
        "Async inter-agent result received: task=%s from=%s success=%s len=%d",
        result.task_id,
        result.recipient,
        result.success,
        len(result.result_text),
    )

    session_info = f"\nSession: `{result.session_name}`" if result.session_name else ""

    if not result.success:
        error_text = (
            f"**Inter-Agent Request Failed**\n\n"
            f"Agent: `{result.recipient}`{session_info}\n"
            f"Error: {result.error}\n"
            f"Request: _{result.message_preview}_"
        )
        await send_rich(bot.bot_instance, chat_id, error_text, SendRichOpts(allowed_roots=roots))
        return

    # Notify user about provider switch before processing the result
    if result.provider_switch_notice:
        await send_rich(
            bot.bot_instance,
            chat_id,
            f"**Provider Switch Detected**\n\n{result.provider_switch_notice}",
            SendRichOpts(allowed_roots=roots),
        )

    # Acquire the chat lock so we safely resume the current active session
    # (no concurrent CLI access). Queues behind any active user conversation.
    lock = bot.sequential.get_lock(chat_id)
    logger.debug("Async inter-agent result: waiting for chat lock (chat_id=%d)", chat_id)
    async with lock, TypingContext(bot.bot_instance, chat_id):
        logger.debug("Async inter-agent result: lock acquired, injecting into main session")
        response_text = await bot._orch.handle_async_interagent_result(
            result,
            chat_id=chat_id,
        )

    if response_text:
        await send_rich(bot.bot_instance, chat_id, response_text, SendRichOpts(allowed_roots=roots))


async def deliver_task_result(bot: TelegramBot, result: TaskResult) -> None:
    """Handle background task result: notify user and inject into active session.

    On success/failure: sends a Telegram notification, acquires the chat
    lock, resumes the current active session with the result, and sends
    the orchestrator's response.
    """
    chat_id = result.chat_id
    if not chat_id:
        chat_id = bot.config.allowed_user_ids[0] if bot.config.allowed_user_ids else 0
    if not chat_id:
        logger.warning("No chat_id for task result delivery (task=%s)", result.task_id)
        return

    set_log_context(operation="task", chat_id=chat_id)
    roots = bot.file_roots(bot._orch.paths)

    logger.debug(
        "Task result: id=%s name='%s' status=%s",
        result.task_id,
        result.name,
        result.status,
    )

    # 1. Send Telegram notification (skip "waiting" — question already shown)
    if result.status == "done":
        duration = f"{result.elapsed_seconds:.0f}s"
        target = f"{result.provider}/{result.model}" if result.provider else ""
        detail = f"{duration}, {target}" if target else duration
        note = f"**Task `{result.name}` completed** ({detail})"
    elif result.status == "cancelled":
        note = f"**Task `{result.name}` cancelled**"
    elif result.status == "waiting":
        note = ""  # Question already delivered via handle_task_question
    else:
        note = f"**Task `{result.name}` failed**\nReason: {result.error}"
    if note:
        await send_rich(bot.bot_instance, chat_id, note, SendRichOpts(allowed_roots=roots))

    # 2. Inject into parent agent's session (for done/failed — not cancelled/waiting)
    if result.status in ("done", "failed"):
        lock = bot.sequential.get_lock(chat_id)
        async with lock, TypingContext(bot.bot_instance, chat_id):
            response_text = await bot._orch.handle_task_result(result, chat_id=chat_id)
        if response_text:
            await send_rich(
                bot.bot_instance, chat_id, response_text, SendRichOpts(allowed_roots=roots)
            )


async def handle_task_question(
    bot: TelegramBot,
    task_id: str,
    question: str,
    prompt_preview: str,
    chat_id: int,
) -> None:
    """Deliver a background task question to the main agent's Telegram chat.

    Sends a notification, then injects the question into the main agent's
    session. The agent decides: answer directly (via resume_task.py) or
    ask the user first, then resume.
    """
    if not chat_id:
        chat_id = bot.config.allowed_user_ids[0] if bot.config.allowed_user_ids else 0
    if not chat_id:
        logger.warning("No chat_id for task question delivery (task=%s)", task_id)
        return

    set_log_context(operation="task", chat_id=chat_id)
    roots = bot.file_roots(bot._orch.paths)

    logger.debug("Task question: id=%s question='%s'", task_id, question[:60])

    # 1. Notify user about the question
    note = f"**Task `{task_id}` has a question:**\n{question}"
    await send_rich(bot.bot_instance, chat_id, note, SendRichOpts(allowed_roots=roots))

    # 2. Inject into main agent's session so it can handle it
    lock = bot.sequential.get_lock(chat_id)
    async with lock, TypingContext(bot.bot_instance, chat_id):
        response = await bot._orch.handle_task_question(task_id, question, prompt_preview, chat_id)

    # 3. Send agent's decision to Telegram
    if response:
        await send_rich(bot.bot_instance, chat_id, response, SendRichOpts(allowed_roots=roots))
