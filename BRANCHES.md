# Active personal branches

Схема — см. `docs/fork-overlay-workflow.md`. `main` строго зеркалит
`upstream/main` (`PleasePrompto/ductor`). Runtime-сборка `deploy` всегда
пересоздается из `main` и перечисленных ниже веток; вручную в `deploy` не
коммитим.

| Branch | Type | Purpose |
|---|---|---|
| `feat/bot-conversation-hop-guard` | feat | Бот→бот через настоящий Telegram reply и hop-counter защита от петель |
| `fix/config-reload-mtime-ns` | fix | Digest-based детектирование быстрых перезаписей `config.json` |
| `fix/gemini-custom-model-validation` | fix | Разрешает custom/stale `gemini-*` для cron и убирает traceback у config errors |
| `fix/claude-omit-model-env` | fix | `DUCTOR_CLAUDE_OMIT_MODEL` для запуска Claude CLI без `--model` |
| `fix/codex-invalid-previous-response` | fix | Пересоздает Codex-сессию при `Invalid previous_response_id` |
| `local/config-and-bootstrap` | local | Runtime env overrides, rule-sync interval и permissive group auth |
| `local/docker-and-ci` | local | Application Dockerfile с API extra, compose, GHCR workflow, Docker target в justfile и billion-context прокси |
| `local/docs-and-notes` | local | Local rule additions, `PROJECT_MEMORY.md` и auth docs |
| `local/meta` | local | Этот реестр и fork-overlay workflow |

## Retired branches

Следующие изменения больше не входят в `deploy`, потому что их поглотил
upstream: Antigravity provider, Telegram reply context, Gemini auto-model и
bundle discovery, queue filtering, Codex prompt через stdin и retention
завершенных background tasks. `fix/cron-silent-success` тоже снят:
нужные cron jobs используют upstream-поле `silent_on_success=true` вместо
магических ответов `OK`/`done`.

## Recipe для пересборки `deploy`

```bash
git fetch upstream
git checkout main
git merge --ff-only upstream/main

branches=(
  feat/bot-conversation-hop-guard
  fix/config-reload-mtime-ns
  fix/gemini-custom-model-validation
  fix/claude-omit-model-env
  fix/codex-invalid-previous-response
  local/config-and-bootstrap
  local/docker-and-ci
  local/docs-and-notes
  local/meta
)

for branch in "${branches[@]}"; do
  git checkout "$branch"
  git rebase main || break
done

git checkout deploy
git reset --hard main
for branch in "${branches[@]}"; do
  git merge --no-ff "$branch" -m "deploy: include $branch"
done
```

## Local policies

- При `group_mention_only=true` любой участник разрешенной Telegram-группы
  может обратиться к боту через mention/reply; `allowed_user_ids` для таких
  сообщений намеренно не применяется.
- `DUCTOR_CLAUDE_OMIT_MODEL=1` остается поддерживаемым runtime override.
- `.github/workflows/publish.yml` всегда сохраняется из upstream; локальная
  Docker-ветка только добавляет GHCR workflow для `deploy`.
- Перед force-push `deploy` обязательны full tests, lint/type checks и Docker build.

## billion-context (bili)

`bili` ставится в образ (`local/docker-and-ci`) и поднимается entrypoint'ом
как локальный прокси на `127.0.0.1:8787`. Самообновлятор заглушен
`ACP_AUTO_UPDATE=0` (read-only npm-глобал). Выключатель без редеплоя —
`BILI_ENABLED=0` в environment сервиса. Состояние прокси живет в `$HOME`
(на проде — bind `/home/node`), лог — `~/.local/state/billion-context/bili.log`.

Файлы `~/.ductor/billion-context/claude-mcp.json` и
`claude-bili-settings.json` сеет entrypoint при первом старте (только если
файла нет), их можно править руками.

Включение на конкретного агента — через `cli_parameters` (`config.json` для
main, `agents.json` для сабов); агенты без поля наследуют пустые списки.

Claude:

```json
"cli_parameters": {
  "claude": [
    "--settings", "/home/node/.ductor/billion-context/claude-bili-settings.json",
    "--mcp-config", "/home/node/.ductor/billion-context/claude-mcp.json"
  ]
}
```

`--settings` — CLI-tier настроек claude, перебивает env из общих
`~/.claude/settings.json`; auth-токен остается в общих настройках и мержится.

Codex:

```json
"cli_parameters": {
  "codex": [
    "-c", "model_providers.ZAI.base_url=\"http://127.0.0.1:8787/bili/https://api.z.ai/api/v1\"",
    "-c", "mcp_servers.bili.command=\"bili\"",
    "-c", "mcp_servers.bili.args=[\"mcp\"]"
  ]
}
```

`-c` имеет высший приоритет в codex, `~/.codex/config.toml` не меняется.
Upstream URL оборачивается по схеме `<proxy>/bili/<текущий апстрим агента>` —
в снипетах Z.ai (`api.z.ai/api/anthropic` для claude, `api.z.ai/api/v1` для
codex); у агента с другим апстримом подставляется его URL.

## Когда удалять ветку

- Если upstream реализовал то же поведение, ветка удаляется из списка и recipe.
- Если локальное поведение больше не требуется, ветка снимается до следующей
  пересборки `deploy`.
