# Project Memory

Last updated: 2026-02-19

## Current State

- Docker support files were added in repo root:
  - `Dockerfile`
  - `docker-compose.example.yml`
  - `.dockerignore`
- `justfile` has `build` target: `just build` runs `docker build -t ductor:latest .`.

## Docker Notes

- `Dockerfile` installs:
  - Python app `ductor` into `/opt/venv`
  - `@openai/codex` (CLI command: `codex`)
  - `@anthropic-ai/claude-code` (CLI command: `claude`)
- Runtime is standardized on `node` user and `/home/node` paths.
  - `DUCTOR_HOME=/home/node/.ductor`
  - `CODEX_HOME=/home/node/.codex`
- Fixed build blockers:
  - PEP 668 (`externally-managed-environment`) resolved via `python3 -m venv /opt/venv` and pip install inside venv.
  - Removed custom `ductor` runtime user to avoid `node` vs `ductor` path confusion.
- Verified on 2026-02-19:
  - `just build` succeeds.
  - `docker run --rm ductor:latest ductor --help` succeeds.
  - `docker run --rm ductor:latest sh -lc 'codex --version && claude --version'` succeeds.

## Runtime Volumes (from compose example)

- `./.ductor:/home/node/.ductor`
- `${HOME}/.codex:/home/node/.codex`
- `${HOME}/.claude:/home/node/.claude`
- `./projects:/home/node/.ductor/workspace/projects`

## Next-Time Quick Start

1. `just build`
2. `cp docker-compose.example.yml docker-compose.yml`
3. `mkdir -p docker-data/ductor projects`
4. `docker compose up -d`
