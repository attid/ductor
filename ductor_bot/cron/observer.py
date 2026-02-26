"""In-process cron job scheduler: watches cron_jobs.json, schedules and executes jobs."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from cronsim import CronSim, CronSimError

from ductor_bot.cli.param_resolver import TaskOverrides, resolve_cli_config
from ductor_bot.config import resolve_user_timezone
from ductor_bot.cron.execution import enrich_instruction
from ductor_bot.cron.manager import CronManager
from ductor_bot.infra.file_watcher import FileWatcher
from ductor_bot.infra.task_runner import check_folder, run_oneshot_task
from ductor_bot.log_context import set_log_context
from ductor_bot.utils.quiet_hours import check_quiet_hour

if TYPE_CHECKING:
    from ductor_bot.cli.codex_cache import CodexModelCache
    from ductor_bot.cli.param_resolver import TaskExecutionConfig
    from ductor_bot.config import AgentConfig
    from ductor_bot.cron.manager import CronJob
    from ductor_bot.workspace.paths import DuctorPaths

logger = logging.getLogger(__name__)

# Callback signature: (job_title, result_text, status)
CronResultCallback = Callable[[str, str, str], Awaitable[None]]


@dataclass(slots=True)
class _ScheduledJob:
    """Scheduling payload for one cron entry."""

    id: str
    schedule: str
    instruction: str
    task_folder: str
    timezone: str


class CronObserver:
    """Watches cron_jobs.json and schedules jobs in-process.

    On start: reads all jobs, calculates next run times via cronsim,
    and schedules asyncio tasks. A background watcher polls the JSON
    file's mtime every 5 seconds; on change it reloads and reschedules.
    """

    def __init__(
        self,
        paths: DuctorPaths,
        manager: CronManager,
        *,
        config: AgentConfig,
        codex_cache: CodexModelCache,
    ) -> None:
        self._paths = paths
        self._manager = manager
        self._config = config
        self._codex_cache = codex_cache
        self._on_result: CronResultCallback | None = None
        self._scheduled: dict[str, asyncio.Task[None]] = {}
        self._reschedule_lock = asyncio.Lock()
        self._requested_reschedule_task: asyncio.Task[None] | None = None
        self._running = False
        self._watcher = FileWatcher(
            paths.cron_jobs_path,
            self._on_file_change,
        )

    def set_result_handler(self, handler: CronResultCallback) -> None:
        """Set callback for job results (called after each execution)."""
        self._on_result = handler

    async def start(self) -> None:
        """Start the observer: schedule all jobs and begin watching."""
        self._running = True
        await self._schedule_all()
        await self._watcher.start()
        logger.info("CronObserver started (%d jobs scheduled)", len(self._scheduled))

    async def stop(self) -> None:
        """Stop the observer: cancel all scheduled jobs and the watcher."""
        self._running = False
        await self._watcher.stop()
        request_task = self._requested_reschedule_task
        self._requested_reschedule_task = None
        if request_task is not None:
            request_task.cancel()
            await asyncio.gather(request_task, return_exceptions=True)
        tasks = list(self._scheduled.values())
        for task in tasks:
            task.cancel()
        self._scheduled.clear()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        logger.info("CronObserver stopped")

    def request_reschedule(self) -> None:
        """Queue a background reschedule request without blocking the caller."""
        if not self._running:
            return
        task = self._requested_reschedule_task
        if task is not None and not task.done():
            return
        self._requested_reschedule_task = asyncio.create_task(self._run_requested_reschedule())

    async def reschedule_now(self) -> None:
        """Reschedule all jobs immediately (used by interactive cron toggles)."""
        if not self._running:
            return
        await self._update_mtime()
        await self._reschedule_locked()

    async def _run_requested_reschedule(self) -> None:
        """Execute one queued reschedule request and log failures."""
        try:
            await self.reschedule_now()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Background cron reschedule failed")
        finally:
            self._requested_reschedule_task = None

    # -- File watcher callback --

    async def _on_file_change(self) -> None:
        """Reload manager in a thread, then reschedule."""
        logger.info("File watcher detected cron_jobs.json change, rescheduling")
        await asyncio.to_thread(self._manager.reload)
        await self._reschedule_locked()

    # -- Scheduling --

    async def _schedule_all(self) -> None:
        """Schedule asyncio tasks for all enabled jobs."""
        await self._watcher.update_mtime()
        for job in self._manager.list_jobs():
            if job.enabled:
                self._schedule_job(
                    job.id,
                    job.schedule,
                    job.agent_instruction,
                    job.task_folder,
                    job.timezone,
                )

    async def _reschedule_all(self) -> None:
        """Cancel existing schedules, await their termination, then reschedule.

        Awaiting cancellation prevents a race where the old task (executing a
        subprocess via asyncio.to_thread) is not yet interrupted and runs
        concurrently with the newly created replacement task.
        """
        tasks = list(self._scheduled.values())
        for task in tasks:
            task.cancel()
        self._scheduled.clear()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self._schedule_all()
        logger.info("Rescheduled %d jobs", len(self._scheduled))

    async def _reschedule_locked(self) -> None:
        """Serialize reschedules from watcher and interactive updates."""
        async with self._reschedule_lock:
            await self._reschedule_all()

    def _schedule_job(
        self,
        job_id: str,
        schedule: str,
        instruction: str,
        task_folder: str,
        job_timezone: str = "",
    ) -> None:
        """Calculate next run time and schedule an asyncio task.

        Uses the job's timezone (if set), then the global ``user_timezone``
        config, then the host OS timezone, and finally UTC as last resort.
        CronSim iterates in the resolved local timezone so that ``0 9 * * *``
        means 09:00 in the user's wall-clock time.
        """
        try:
            tz = resolve_user_timezone(job_timezone or self._config.user_timezone)
            now_local = datetime.now(tz)
            # CronSim works on time components; feed it the local time
            # so hour fields match the user's wall clock.
            now_naive = now_local.replace(tzinfo=None)
            it = CronSim(schedule, now_naive)
            next_naive: datetime = next(it)
            # Re-attach the timezone using fold=0 (prefer pre-DST interpretation
            # for ambiguous times).  For non-existent times (DST spring-forward
            # gap) the delay becomes negative; in that case advance to the next
            # cron slot so the job fires at the correct wall-clock time.
            next_aware = next_naive.replace(tzinfo=tz)  # fold=0 default
            delay = (next_aware - datetime.now(tz)).total_seconds()
            if delay < 0:
                next_naive = next(it)
                next_aware = next_naive.replace(tzinfo=tz)
                delay = (next_aware - datetime.now(tz)).total_seconds()
            delay = max(delay, 0)
            scheduled_job = _ScheduledJob(
                id=job_id,
                schedule=schedule,
                instruction=instruction,
                task_folder=task_folder,
                timezone=job_timezone,
            )
            task = asyncio.create_task(
                self._run_at(delay, scheduled_job),
            )
            self._scheduled[job_id] = task
            logger.debug(
                "Scheduled %s: next run %s (%s), delay %.0fs",
                job_id,
                next_naive.isoformat(),
                tz.key,
                delay,
            )
        except (CronSimError, StopIteration):
            logger.warning("Invalid cron expression for job %s: %s", job_id, schedule)
        except Exception:
            logger.exception("Failed to schedule job %s", job_id)

    async def _run_at(self, delay: float, scheduled_job: _ScheduledJob) -> None:
        """Wait for delay, execute the job, then reschedule for next occurrence."""
        try:
            await asyncio.sleep(delay)
            await self._execute_job(
                scheduled_job.id,
                scheduled_job.instruction,
                scheduled_job.task_folder,
            )
        except asyncio.CancelledError:
            logger.warning("Cron job %s cancelled during execution", scheduled_job.id)
            return
        except Exception:
            logger.exception("Cron job %s failed unexpectedly", scheduled_job.id)
        if self._running:
            self._schedule_job(
                scheduled_job.id,
                scheduled_job.schedule,
                scheduled_job.instruction,
                scheduled_job.task_folder,
                scheduled_job.timezone,
            )

    # -- Execution --

    def _resolve_execution_config(
        self,
        task_overrides: TaskOverrides,
    ) -> TaskExecutionConfig:
        """Use param_resolver to get final config for this task."""
        return resolve_cli_config(
            self._config,
            self._codex_cache,
            task_overrides=task_overrides,
        )

    async def _deliver_result(
        self, job_id: str, job_title: str, result_text: str, status: str
    ) -> None:
        """Send result to the external handler (e.g. Telegram).

        Uses *job_title* (computed at execution start) so delivery works even
        if the job was removed from the manager mid-execution.
        """
        if self._on_result:
            try:
                await self._on_result(job_title, result_text, status)
            except Exception:
                logger.exception("Error in cron result handler for job %s", job_id)

    async def _execute_job(
        self,
        job_id: str,
        instruction: str,
        task_folder: str,
    ) -> None:
        """Spawn a fresh CLI session in the cron_task folder."""
        set_log_context(operation="cron")
        job = self._manager.get_job(job_id)
        job_title = job.title if job else job_id

        # Acquire dependency lock (if needed)
        from ductor_bot.cron.dependency_queue import get_dependency_queue

        dep_queue = get_dependency_queue()
        dependency = job.dependency if job else None

        async with dep_queue.acquire(job_id, job_title, dependency):
            logger.info("Cron job starting job=%s", job_title)

            if self._is_quiet_hours(job, job_title):
                return

            t0 = time.monotonic()

            folder = self._paths.cron_tasks_dir / task_folder
            if not await check_folder(folder):
                logger.error("Cron task folder missing: %s", folder)
                self._manager.update_run_status(job_id, status="error:folder_missing")
                return

            overrides = TaskOverrides(
                provider=job.provider if job else None,
                model=job.model if job else None,
                reasoning_effort=job.reasoning_effort if job else None,
                cli_parameters=job.cli_parameters if job else [],
            )
            exec_config = self._resolve_execution_config(overrides)
            enriched = enrich_instruction(instruction, task_folder)

            logger.debug(
                "Cron subprocess cwd=%s provider=%s model=%s timeout=%.0fs",
                folder,
                exec_config.provider,
                exec_config.model,
                self._config.cli_timeout,
            )

            try:
                result = await run_oneshot_task(
                    exec_config,
                    enriched,
                    cwd=folder,
                    timeout_seconds=self._config.cli_timeout,
                    timeout_label="Cron job",
                )
            except asyncio.CancelledError:
                logger.warning("Cron job %s cancelled during subprocess", job_id)
                raise

            logger.info(
                "Cron subprocess returned job=%s status=%s has_execution=%s",
                job_id,
                result.status,
                result.execution is not None,
            )

            if result.execution is None:
                # CLI not found — deliver error to chat, then persist status
                logger.error("%s CLI not found for cron job %s", exec_config.provider, job_id)
                await self._deliver_result(job_id, job_title, result.result_text, result.status)
                self._manager.update_run_status(job_id, status=result.status)
                return

            if result.execution.timed_out:
                logger.warning(
                    "Cron job %s timed out after %.0fs, killing process",
                    job_id,
                    self._config.cli_timeout,
                )

            if result.execution.stderr:
                logger.debug(
                    "Cron stderr (%s): %s",
                    job_id,
                    result.execution.stderr.decode(errors="replace")[:500],
                )

            elapsed_ms = (time.monotonic() - t0) * 1000
            logger.info(
                "Cron job completed job=%s status=%s duration_ms=%.0f stdout=%d result=%d",
                job_title,
                result.status,
                elapsed_ms,
                len(result.execution.stdout),
                len(result.result_text),
            )

            # Deliver result BEFORE writing run-status to disk.  The file
            # write can trigger the file-watcher which reschedules (and
            # cancels) running tasks.  Delivering first guarantees the
            # Telegram message is sent even if the task is cancelled during
            # the subsequent file I/O.
            await self._deliver_result(job_id, job_title, result.result_text, result.status)

            self._manager.update_run_status(job_id, status=result.status)
            # Refresh our mtime baseline so the file-watcher doesn't treat the
            # run-status write as a user-initiated change and trigger a full
            # reschedule of all other jobs.
            await self._watcher.update_mtime()

    def _is_quiet_hours(self, job: CronJob | None, job_title: str) -> bool:
        """Return True when the job must be skipped due to quiet-hour settings."""
        job_start = job.quiet_start if job else None
        job_end = job.quiet_end if job else None

        # Cron jobs only respect quiet hours explicitly set on the job itself.
        # Do NOT fall back to heartbeat quiet hours.
        if job_start is None and job_end is None:
            return False

        is_quiet, now_hour, tz = check_quiet_hour(
            quiet_start=job_start,
            quiet_end=job_end,
            user_timezone=self._config.user_timezone,
            global_quiet_start=0,
            global_quiet_end=0,
        )
        if not is_quiet:
            return False

        logger.debug(
            "Cron job skipped: quiet hours (%d:00 %s) job=%s",
            now_hour,
            tz.key,
            job_title,
        )
        return True

    async def _update_mtime(self) -> None:
        """Cache the current mtime of the jobs file."""
        await self._watcher.update_mtime()
