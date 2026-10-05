"""Tests for PID lockfile management."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest


class TestIsProcessAlive:
    """Test process liveness detection."""

    def test_current_process_is_alive(self) -> None:
        from ductor_bot.infra.pidlock import _is_process_alive

        assert _is_process_alive(os.getpid()) is True

    def test_nonexistent_pid_is_dead(self) -> None:
        from ductor_bot.infra.pidlock import _is_process_alive

        # PID 2^30 is extremely unlikely to exist
        assert _is_process_alive(2**30) is False

    def test_permission_error_means_alive(self) -> None:
        from ductor_bot.infra.pidlock import _is_process_alive

        with (
            patch("ductor_bot.infra.pidlock.is_windows", return_value=False),
            patch("os.kill", side_effect=PermissionError),
        ):
            assert _is_process_alive(999) is True


class TestAcquireLock:
    """Test PID lock acquisition."""

    def test_creates_pid_file(self, tmp_path: Path) -> None:
        from ductor_bot.infra.pidlock import acquire_lock, release_lock

        pid_file = tmp_path / "bot.pid"
        acquire_lock(pid_file=pid_file)
        try:
            assert pid_file.exists()
            assert pid_file.read_text(encoding="utf-8").strip() == str(os.getpid())
        finally:
            release_lock(pid_file=pid_file)

    def test_stale_pid_file_overwritten(self, tmp_path: Path) -> None:
        from ductor_bot.infra.pidlock import acquire_lock, release_lock

        pid_file = tmp_path / "bot.pid"
        # Write a PID that doesn't exist
        pid_file.write_text("999999999", encoding="utf-8")

        acquire_lock(pid_file=pid_file)
        try:
            assert pid_file.read_text(encoding="utf-8").strip() == str(os.getpid())
        finally:
            release_lock(pid_file=pid_file)

    def test_corrupt_pid_file_overwritten(self, tmp_path: Path) -> None:
        from ductor_bot.infra.pidlock import acquire_lock, release_lock

        pid_file = tmp_path / "bot.pid"
        pid_file.write_text("not-a-number", encoding="utf-8")

        acquire_lock(pid_file=pid_file)
        try:
            assert pid_file.exists()
        finally:
            release_lock(pid_file=pid_file)

    def test_active_pid_without_kill_raises_system_exit(self, tmp_path: Path) -> None:
        from ductor_bot.infra.pidlock import acquire_lock

        pid_file = tmp_path / "bot.pid"
        pid_file.write_text("999999999", encoding="utf-8")

        with (
            patch("ductor_bot.infra.pidlock._is_process_alive", return_value=True),
            patch("ductor_bot.infra.pidlock._is_ductor_process", return_value=True),
            pytest.raises(SystemExit),
        ):
            # A foreign live ductor instance without kill_existing should fail
            acquire_lock(pid_file=pid_file)

    def test_own_pid_in_file_overwrites_without_kill(self, tmp_path: Path) -> None:
        """Container recreation reuses the same PID; the file must go stale, not kill."""
        from ductor_bot.infra.pidlock import acquire_lock

        pid_file = tmp_path / "bot.pid"
        pid_file.write_text(str(os.getpid()), encoding="utf-8")

        with patch("ductor_bot.infra.pidlock._kill_and_wait") as mock_kill:
            acquire_lock(pid_file=pid_file, kill_existing=True)

        mock_kill.assert_not_called()
        assert pid_file.read_text(encoding="utf-8").strip() == str(os.getpid())

    def test_own_pid_in_file_without_kill_overwrites_not_exits(self, tmp_path: Path) -> None:
        from ductor_bot.infra.pidlock import acquire_lock

        pid_file = tmp_path / "bot.pid"
        pid_file.write_text(str(os.getpid()), encoding="utf-8")

        acquire_lock(pid_file=pid_file, kill_existing=False)

        assert pid_file.read_text(encoding="utf-8").strip() == str(os.getpid())

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX process tree test")
    def test_own_pid_in_file_spares_subtree(self, tmp_path: Path) -> None:
        """Real reproduction: a live child (sidecar) must survive acquisition."""
        from ductor_bot.infra.pidlock import acquire_lock

        child = subprocess.Popen(["sleep", "30"])
        try:
            pid_file = tmp_path / "bot.pid"
            pid_file.write_text(str(os.getpid()), encoding="utf-8")

            acquire_lock(pid_file=pid_file, kill_existing=True)

            assert child.poll() is None, "sidecar child was killed by acquire_lock"
        finally:
            child.terminate()
            child.wait()

    def test_foreign_live_process_treated_stale(self, tmp_path: Path) -> None:
        """A reused PID belonging to a non-ductor process must not be killed."""
        from ductor_bot.infra.pidlock import acquire_lock

        pid_file = tmp_path / "bot.pid"
        pid_file.write_text("424242", encoding="utf-8")

        with (
            patch("ductor_bot.infra.pidlock._is_process_alive", return_value=True),
            patch("ductor_bot.infra.pidlock._is_ductor_process", return_value=False),
            patch("ductor_bot.infra.pidlock._kill_and_wait") as mock_kill,
        ):
            acquire_lock(pid_file=pid_file, kill_existing=True)

        mock_kill.assert_not_called()
        assert pid_file.read_text(encoding="utf-8").strip() == str(os.getpid())

    def test_foreign_live_process_without_kill_overwrites(self, tmp_path: Path) -> None:
        from ductor_bot.infra.pidlock import acquire_lock

        pid_file = tmp_path / "bot.pid"
        pid_file.write_text("424242", encoding="utf-8")

        with (
            patch("ductor_bot.infra.pidlock._is_process_alive", return_value=True),
            patch("ductor_bot.infra.pidlock._is_ductor_process", return_value=False),
        ):
            acquire_lock(pid_file=pid_file, kill_existing=False)

        assert pid_file.read_text(encoding="utf-8").strip() == str(os.getpid())

    def test_unknown_identity_with_kill_runs_legacy_kill(self, tmp_path: Path) -> None:
        from ductor_bot.infra.pidlock import acquire_lock

        pid_file = tmp_path / "bot.pid"
        pid_file.write_text("424242", encoding="utf-8")

        with (
            patch("ductor_bot.infra.pidlock._is_process_alive", return_value=True),
            patch("ductor_bot.infra.pidlock._is_ductor_process", return_value=None),
            patch("ductor_bot.infra.pidlock._kill_and_wait") as mock_kill,
        ):
            acquire_lock(pid_file=pid_file, kill_existing=True)

        mock_kill.assert_called_once_with(424242)

    def test_unknown_identity_without_kill_raises_system_exit(self, tmp_path: Path) -> None:
        from ductor_bot.infra.pidlock import acquire_lock

        pid_file = tmp_path / "bot.pid"
        pid_file.write_text("424242", encoding="utf-8")

        with (
            patch("ductor_bot.infra.pidlock._is_process_alive", return_value=True),
            patch("ductor_bot.infra.pidlock._is_ductor_process", return_value=None),
            pytest.raises(SystemExit),
        ):
            acquire_lock(pid_file=pid_file, kill_existing=False)

    def test_live_ductor_process_is_killed_and_acquires(self, tmp_path: Path) -> None:
        """Honest second ductor instance still goes through the kill path."""
        from ductor_bot.infra.pidlock import acquire_lock

        pid_file = tmp_path / "bot.pid"
        pid_file.write_text("424242", encoding="utf-8")

        with (
            patch("ductor_bot.infra.pidlock._is_process_alive", return_value=True),
            patch("ductor_bot.infra.pidlock._is_ductor_process", return_value=True),
            patch("ductor_bot.infra.pidlock._kill_and_wait") as mock_kill,
        ):
            acquire_lock(pid_file=pid_file, kill_existing=True)

        mock_kill.assert_called_once_with(424242)
        assert pid_file.read_text(encoding="utf-8").strip() == str(os.getpid())

    def test_active_pid_with_kill_kills_and_acquires(self, tmp_path: Path) -> None:
        from ductor_bot.infra.pidlock import acquire_lock, release_lock

        pid_file = tmp_path / "bot.pid"
        fake_pid = 999999999
        pid_file.write_text(str(fake_pid), encoding="utf-8")

        with (
            patch("ductor_bot.infra.pidlock._is_process_alive", return_value=True),
            # Identity undeterminable -> legacy kill path.
            patch("ductor_bot.infra.pidlock._is_ductor_process", return_value=None),
            patch("ductor_bot.infra.pidlock._kill_and_wait") as mock_kill,
        ):
            acquire_lock(pid_file=pid_file, kill_existing=True)

        try:
            mock_kill.assert_called_once_with(fake_pid)
            assert pid_file.read_text(encoding="utf-8").strip() == str(os.getpid())
        finally:
            release_lock(pid_file=pid_file)

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        from ductor_bot.infra.pidlock import acquire_lock, release_lock

        pid_file = tmp_path / "deep" / "nested" / "bot.pid"
        acquire_lock(pid_file=pid_file)
        try:
            assert pid_file.exists()
        finally:
            release_lock(pid_file=pid_file)


class TestIsDuctorProcess:
    """Direct tests for the /proc cmdline identity probe."""

    def _spawn_child(
        self, tmp_path: Path, *extra_args: str
    ) -> tuple[subprocess.Popen[bytes], Path]:
        """Spawn a sleeping python child; returns it with a file written post-exec."""
        marker = tmp_path / "exec-ready"
        code = (
            f"import pathlib, time; pathlib.Path({str(marker)!r}).write_text('x'); time.sleep(30)"
        )
        child = subprocess.Popen([sys.executable, "-c", code, *extra_args])
        deadline = time.monotonic() + 10.0
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        return child, marker

    @pytest.mark.skipif(sys.platform != "linux", reason="/proc-based probe")
    def test_console_script_launch_matches(self, tmp_path: Path) -> None:
        """A real shebang console script named ductor: kernel leaves the script
        path as argv[1] right after the interpreter."""
        from ductor_bot.infra.pidlock import _is_ductor_process

        marker = tmp_path / "exec-ready"
        script = tmp_path / "ductor"
        script.write_text(
            f"#!{sys.executable}\n"
            "import pathlib, time\n"
            f"pathlib.Path({str(marker)!r}).write_text('x')\n"
            "time.sleep(30)\n"
        )
        script.chmod(0o755)
        child = subprocess.Popen([str(script)])
        deadline = time.monotonic() + 10.0
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        try:
            assert marker.exists(), "child did not exec in time"
            assert _is_ductor_process(child.pid) is True
        finally:
            child.terminate()
            child.wait()

    @pytest.mark.skipif(sys.platform != "linux", reason="/proc-based probe")
    def test_module_launch_matches(self, tmp_path: Path) -> None:
        from ductor_bot.infra.pidlock import _is_ductor_process

        child, marker = self._spawn_child(tmp_path, "-m", "ductor_bot")
        try:
            assert marker.exists(), "child did not exec in time"
            assert _is_ductor_process(child.pid) is True
        finally:
            child.terminate()
            child.wait()

    @pytest.mark.skipif(sys.platform != "linux", reason="/proc-based probe")
    def test_real_foreign_process_does_not_match(self, tmp_path: Path) -> None:
        from ductor_bot.infra.pidlock import _is_ductor_process

        child, marker = self._spawn_child(tmp_path)
        try:
            assert marker.exists(), "child did not exec in time"
            assert _is_ductor_process(child.pid) is False
        finally:
            child.terminate()
            child.wait()

    @pytest.mark.skipif(sys.platform != "linux", reason="/proc-based probe")
    def test_missing_process_reads_as_not_ductor(self) -> None:
        """Process died between liveness check and cmdline read -> stale, not None."""
        from ductor_bot.infra.pidlock import _is_ductor_process

        assert _is_ductor_process(2**30) is False

    def test_non_linux_platform_reads_as_unknown(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")

        from ductor_bot.infra.pidlock import _is_ductor_process

        assert _is_ductor_process(12345) is None

    @pytest.mark.skipif(sys.platform != "linux", reason="/proc-based probe")
    def test_unreadable_cmdline_reads_as_unknown(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _raise_permission_error(*_args: object) -> bytes:
            raise PermissionError

        monkeypatch.setattr("pathlib.Path.read_bytes", _raise_permission_error)

        from ductor_bot.infra.pidlock import _is_ductor_process

        assert _is_ductor_process(12345) is None

    @pytest.mark.skipif(sys.platform != "linux", reason="/proc-based probe")
    def test_empty_cmdline_reads_as_not_ductor(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Zombie / kernel thread: no cmdline -> cannot be a running instance."""
        monkeypatch.setattr("pathlib.Path.read_bytes", lambda *_args: b"")

        from ductor_bot.infra.pidlock import _is_ductor_process

        assert _is_ductor_process(12345) is False

    @pytest.mark.skipif(sys.platform != "linux", reason="/proc-based probe")
    def test_process_dying_between_checks_treated_stale(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Liveness passes, then the cmdline read hits a dead pid -> stale, no kill."""
        from ductor_bot.infra.pidlock import acquire_lock

        pid_file = tmp_path / "bot.pid"
        pid_file.write_text("424242", encoding="utf-8")

        def _raise_missing(_self: Path) -> bytes:
            raise FileNotFoundError

        monkeypatch.setattr("ductor_bot.infra.pidlock._is_process_alive", lambda _pid: True)
        monkeypatch.setattr("pathlib.Path.read_bytes", _raise_missing)

        with patch("ductor_bot.infra.pidlock._kill_and_wait") as mock_kill:
            acquire_lock(pid_file=pid_file, kill_existing=True)

        mock_kill.assert_not_called()
        assert pid_file.read_text(encoding="utf-8").strip() == str(os.getpid())


class TestArgvLooksLikeDuctor:
    """Table tests for the pure argv matcher (no fork/exec overhead)."""

    def test_signature_table(self) -> None:
        from ductor_bot.infra.pidlock import _argv_looks_like_ductor

        matches = [
            [b"/opt/venv/bin/ductor"],  # console script (shebang leaves path in argv)
            [b"python", b"/opt/venv/bin/ductor"],
            [b"ductor"],
            [b"ductor_bot"],
            [b"python", b"-m", b"ductor_bot"],
            [b"python", b"-m", b"ductor_bot.__main__"],
            [b"python", b"-m", b"ductor_bot.cli"],
            [b"python", b"-u", b"-m", b"ductor_bot"],
            [b"python", b"-O", b"-m", b"ductor_bot"],
            [b"pypy3", b"-m", b"ductor_bot"],
            [b"python", b"/app/ductor_bot/__main__.py"],
            [b"ductor", b""],
            [b"python", b"-m", b"ductor_bot", b""],
        ]
        non_matches = [
            [],
            [b"journalctl", b"-u", b"ductor"],
            [b"git", b"diff", b"ductor_bot/infra/pidlock.py"],
            [b"git", b"diff", b"ductor_bot/__main__.py"],
            [b"cat", b"ductor_bot/__main__.py"],
            [b"ls", b"/home/node/ductor"],
            [b"tar", b"-czf", b"backup.tar.gz", b"/home/node/ductor"],
            [b"grep", b"-r", b"foo", b"/home/user/ductor"],
            [b"grep", b"ductor"],
            [b"git", b"commit", b"-m", b"ductor_bot"],
            [b"tail", b"-f", b"/home/x/.ductor/logs/agent.log"],
            [b"python", b"other_script.py", b"/path/to/ductor"],
            [b"python", b"-c", b"import time", b"-u", b"ductor"],
        ]
        for argv in matches:
            assert _argv_looks_like_ductor(argv) is True, argv
        for argv in non_matches:
            assert _argv_looks_like_ductor(argv) is False, argv


class TestReleaseLock:
    """Test PID lock release."""

    def test_removes_own_pid_file(self, tmp_path: Path) -> None:
        from ductor_bot.infra.pidlock import release_lock

        pid_file = tmp_path / "bot.pid"
        pid_file.write_text(str(os.getpid()), encoding="utf-8")
        release_lock(pid_file=pid_file)
        assert not pid_file.exists()

    def test_does_not_remove_other_pid(self, tmp_path: Path) -> None:
        from ductor_bot.infra.pidlock import release_lock

        pid_file = tmp_path / "bot.pid"
        pid_file.write_text("999999999", encoding="utf-8")
        release_lock(pid_file=pid_file)
        assert pid_file.exists()  # Should NOT be removed

    def test_noop_when_no_file(self, tmp_path: Path) -> None:
        from ductor_bot.infra.pidlock import release_lock

        pid_file = tmp_path / "bot.pid"
        release_lock(pid_file=pid_file)  # No error

    def test_removes_corrupt_pid_file(self, tmp_path: Path) -> None:
        from ductor_bot.infra.pidlock import release_lock

        pid_file = tmp_path / "bot.pid"
        pid_file.write_text("garbage", encoding="utf-8")
        release_lock(pid_file=pid_file)
        assert not pid_file.exists()
