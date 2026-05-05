# Active personal branches

Схема — см. `docs/fork-overlay-workflow.md`. `main` = строгое зеркало `upstream/main` (PleasePrompto/ductor). Всё личное живёт на отдельных ветках от `main`. Запуск собирается из `deploy` = `main` + ветки ниже через `git merge --no-ff`.

| Branch | Type | Purpose | Upstream PR |
|---|---|---|---|
| `feat/telegram-reply-context` | feat | Прокидывает контекст реплая в промпт агенту | — |
| `feat/bot-conversation-hop-guard` | feat | Бот→бот через TG-reply + hop-counter защита от петель | — |
| `feat/gemini-auto-model` | feat | Кнопки `auto/pro/flash/flash-lite` в `/model` для Gemini | — |
| `fix/telegram-queue-indicator-unaddressed` | fix | Не показывать "queue indicator" для неадресованных сообщений в группе | [#123](https://github.com/PleasePrompto/ductor/pull/123) |
| `fix/cron-silent-success` | fix | Подавлять silent-success результаты cron | — |
| `local/docker-and-ci` | local | Dockerfile, docker-compose, justfile, README.SERVER, GHCR workflow, uv.lock | n/a |
| `local/docs-and-notes` | local | Локальные правки AGENTS.md/CLAUDE.md/GEMINI.md, PROJECT_MEMORY.md, docs/modules/bot.md | n/a |
| `local/config-and-bootstrap` | local | __main__, config, install, orchestrator, telegram middleware/app overlays | n/a |
| `local/meta` | local | Этот файл и `docs/fork-overlay-workflow.md` | n/a |

## Recipe для пересборки `deploy`

```bash
git fetch upstream
git checkout main && git merge --ff-only upstream/main
for b in feat/telegram-reply-context feat/bot-conversation-hop-guard \
         feat/gemini-auto-model \
         fix/telegram-queue-indicator-unaddressed fix/cron-silent-success \
         local/docker-and-ci local/docs-and-notes \
         local/config-and-bootstrap local/meta; do
    git checkout "$b" && git rebase main || break
done
git checkout deploy && git reset --hard main
for b in feat/telegram-reply-context feat/bot-conversation-hop-guard \
         feat/gemini-auto-model \
         fix/telegram-queue-indicator-unaddressed fix/cron-silent-success \
         local/docker-and-ci local/docs-and-notes \
         local/config-and-bootstrap local/meta; do
    git merge --no-ff "$b" -m "deploy: include $b"
done
```

## Когда удалять ветку

- Апстрим вмержил твой PR → удалить ветку, на следующем `merge --ff-only` фикс приедет в `main` сам, и его надо убрать из списка выше + из recipe.
- Изменение перестало быть нужным → удалить ветку и убрать из recipe.

## Примечания

- `local/config-and-bootstrap` содержит squashed-overlay; его middleware/app перекрываются с feat/fix-ветками telegram. На `git merge` дубликаты схлопываются автоматически благодаря 3-way merge (rerere ускоряет повторы).
- `local/meta` ребейзится на новый `main` без конфликтов — апстрим этих файлов не касается.
