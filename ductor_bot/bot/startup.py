"""Bot startup lifecycle: orchestrator creation, recovery, sentinel handling."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING

from ductor_bot.bot.sender import SendRichOpts, send_rich
from ductor_bot.infra.restart import consume_restart_sentinel
from ductor_bot.infra.updater import UpdateObserver, consume_upgrade_sentinel
from ductor_bot.infra.version import get_current_version

if TYPE_CHECKING:
    from ductor_bot.bot.app import TelegramBot

logger = logging.getLogger(__name__)


async def _handle_restart_sentinel(bot: TelegramBot) -> dict[str, object] | None:
    """Consume and handle the restart sentinel file. Returns sentinel dict or None."""
    sentinel_path = bot._orch.paths.ductor_home / "restart-sentinel.json"
    sentinel = await asyncio.to_thread(consume_restart_sentinel, sentinel_path=sentinel_path)
    if sentinel:
        chat_id = int(sentinel.get("chat_id", 0))
        msg = str(sentinel.get("message", "Restart completed."))
        if chat_id:
            await send_rich(
                bot.bot_instance,
                chat_id,
                msg,
                SendRichOpts(allowed_roots=bot.file_roots(bot._orch.paths)),
            )
    return sentinel


async def run_startup(bot: TelegramBot) -> None:
    """Execute full startup sequence: orchestrator, sentinels, recovery, update observer."""
    from ductor_bot.orchestrator.core import Orchestrator

    bot._orchestrator = await Orchestrator.create(
        bot.config,
        agent_name=bot._agent_name,
    )

    from ductor_bot.bot.chat_tracker import ChatTracker

    bot._chat_tracker = ChatTracker(bot._orch.paths.chat_activity_path)

    me = await bot.bot_instance.get_me()
    bot._bot_id = me.id
    bot._bot_username = (me.username or "").lower()
    logger.info("Bot online: @%s (id=%d)", me.username, me.id)

    sentinel = await _handle_restart_sentinel(bot)

    bot._orchestrator.set_cron_result_handler(bot._on_cron_result)
    bot._orchestrator.set_heartbeat_handler(bot._on_heartbeat_result)
    bot._orchestrator.set_webhook_result_handler(bot._on_webhook_result)
    bot._orchestrator.set_webhook_wake_handler(bot._handle_webhook_wake)
    bot._orchestrator.set_session_result_handler(bot._on_session_result)
    bot._orchestrator.set_config_hot_reload_handler(bot._on_auth_hot_reload)
    bot._bus.set_injector(bot._orch)

    # Check for post-upgrade notification
    upgrade = await asyncio.to_thread(consume_upgrade_sentinel, bot._orch.paths.ductor_home)
    if upgrade:
        uid = int(upgrade.get("chat_id", 0))
        old_v = upgrade.get("old_version", "?")
        new_v = upgrade.get("new_version", get_current_version())
        if uid:
            await send_rich(
                bot.bot_instance,
                uid,
                f"**Upgrade complete** `{old_v}` -> `{new_v}`",
                SendRichOpts(
                    allowed_roots=bot.file_roots(bot._orch.paths),
                ),
            )

    # -- Startup lifecycle detection --
    from ductor_bot.infra.startup_state import detect_startup_kind, save_startup_state
    from ductor_bot.text.response_format import startup_notification_text

    startup_info = await asyncio.to_thread(detect_startup_kind, bot._orch.paths.startup_state_path)
    await asyncio.to_thread(save_startup_state, bot._orch.paths.startup_state_path, startup_info)
    if sentinel is None and startup_info.kind.value != "service_restart":
        note = startup_notification_text(startup_info.kind.value)
        if note:
            await bot.broadcast(note, SendRichOpts(allowed_roots=bot.file_roots(bot._orch.paths)))

    # -- Auto-recovery of interrupted work --
    from ductor_bot.infra.recovery import RecoveryPlanner
    from ductor_bot.text.response_format import recovery_notification_text

    planner = RecoveryPlanner(
        inflight=bot._orch.inflight_tracker,
        named_sessions=bot._orch.named_sessions.pop_recovered_running(),
        max_age_seconds=bot.config.timeouts.normal * 2,
    )
    for action in planner.plan():
        note = recovery_notification_text(action.kind, action.prompt_preview, action.session_name)
        await send_rich(
            bot.bot_instance,
            action.chat_id,
            note,
            SendRichOpts(allowed_roots=bot.file_roots(bot._orch.paths)),
        )
        if action.kind == "named_session" and action.session_name:
            with contextlib.suppress(Exception):
                bot._orch.submit_named_followup_bg(
                    action.chat_id,
                    action.session_name,
                    action.prompt_preview,
                    message_id=0,
                    thread_id=None,
                )
    bot._orch.inflight_tracker.clear()

    # Start background version checker (skip for dev/source installs)
    from ductor_bot.infra.install import is_upgradeable

    if is_upgradeable():
        bot._update_observer = UpdateObserver(notify=bot._on_update_available)
        bot._update_observer.start()

    await bot._sync_commands()
    bot._restart_watcher = asyncio.create_task(bot._watch_restart_marker())
