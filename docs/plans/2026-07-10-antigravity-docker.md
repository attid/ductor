# Antigravity Docker Support Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Ship the official `agy` CLI in the application image with a persistent Linux Secret Service keyring that survives container recreation.

**Architecture:** Install the official checksum-verifying Antigravity bootstrap into `/usr/local/bin`. A small container entrypoint starts a per-container D-Bus session, unlocks GNOME Keyring with either `DUCTOR_KEYRING_PASSWORD` or a generated password persisted beside the keyring, then executes Ductor. Compose persists both Antigravity state under `~/.gemini` and keyring data under `~/.local/share/keyrings`.

**Tech Stack:** Docker, POSIX shell, D-Bus, GNOME Keyring, pytest.

---

### Task 1: Specify entrypoint behavior

**Files:**
- Create: `tests/docker/test_entrypoint.py`
- Create: `docker-entrypoint.sh`

1. Add tests that run the entrypoint with fake D-Bus/keyring commands.
2. Assert that a generated password is mode `0600`, reused on restart, and passed to keyring stdin.
3. Assert that `DUCTOR_KEYRING_PASSWORD` overrides generation.
4. Run `uv run pytest tests/docker/test_entrypoint.py -q` and confirm failure because the entrypoint is absent.
5. Implement the minimal POSIX entrypoint and rerun the tests to green.

### Task 2: Add Antigravity and runtime dependencies

**Files:**
- Modify: `Dockerfile`
- Modify: `.dockerignore`

1. Install `dbus-daemon` and `gnome-keyring` without recommended packages.
2. Download the official Antigravity installer and install `agy` to `/usr/local/bin`.
3. Disable in-container `agy` self-updates and copy the tested entrypoint.
4. Run Dockerfile-focused tests and formatting/lint checks.

### Task 3: Persist and document credentials

**Files:**
- Modify: `docker-compose.yml`
- Modify: `docker-compose.example.yml`
- Modify: `README.SERVER.md`

1. Add `antigravity_keyring:/home/node/.local/share/keyrings` to both compose files.
2. Document first-time OAuth through `docker compose exec -it ductor agy`.
3. Document the optional stable `DUCTOR_KEYRING_PASSWORD=change_me` override without side files.

### Task 4: Verify and integrate

1. Run Python tests, Ruff, mypy, and Docker build.
2. Verify `agy --version`, keyring persistence across two containers, and unauthenticated OAuth output.
3. Commit and push `local/docker-and-ci`.
4. Rebuild `deploy` from `main` plus all active overlays, run the required full verification, and push with force-with-lease.
