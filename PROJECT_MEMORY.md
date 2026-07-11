# Project Memory

Last updated: 2026-07-10

## Current State

- `main` and `fork/main` mirror `upstream/main` at `626d90b` (post-v0.18.2, package 0.18.3).
- Upstream now includes Antigravity, Telegram reply context, Gemini model discovery,
  queue filtering, and explicit cron `silent_on_success` support.
- Personal behavior is maintained as small overlay branches and assembled into `deploy`.
- Pre-update branch tips are preserved under `refs/backups/20260710-pre-v018/`.

## Active Overlay

- `feat/bot-conversation-hop-guard`: true Telegram replies plus bot-to-bot hop limiting.
- `fix/config-reload-mtime-ns`: digest-based config reload detection.
- `fix/gemini-custom-model-validation`: treat discovered Gemini models as UI hints rather
  than an allowlist for `gemini-*` task models; log cron config errors without tracebacks.
- `fix/codex-prompt-stdin`: pass large Codex prompts through stdin.
- `fix/task-retention-cleanup`: automatic age/count retention for finished tasks.
- `fix/claude-omit-model-env`: `DUCTOR_CLAUDE_OMIT_MODEL` support.
- `local/config-and-bootstrap`: runtime env overrides and permissive group auth when
  `group_mention_only=true`.
- `local/docker-and-ci`: application image, provider CLIs, persistent Antigravity keyring,
  compose files, and GHCR build workflow.
- `local/docs-and-notes`: local rule additions and this memory file.
- `local/meta`: overlay registry and rebuild instructions.

## Retired Overlay

- Antigravity provider, Telegram reply context, Gemini auto-model/discovery, queue filtering,
  and cron silent-success code patches are no longer merged because upstream supersedes them.
- Cron jobs that should stay quiet must use upstream `silent_on_success=true`.

## Docker Notes

- The application image installs ductor, current npm releases of Codex, Claude Code and
  Gemini CLI, plus the official Antigravity CLI (`agy`) at build time.
- The container entrypoint starts D-Bus and GNOME Keyring. Antigravity OAuth survives
  recreation through the `antigravity_keyring` volume; `~/.gemini` remains its settings
  and conversation-state volume.
- Runtime uses the `node` user, `/opt/venv`, and `/home/node` paths.
- Compose files use named volumes for ductor state, provider auth, and project data.
- GHCR workflow builds `ghcr.io/attid/ductor:latest` on pushes to `deploy` and supports
  manual dispatch once the workflow is visible on the repository default branch.

## Verification

- Run `uv run pytest` for the full suite.
- Run `uv run ruff check .`, `uv run ruff format --check .`, and `uv run mypy ductor_bot`.
- Run `docker build -t ductor:latest .` after rebuilding `deploy`.
