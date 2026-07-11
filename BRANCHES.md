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
| `fix/codex-prompt-stdin` | fix | Передача больших Codex prompts через stdin вместо argv |
| `fix/task-retention-cleanup` | fix | Автоочистка завершенных background tasks по age/count retention |
| `fix/claude-omit-model-env` | fix | `DUCTOR_CLAUDE_OMIT_MODEL` для запуска Claude CLI без `--model` |
| `local/config-and-bootstrap` | local | Runtime env overrides, rule-sync interval и permissive group auth |
| `local/docker-and-ci` | local | Application Dockerfile, compose, GHCR workflow и Docker target в justfile |
| `local/docs-and-notes` | local | Local rule additions, `PROJECT_MEMORY.md` и auth docs |
| `local/meta` | local | Этот реестр и fork-overlay workflow |

## Retired branches

Следующие изменения больше не входят в `deploy`, потому что их поглотил
upstream: Antigravity provider, Telegram reply context, Gemini auto-model и
bundle discovery, queue filtering. `fix/cron-silent-success` тоже снят:
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
  fix/codex-prompt-stdin
  fix/task-retention-cleanup
  fix/claude-omit-model-env
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

## Когда удалять ветку

- Если upstream реализовал то же поведение, ветка удаляется из списка и recipe.
- Если локальное поведение больше не требуется, ветка снимается до следующей
  пересборки `deploy`.
