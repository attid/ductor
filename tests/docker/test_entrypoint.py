from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parents[2]
ENTRYPOINT = REPO_ROOT / "docker-entrypoint.sh"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


@pytest.fixture
def entrypoint_env(tmp_path: Path) -> tuple[dict[str, str], Path, Path]:
    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    probe_dir = tmp_path / "probe"
    home.mkdir()
    fake_bin.mkdir()
    probe_dir.mkdir()

    _write_executable(
        fake_bin / "dbus-daemon",
        '#!/bin/sh\nprintf "%s\\n" "$*" >"$PROBE_DIR/dbus.args"\n',
    )
    _write_executable(
        fake_bin / "gnome-keyring-daemon",
        '#!/bin/sh\ncat >"$PROBE_DIR/keyring.password"\n',
    )

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "PROBE_DIR": str(probe_dir),
            "XDG_RUNTIME_DIR": str(tmp_path / "runtime"),
        }
    )
    env.pop("DUCTOR_KEYRING_PASSWORD", None)
    return env, home, probe_dir


def _run_entrypoint(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            str(ENTRYPOINT),
            "/bin/sh",
            "-c",
            'printf "%s" "$DBUS_SESSION_BUS_ADDRESS" >"$PROBE_DIR/command.bus"',
        ],
        check=False,
        capture_output=True,
        env=env,
        text=True,
    )


def test_entrypoint_is_present() -> None:
    assert ENTRYPOINT.is_file()


def test_generated_keyring_password_is_private_and_reused(
    entrypoint_env: tuple[dict[str, str], Path, Path],
) -> None:
    env, home, probe_dir = entrypoint_env

    first = _run_entrypoint(env)

    assert first.returncode == 0, first.stderr
    password_file = home / ".local" / "share" / "keyrings" / ".ductor-password"
    first_password = password_file.read_text(encoding="utf-8")
    assert len(first_password) == 64
    assert stat.S_IMODE(password_file.stat().st_mode) == 0o600
    assert (probe_dir / "keyring.password").read_text(encoding="utf-8") == first_password

    second = _run_entrypoint(env)

    assert second.returncode == 0, second.stderr
    assert password_file.read_text(encoding="utf-8") == first_password
    assert (probe_dir / "keyring.password").read_text(encoding="utf-8") == first_password


def test_entrypoint_starts_private_bus_and_exports_address(
    entrypoint_env: tuple[dict[str, str], Path, Path],
) -> None:
    env, _, probe_dir = entrypoint_env

    result = _run_entrypoint(env)

    assert result.returncode == 0, result.stderr
    bus_address = f"unix:path={env['XDG_RUNTIME_DIR']}/bus"
    assert (probe_dir / "command.bus").read_text(encoding="utf-8") == bus_address
    assert (probe_dir / "dbus.args").read_text(encoding="utf-8").strip() == (
        f"--session --fork --address={bus_address}"
    )
    assert stat.S_IMODE(Path(env["XDG_RUNTIME_DIR"]).stat().st_mode) == 0o700


def test_explicit_keyring_password_does_not_create_password_file(
    entrypoint_env: tuple[dict[str, str], Path, Path],
) -> None:
    env, home, probe_dir = entrypoint_env
    env["DUCTOR_KEYRING_PASSWORD"] = "configured-password"

    result = _run_entrypoint(env)

    assert result.returncode == 0, result.stderr
    assert (probe_dir / "keyring.password").read_text(encoding="utf-8") == ("configured-password")
    assert not (home / ".local" / "share" / "keyrings" / ".ductor-password").exists()


def test_dockerfile_installs_antigravity_and_keyring_runtime() -> None:
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "dbus-daemon" in dockerfile
    assert "gnome-keyring" in dockerfile
    assert "https://antigravity.google/cli/install.sh" in dockerfile
    assert "--dir /usr/local/bin" in dockerfile
    assert "AGY_CLI_DISABLE_AUTO_UPDATE=true" in dockerfile
    assert 'ENTRYPOINT ["/usr/bin/tini", "-g", "--", "/usr/local/bin/docker-entrypoint"]' in (
        dockerfile
    )


def test_dockerfile_installs_billion_context() -> None:
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")

    npm_line = (
        "npm install -g @openai/codex @anthropic-ai/claude-code @google/gemini-cli billion-context"
    )
    assert npm_line in dockerfile
    assert "ACP_AUTO_UPDATE=0" in dockerfile


def _fake_bili_tools(env: dict[str, str]) -> None:
    fake_bin = Path(env["PATH"].split(":")[0])
    _write_executable(
        fake_bin / "bili",
        '#!/bin/sh\ntouch "$PROBE_DIR/bili.ran"\nkill -TERM "$PPID"\n',
    )
    _write_executable(
        fake_bin / "curl",
        '#!/bin/sh\nprintf "%s\\n" "$*" >"$PROBE_DIR/curl.args"\n',
    )


def test_entrypoint_starts_bili_and_seeds_agent_configs(
    entrypoint_env: tuple[dict[str, str], Path, Path],
) -> None:
    env, home, probe_dir = entrypoint_env
    _fake_bili_tools(env)

    result = _run_entrypoint(env)

    assert result.returncode == 0, result.stderr
    assert (probe_dir / "bili.ran").exists()
    curl_args = (probe_dir / "curl.args").read_text(encoding="utf-8")
    assert "http://127.0.0.1:8787/__bili/health" in curl_args
    bili_state = home / ".ductor" / "billion-context"
    assert (bili_state / "claude-mcp.json").read_text(encoding="utf-8") == (
        '{"mcpServers":{"bili":{"command":"bili","args":["mcp"]}}}\n'
    )
    settings = json.loads((bili_state / "claude-bili-settings.json").read_text(encoding="utf-8"))
    assert settings["env"]["ANTHROPIC_BASE_URL"] == (
        "http://127.0.0.1:8787/bili/https://api.z.ai/api/anthropic"
    )


def test_bili_seed_does_not_overwrite_existing_configs(
    entrypoint_env: tuple[dict[str, str], Path, Path],
) -> None:
    env, home, _probe_dir = entrypoint_env
    _fake_bili_tools(env)
    bili_state = home / ".ductor" / "billion-context"
    bili_state.mkdir(parents=True)
    (bili_state / "claude-mcp.json").write_text('{"mcpServers":{}}', encoding="utf-8")

    result = _run_entrypoint(env)

    assert result.returncode == 0, result.stderr
    assert (bili_state / "claude-mcp.json").read_text(encoding="utf-8") == '{"mcpServers":{}}'


def test_bili_disabled_by_env(
    entrypoint_env: tuple[dict[str, str], Path, Path],
) -> None:
    env, home, probe_dir = entrypoint_env
    fake_bin = Path(env["PATH"].split(":")[0])
    _write_executable(fake_bin / "bili", '#!/bin/sh\ntouch "$PROBE_DIR/bili.ran"\n')
    env["BILI_ENABLED"] = "0"

    result = _run_entrypoint(env)

    assert result.returncode == 0, result.stderr
    assert not (probe_dir / "bili.ran").exists()
    assert not (home / ".ductor" / "billion-context" / "claude-mcp.json").exists()


@pytest.mark.parametrize("compose_name", ["docker-compose.yml", "docker-compose.example.yml"])
def test_compose_persists_antigravity_keyring(compose_name: str) -> None:
    compose = (REPO_ROOT / compose_name).read_text(encoding="utf-8")

    assert "antigravity_keyring:/home/node/.local/share/keyrings" in compose
    assert "antigravity_keyring:" in compose
