"""PID lockfile: prevents multiple bot instances from running simultaneously."""

from __future__ import annotations

import contextlib
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

from ductor_bot.infra.atomic_io import atomic_bytes_save
from ductor_bot.infra.platform import CREATION_FLAGS as _CREATION_FLAGS
from ductor_bot.infra.platform import is_windows
from ductor_bot.infra.process_tree import (
    force_kill_process_tree,
    list_process_descendants,
    terminate_process_tree,
)

logger = logging.getLogger(__name__)

_KILL_WAIT_SECONDS = 5.0
_KILL_POLL_INTERVAL = 0.2


def _is_process_alive(pid: int) -> bool:
    """Check if a process with the given PID is still running."""
    if pid <= 0:
        return False
    if is_windows():
        return _is_process_alive_windows(pid)
    return _is_process_alive_posix(pid)


def _is_process_alive_windows(pid: int) -> bool:
    """Check process liveness via tasklist.

    os.kill(pid, 0) is unsafe here: on Windows any signal other than
    CTRL_C_EVENT/CTRL_BREAK_EVENT terminates the target process.
    """
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
            creationflags=_CREATION_FLAGS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return f'"{pid}"' in result.stdout


def _is_process_alive_posix(pid: int) -> bool:
    """Check process liveness with POSIX signal 0 semantics."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _terminate_process(pid: int) -> None:
    """Send a graceful termination signal to a process tree."""
    terminate_process_tree(pid)


def _force_kill_process(pid: int) -> None:
    """Force-kill a process tree."""
    force_kill_process_tree(pid)


def _kill_and_wait(pid: int) -> None:
    """Send termination signal, wait for exit, escalate to force-kill if needed."""
    logger.info("Stopping existing bot instance (pid=%d)", pid)
    descendants = [child for child in list_process_descendants(pid) if child != os.getpid()]
    try:
        _terminate_process(pid)
    except OSError:
        logger.warning("Failed to terminate pid=%d", pid, exc_info=True)
        return

    deadline = time.monotonic() + _KILL_WAIT_SECONDS
    while _is_process_alive(pid) and time.monotonic() < deadline:
        time.sleep(_KILL_POLL_INTERVAL)

    if _is_process_alive(pid):
        logger.warning("pid=%d did not exit after %.0fs, force killing", pid, _KILL_WAIT_SECONDS)
        with contextlib.suppress(OSError):
            _force_kill_process(pid)
        time.sleep(_KILL_POLL_INTERVAL)
    else:
        logger.info("Previous instance (pid=%d) exited cleanly", pid)

    alive_desc = _alive_pids(descendants)
    if not alive_desc:
        return

    logger.warning(
        "Cleaning up %d orphan child process(es) for pid=%d",
        len(alive_desc),
        pid,
    )
    for child_pid in alive_desc:
        with contextlib.suppress(OSError):
            _force_kill_process(child_pid)

    child_deadline = time.monotonic() + _KILL_WAIT_SECONDS
    remaining = _alive_pids(alive_desc)
    while remaining and time.monotonic() < child_deadline:
        time.sleep(_KILL_POLL_INTERVAL)
        remaining = _alive_pids(remaining)

    if remaining:
        logger.warning("Some child processes did not exit: %s", ",".join(str(p) for p in remaining))


def _alive_pids(pids: list[int]) -> list[int]:
    return [pid for pid in pids if _is_process_alive(pid)]


def _argv_looks_like_ductor(argv: list[bytes]) -> bool:
    """Match structural launch signatures against NUL-split cmdline tokens."""
    if not argv:
        return False
    basename0 = argv[0].rsplit(b"/", 1)[-1]
    if basename0 in (b"ductor", b"ductor_bot"):
        return True
    if not basename0.startswith((b"python", b"pypy")):
        return False
    for i, token in enumerate(argv[1:], start=1):
        basename = token.rsplit(b"/", 1)[-1]
        if argv[i - 1] == b"-m" and basename.startswith(b"ductor_bot"):
            return True
        if i == 1:
            is_script = basename == b"ductor" or (
                basename == b"__main__.py" and b"ductor_bot" in token
            )
            if is_script:
                return True
    return False


def _is_ductor_process(pid: int) -> bool | None:
    """Best-effort identity probe for a live PID via ``/proc/<pid>/cmdline``.

    Matches only structural signatures of a ductor launch (see
    ``_argv_looks_like_ductor``): flag values and path arguments of unrelated
    tools (``journalctl -u ductor``, ``git diff ductor_bot/...``,
    ``grep -r foo /home/user/ductor``) do not match. Returns True/False when
    the command line is readable, None when identity cannot be determined
    (non-Linux platform, restricted /proc) so callers keep the legacy
    behavior. A vanished ``/proc/<pid>`` (process exited between the
    liveness check and this read) and an empty cmdline (zombie, kernel
    thread) read as False: a dead process cannot be a running instance.

    Limitation: launchers with no ductor token in argv (a custom
    ``python run.py`` wrapper) are indistinguishable from foreign processes
    and are treated as stale.
    """
    if sys.platform != "linux":
        return None
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
    except FileNotFoundError:
        return False
    except OSError:
        return None
    if not cmdline:
        return False
    return _argv_looks_like_ductor(cmdline.split(b"\x00"))


def acquire_lock(*, pid_file: Path, kill_existing: bool = False) -> None:
    """Write PID file after ensuring no other instance is running.

    Args:
        pid_file: Path to the PID lockfile.
        kill_existing: If True, kill any running instance before acquiring.
                       If False, raise ``SystemExit`` when another instance is found.
    """
    pid_file.parent.mkdir(parents=True, exist_ok=True)

    if pid_file.exists():
        try:
            existing_pid = int(pid_file.read_text(encoding="utf-8").strip())
        except (ValueError, OSError):
            existing_pid = None

        if existing_pid == os.getpid():
            # A persistent pid_file can survive container recreation, and a
            # fresh PID namespace reuses the same deterministic PID. Liveness
            # would match this very process and the kill branch would take
            # down the process subtree (e.g. sidecars started by the
            # entrypoint), so the file is simply stale.
            logger.warning("Stale PID file contains our own pid=%d, overwriting", existing_pid)
        elif existing_pid is not None and _is_process_alive(existing_pid):
            if _is_ductor_process(existing_pid) is False:
                logger.warning(
                    "PID %d is alive but is not a ductor process (stale after PID reuse),"
                    " overwriting",
                    existing_pid,
                )
            elif kill_existing:
                _kill_and_wait(existing_pid)
            else:
                logger.error(
                    "Another bot instance is already running (pid=%d). "
                    "Kill it first or delete %s if stale.",
                    existing_pid,
                    pid_file,
                )
                raise SystemExit(1)
        else:
            logger.warning("Stale PID file found (pid=%s), overwriting", existing_pid)

    atomic_bytes_save(pid_file, str(os.getpid()).encode())
    logger.info("PID lock acquired (pid=%d)", os.getpid())


def release_lock(*, pid_file: Path) -> None:
    """Remove PID file if it belongs to the current process."""
    if not pid_file.exists():
        return
    try:
        stored_pid = int(pid_file.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        pid_file.unlink(missing_ok=True)
        return

    if stored_pid == os.getpid():
        pid_file.unlink(missing_ok=True)
        logger.info("PID lock released")
    else:
        logger.debug("PID file belongs to pid=%d, not removing", stored_pid)
