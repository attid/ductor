"""Cross-platform process-tree termination helpers."""

from __future__ import annotations

import contextlib
import logging
import os
import signal
import subprocess
import sys

logger = logging.getLogger(__name__)

_IS_WINDOWS = sys.platform == "win32"

_PS_TIMEOUT_SECONDS = 5.0
_TASKKILL_TIMEOUT_SECONDS = 5.0


def list_process_descendants(pid: int) -> list[int]:
    """Return recursive child PIDs for *pid* on POSIX systems.

    On Windows this returns an empty list because recursive enumeration is
    delegated to ``taskkill /T`` in kill helpers.
    """
    if _IS_WINDOWS or pid <= 0:
        return []

    snapshot = _read_process_snapshot()
    if not snapshot:
        return []

    children: dict[int, list[int]] = {}
    for line in snapshot.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        with contextlib.suppress(ValueError):
            child_pid = int(parts[0])
            parent_pid = int(parts[1])
            children.setdefault(parent_pid, []).append(child_pid)

    descendants: list[int] = []
    seen: set[int] = set()
    stack = list(children.get(pid, []))
    while stack:
        child = stack.pop()
        if child in seen:
            continue
        seen.add(child)
        descendants.append(child)
        stack.extend(children.get(child, []))
    return descendants


def terminate_process_tree(pid: int) -> None:
    """Send a graceful termination signal to a process tree."""
    if pid <= 0:
        return

    if _IS_WINDOWS:
        _run_taskkill(pid, force=False)
        return

    targets = [pid, *list_process_descendants(pid)]
    _send_posix_signal(targets, signal.SIGTERM)


def force_kill_process_tree(pid: int) -> None:
    """Force-kill a process tree."""
    if pid <= 0:
        return

    if _IS_WINDOWS:
        _run_taskkill(pid, force=True)
        return

    # Kill descendants before root to avoid reparenting survivors.
    targets = [*list_process_descendants(pid), pid]
    _send_posix_signal(targets, signal.SIGKILL)


def _run_taskkill(pid: int, *, force: bool) -> None:
    cmd = ["taskkill"]
    if force:
        cmd.append("/F")
    cmd.extend(["/T", "/PID", str(pid)])
    with contextlib.suppress(OSError, subprocess.TimeoutExpired):
        subprocess.run(
            cmd,
            capture_output=True,
            check=False,
            timeout=_TASKKILL_TIMEOUT_SECONDS,
        )


def _send_posix_signal(targets: list[int], sig: signal.Signals) -> None:
    current_pid = os.getpid()
    for target in targets:
        if target <= 0 or target == current_pid:
            continue
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.kill(target, sig)


def kill_all_ductor_processes() -> int:
    """Find and force-kill remaining ``ductor`` processes system-wide.

    On Windows: scans ``tasklist`` for processes whose image name contains
    ``ductor`` (e.g. ``ductor.exe``).  On POSIX this is a no-op because the
    PID-file mechanism is sufficient and broad ``pgrep`` patterns would
    unsafely match unrelated processes (editors, shells in a ductor directory).

    Skips the current process so the caller survives.
    Returns the number of processes killed.
    """
    if not _IS_WINDOWS:
        return 0

    current = os.getpid()
    return _kill_all_ductor_windows(current)


def _kill_all_ductor_windows(current_pid: int) -> int:
    """Use ``tasklist`` to find ductor processes, then ``taskkill /F /T``."""
    try:
        result = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            check=False,
            timeout=_TASKKILL_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 0

    killed = 0
    for line in result.stdout.splitlines():
        parts = line.strip().strip('"').split('","')
        if len(parts) < 2:
            continue
        name = parts[0].lower()
        if name not in ("ductor.exe", "ductor"):
            continue
        with contextlib.suppress(ValueError):
            pid = int(parts[1])
            if pid == current_pid or pid <= 0:
                continue
            logger.info("Killing ductor process: pid=%d name=%s", pid, name)
            _run_taskkill(pid, force=True)
            killed += 1
    return killed


def _read_process_snapshot() -> str:
    with contextlib.suppress(OSError, subprocess.TimeoutExpired):
        result = subprocess.run(
            ["ps", "-axo", "pid=,ppid="],
            capture_output=True,
            text=True,
            check=False,
            timeout=_PS_TIMEOUT_SECONDS,
        )
        if result.returncode == 0:
            return result.stdout

    logger.debug("Failed to read process snapshot via ps")
    return ""
