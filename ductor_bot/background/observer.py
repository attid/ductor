"""Background task observer: fire-and-forget CLI execution with notification."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from ductor_bot.background.models import BackgroundResult, BackgroundTask
from ductor_bot.infra.task_runner import run_oneshot_task

if TYPE_CHECKING:
    from ductor_bot.cli.param_resolver import TaskExecutionConfig
    from ductor_bot.workspace.paths import DuctorPaths

logger = logging.getLogger(__name__)

BgResultCallback = Callable[[BackgroundResult], Awaitable[None]]

MAX_TASKS_PER_CHAT = 5


class BackgroundObserver:
    """Manages fire-and-forget background CLI tasks."""

    def __init__(self, paths: DuctorPaths, *, timeout_seconds: float) -> None:
        self._paths = paths
        self._timeout_seconds = timeout_seconds
        self._on_result: BgResultCallback | None = None
        self._tasks: dict[str, BackgroundTask] = {}

    def set_result_handler(self, handler: BgResultCallback) -> None:
        self._on_result = handler

    def submit(
        self,
        chat_id: int,
        prompt: str,
        message_id: int,
        thread_id: int | None,
        exec_config: TaskExecutionConfig,
    ) -> str:
        """Submit a background task. Returns task_id."""
        active = sum(
            1
            for t in self._tasks.values()
            if t.chat_id == chat_id and t.asyncio_task and not t.asyncio_task.done()
        )
        if active >= MAX_TASKS_PER_CHAT:
            msg = f"Too many background tasks ({MAX_TASKS_PER_CHAT} max)"
            raise ValueError(msg)

        task_id = secrets.token_hex(4)
        bg_task = BackgroundTask(
            task_id=task_id,
            chat_id=chat_id,
            prompt=prompt,
            message_id=message_id,
            thread_id=thread_id,
            provider=exec_config.provider,
            model=exec_config.model,
            submitted_at=time.monotonic(),
        )
        atask = asyncio.create_task(self._run(bg_task, exec_config))
        bg_task.asyncio_task = atask
        atask.add_done_callback(lambda _t: self._tasks.pop(task_id, None))
        self._tasks[task_id] = bg_task
        logger.info(
            "Background task submitted id=%s chat=%d provider=%s",
            task_id,
            chat_id,
            exec_config.provider,
        )
        return task_id

    def active_tasks(self, chat_id: int | None = None) -> list[BackgroundTask]:
        tasks = [t for t in self._tasks.values() if t.asyncio_task and not t.asyncio_task.done()]
        if chat_id is not None:
            tasks = [t for t in tasks if t.chat_id == chat_id]
        return tasks

    async def cancel_all(self, chat_id: int) -> int:
        count = 0
        cancelled: list[asyncio.Task[None]] = []
        for task in list(self._tasks.values()):
            if task.chat_id == chat_id and task.asyncio_task and not task.asyncio_task.done():
                task.asyncio_task.cancel()
                cancelled.append(task.asyncio_task)
                count += 1
        if cancelled:
            await asyncio.gather(*cancelled, return_exceptions=True)
        return count

    async def shutdown(self) -> None:
        cancelled: list[asyncio.Task[None]] = []
        for task in list(self._tasks.values()):
            if task.asyncio_task and not task.asyncio_task.done():
                task.asyncio_task.cancel()
                cancelled.append(task.asyncio_task)
        if cancelled:
            await asyncio.gather(*cancelled, return_exceptions=True)
        self._tasks.clear()

    async def _run(self, bg_task: BackgroundTask, exec_config: TaskExecutionConfig) -> None:
        t0 = time.monotonic()
        try:
            result = await run_oneshot_task(
                exec_config,
                bg_task.prompt,
                cwd=self._paths.workspace,
                timeout_seconds=self._timeout_seconds,
                timeout_label="Background task",
            )

            elapsed = time.monotonic() - t0
            await self._deliver(
                BackgroundResult(
                    task_id=bg_task.task_id,
                    chat_id=bg_task.chat_id,
                    message_id=bg_task.message_id,
                    thread_id=bg_task.thread_id,
                    prompt_preview=bg_task.prompt[:60],
                    result_text=result.result_text,
                    status="error:cli_not_found" if result.execution is None else result.status,
                    elapsed_seconds=elapsed,
                    provider=bg_task.provider,
                    model=bg_task.model,
                )
            )
        except asyncio.CancelledError:
            elapsed = time.monotonic() - t0
            with contextlib.suppress(Exception):
                await self._deliver(
                    BackgroundResult(
                        task_id=bg_task.task_id,
                        chat_id=bg_task.chat_id,
                        message_id=bg_task.message_id,
                        thread_id=bg_task.thread_id,
                        prompt_preview=bg_task.prompt[:60],
                        result_text="",
                        status="aborted",
                        elapsed_seconds=elapsed,
                        provider=bg_task.provider,
                        model=bg_task.model,
                    )
                )
            raise
        except Exception:
            logger.exception("Background task failed id=%s", bg_task.task_id)
            elapsed = time.monotonic() - t0
            with contextlib.suppress(Exception):
                await self._deliver(
                    BackgroundResult(
                        task_id=bg_task.task_id,
                        chat_id=bg_task.chat_id,
                        message_id=bg_task.message_id,
                        thread_id=bg_task.thread_id,
                        prompt_preview=bg_task.prompt[:60],
                        result_text="Internal error (check logs)",
                        status="error:internal",
                        elapsed_seconds=elapsed,
                        provider=bg_task.provider,
                        model=bg_task.model,
                    )
                )

    async def _deliver(self, result: BackgroundResult) -> None:
        if self._on_result is None:
            logger.warning("No result handler set for background task %s", result.task_id)
            return
        try:
            await self._on_result(result)
        except Exception:
            logger.exception("Error delivering background result id=%s", result.task_id)
