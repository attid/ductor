"""Async wrapper around the Antigravity CLI."""

from __future__ import annotations

import logging
import os
import re
import tempfile
from collections.abc import AsyncGenerator
from pathlib import Path
from shutil import which
from typing import TYPE_CHECKING

from ductor_bot.cli.base import BaseCLI, CLIConfig, docker_wrap
from ductor_bot.cli.executor import SubprocessSpec, run_oneshot_subprocess
from ductor_bot.cli.stream_events import ResultEvent, StreamEvent
from ductor_bot.cli.types import CLIResponse

if TYPE_CHECKING:
    from ductor_bot.cli.timeout_controller import TimeoutController

logger = logging.getLogger(__name__)

_CONVERSATION_RE = re.compile(r"\bconversation=([0-9a-fA-F-]{8,})\b|Created conversation ([0-9a-fA-F-]{8,})")


class AntigravityCLI(BaseCLI):
    """Async wrapper around the Antigravity CLI print mode."""

    def __init__(self, config: CLIConfig) -> None:
        self._config = config
        self._working_dir = Path(config.working_dir).resolve()
        self._cli = "agy" if config.docker_container else self._find_cli()
        logger.info("Antigravity CLI wrapper: cwd=%s", self._working_dir)

    @staticmethod
    def _find_cli() -> str:
        path = which("agy")
        if not path:
            msg = "Antigravity CLI not found on PATH. Install it from https://antigravity.google/"
            raise FileNotFoundError(msg)
        return path

    def _compose_prompt(self, prompt: str) -> str:
        """Inject system context into the user prompt."""
        parts: list[str] = []
        if self._config.system_prompt:
            parts.append(self._config.system_prompt)
        parts.append(prompt)
        if self._config.append_system_prompt:
            parts.append(self._config.append_system_prompt)
        return "\n\n".join(parts)

    def _build_command(
        self,
        prompt: str,
        resume_session: str | None = None,
        *,
        timeout_seconds: float | None = None,
    ) -> tuple[list[str], Path]:
        fd, raw_log_path = tempfile.mkstemp(prefix="ductor-agy-", suffix=".log")
        os.close(fd)
        log_path = Path(raw_log_path)
        cmd = [
            self._cli,
            "--print-timeout",
            _format_duration(timeout_seconds),
            "--log-file",
            str(log_path),
        ]
        if self._config.permission_mode == "bypassPermissions":
            cmd.append("--dangerously-skip-permissions")
        if resume_session:
            cmd += ["--conversation", resume_session]
        if self._config.cli_parameters:
            cmd.extend(self._config.cli_parameters)
        cmd += ["--print", prompt]
        return cmd, log_path

    async def send(
        self,
        prompt: str,
        resume_session: str | None = None,
        continue_session: bool = False,
        timeout_seconds: float | None = None,
        timeout_controller: TimeoutController | None = None,
    ) -> CLIResponse:
        """Send a prompt via ``agy --print`` and return the final result."""
        if continue_session and not resume_session:
            logger.debug("continue_session is ignored; ductor resumes via explicit conversation IDs")
        final_prompt = self._compose_prompt(prompt)
        cmd, log_path = self._build_command(
            final_prompt,
            resume_session,
            timeout_seconds=timeout_seconds,
        )
        exec_cmd, use_cwd = docker_wrap(cmd, self._config)
        _log_cmd(exec_cmd)
        try:
            return await run_oneshot_subprocess(
                config=self._config,
                spec=SubprocessSpec(
                    exec_cmd,
                    use_cwd,
                    final_prompt,
                    timeout_seconds,
                    timeout_controller,
                ),
                parse_output=lambda stdout, stderr, returncode: parse_antigravity_response(
                    stdout, stderr, returncode, log_path
                ),
                provider_label="Antigravity",
            )
        finally:
            try:
                log_path.unlink(missing_ok=True)
            except OSError:
                logger.debug("Failed to remove Antigravity log file: %s", log_path)

    async def send_streaming(
        self,
        prompt: str,
        resume_session: str | None = None,
        continue_session: bool = False,
        timeout_seconds: float | None = None,
        timeout_controller: TimeoutController | None = None,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Expose print mode through the streaming interface as one final event."""
        response = await self.send(
            prompt,
            resume_session=resume_session,
            continue_session=continue_session,
            timeout_seconds=timeout_seconds,
            timeout_controller=timeout_controller,
        )
        yield ResultEvent(
            type="result",
            session_id=response.session_id,
            result=response.result,
            is_error=response.is_error,
            returncode=response.returncode,
        )


def parse_antigravity_response(
    stdout: bytes,
    stderr: bytes,
    returncode: int | None,
    log_path: Path,
) -> CLIResponse:
    """Parse Antigravity print-mode stdout plus log-derived conversation ID."""
    stderr_text = stderr.decode(errors="replace")[:2000] if stderr else ""
    stdout_text = stdout.decode(errors="replace").strip()
    is_error = bool(returncode)
    result = stdout_text or stderr_text.strip()
    return CLIResponse(
        session_id=_extract_conversation_id(log_path),
        result=result,
        is_error=is_error,
        returncode=returncode,
        stderr=stderr_text,
    )


def _extract_conversation_id(log_path: Path) -> str | None:
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for match in _CONVERSATION_RE.finditer(text):
        return next(group for group in match.groups() if group)
    return None


def _format_duration(timeout_seconds: float | None) -> str:
    if timeout_seconds is None:
        return "5m0s"
    seconds = max(1, int(timeout_seconds))
    minutes, rem = divmod(seconds, 60)
    if minutes:
        return f"{minutes}m{rem}s"
    return f"{rem}s"


def _log_cmd(cmd: list[str]) -> None:
    safe_cmd = [(c[:80] + "...") if i == len(cmd) - 1 and len(c) > 80 else c for i, c in enumerate(cmd)]
    logger.info("Antigravity cmd: %s", " ".join(safe_cmd))
