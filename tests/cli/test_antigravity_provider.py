"""Tests for the Antigravity CLI provider."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ductor_bot.cli.base import CLIConfig
from ductor_bot.cli.types import CLIResponse

if TYPE_CHECKING:
    import pytest


def test_antigravity_build_command_basic(monkeypatch: pytest.MonkeyPatch) -> None:
    from ductor_bot.cli.antigravity_provider import AntigravityCLI

    monkeypatch.setattr("ductor_bot.cli.antigravity_provider.which", lambda _: "/usr/bin/agy")
    cfg = CLIConfig(provider="antigravity", permission_mode="default")
    cli = AntigravityCLI(cfg)

    cmd, log_path = cli._build_command("hello")

    assert cmd[:2] == ["/usr/bin/agy", "--print-timeout"]
    assert "5m0s" in cmd
    assert "--log-file" in cmd
    assert str(log_path).endswith(".log")
    assert "--print" in cmd
    assert cmd[-1] == "hello"
    assert "--conversation" not in cmd
    assert "-m" not in cmd


def test_antigravity_build_command_resume(monkeypatch: pytest.MonkeyPatch) -> None:
    from ductor_bot.cli.antigravity_provider import AntigravityCLI

    monkeypatch.setattr("ductor_bot.cli.antigravity_provider.which", lambda _: "/usr/bin/agy")
    cli = AntigravityCLI(CLIConfig(provider="antigravity"))

    cmd, _log_path = cli._build_command("hello", resume_session="conv-123")

    assert "--conversation" in cmd
    assert cmd[cmd.index("--conversation") + 1] == "conv-123"


def test_antigravity_build_command_bypass_permissions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ductor_bot.cli.antigravity_provider import AntigravityCLI

    monkeypatch.setattr("ductor_bot.cli.antigravity_provider.which", lambda _: "/usr/bin/agy")
    cli = AntigravityCLI(CLIConfig(provider="antigravity", permission_mode="bypassPermissions"))

    cmd, _log_path = cli._build_command("hello")

    assert "--dangerously-skip-permissions" in cmd


def test_antigravity_compose_prompt_injects_system_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ductor_bot.cli.antigravity_provider import AntigravityCLI

    monkeypatch.setattr("ductor_bot.cli.antigravity_provider.which", lambda _: "/usr/bin/agy")
    cli = AntigravityCLI(
        CLIConfig(
            provider="antigravity",
            system_prompt="System",
            append_system_prompt="Append",
        )
    )

    composed = cli._compose_prompt("User")

    assert composed == "System\n\nUser\n\nAppend"


def test_antigravity_parse_response_extracts_conversation_id(tmp_path: Path) -> None:
    from ductor_bot.cli.antigravity_provider import parse_antigravity_response

    log_path = tmp_path / "agy.log"
    log_path.write_text(
        "I0524 printmode.go:130] Print mode: "
        "conversation=368bf8e8-738e-4375-b104-03ef64b936b3, sending message\n",
        encoding="utf-8",
    )

    response = parse_antigravity_response(b"Done\n", b"", 0, log_path)

    assert response == CLIResponse(
        result="Done",
        session_id="368bf8e8-738e-4375-b104-03ef64b936b3",
        returncode=0,
    )


def test_antigravity_parse_response_marks_nonzero_error(tmp_path: Path) -> None:
    from ductor_bot.cli.antigravity_provider import parse_antigravity_response

    response = parse_antigravity_response(b"", b"bad auth", 1, tmp_path / "missing.log")

    assert response.is_error is True
    assert response.result == "bad auth"
    assert response.returncode == 1
