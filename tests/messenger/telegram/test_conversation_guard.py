"""Unit tests for BotConversationGuard."""

from __future__ import annotations

import time

import pytest

from ductor_bot.messenger.telegram.conversation_guard import BotConversationGuard

CHAT: tuple[int, int | None] = (-100, None)
TOPIC: tuple[int, int | None] = (-100, 42)


def test_first_bot_msgs_pass_until_limit() -> None:
    g = BotConversationGuard(max_hops=2, idle_reset_seconds=60.0)
    assert g.should_drop_bot(CHAT) is False  # hop=1
    assert g.should_drop_bot(CHAT) is False  # hop=2
    assert g.should_drop_bot(CHAT) is True  # hop=3 > 2 → drop


def test_user_message_resets_counter() -> None:
    g = BotConversationGuard(max_hops=1, idle_reset_seconds=60.0)
    assert g.should_drop_bot(CHAT) is False  # hop=1
    assert g.should_drop_bot(CHAT) is True  # hop=2 > 1
    g.observe_user(CHAT)
    assert g.should_drop_bot(CHAT) is False  # reset, hop=1


def test_idle_reset_after_timeout() -> None:
    g = BotConversationGuard(max_hops=1, idle_reset_seconds=0.05)
    assert g.should_drop_bot(CHAT) is False
    assert g.should_drop_bot(CHAT) is True
    time.sleep(0.06)
    # Idle expired → counter cleared on next call
    assert g.should_drop_bot(CHAT) is False


def test_max_hops_zero_drops_first_bot_msg() -> None:
    g = BotConversationGuard(max_hops=0, idle_reset_seconds=60.0)
    assert g.should_drop_bot(CHAT) is True


def test_per_chat_keys_are_independent() -> None:
    g = BotConversationGuard(max_hops=1, idle_reset_seconds=60.0)
    assert g.should_drop_bot(CHAT) is False  # CHAT hop=1
    assert g.should_drop_bot(TOPIC) is False  # TOPIC hop=1 — separate
    assert g.should_drop_bot(CHAT) is True  # CHAT hop=2 → drop
    assert g.should_drop_bot(TOPIC) is True  # TOPIC hop=2 → drop


def test_invalid_args_raise() -> None:
    with pytest.raises(ValueError, match="max_hops must be >= 0"):
        BotConversationGuard(max_hops=-1, idle_reset_seconds=10.0)
    with pytest.raises(ValueError, match="idle_reset_seconds must be > 0"):
        BotConversationGuard(max_hops=1, idle_reset_seconds=0.0)
    with pytest.raises(ValueError, match="idle_reset_seconds must be > 0"):
        BotConversationGuard(max_hops=1, idle_reset_seconds=-5.0)
