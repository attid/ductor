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
| `fix/mainmemory-injection-cap` | fix | Cap инъекции MAINMEMORY на старте сессии в 256 KiB — иначе жирная память провоцирует дорогой preflight в bili |
| `fix/codex-resume-cli-parameters` | fix | Пробрасывает `cli_parameters` в codex resume-команды (раньше флаги жили только на первом спавне) |
| `fix/pidlock-own-pid-stale` | fix | PID-лок переживает пересоздание контейнера: свой pid и живой не-ductor в bot.pid считаются stale — иначе kill-ветка убивала bili sidecar |
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
  fix/mainmemory-injection-cap
  fix/codex-resume-cli-parameters
  fix/pidlock-own-pid-stale
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

Файл `~/.ductor/billion-context/claude-mcp.json` сеет entrypoint при первом
старте (только если файла нет), его можно править руками. Сид с апстримом
(`claude-bili-settings.json`) сознательно НЕ сеется: образ не должен знать
ни одного действующего провайдера — файл с реальным апстримом создает
деплой под себя.

Включение на конкретного агента — через `cli_parameters` (`config.json` для
main, `agents.json` для сабов); агенты без поля наследуют пустые списки.

Ограничение `cli_parameters` (исправлено в `fix/codex-resume-cli-parameters`,
действует с ближайшей пересборки `deploy`; в текущем образе на проде баг
еще жив): они дописывались только к первому спавну сессии — resume-ходы
флагов не получали. Для codex всё равно надежнее конфиг в
`~/.codex/config.toml` (читается на каждом запуске, не зависит от ductor):

```toml
[model_providers.ZAI]
base_url = "http://127.0.0.1:8787/bili/https://api.z.ai/api/v1"

[mcp_servers.bili]
command = "bili"
args = ["mcp"]
```

и тогда `cli_parameters` у codex в ductor-конфиге надо убрать (`-c` сильнее
toml и замаскирует его). Учти: config.toml глобален на контейнер — через
bili пойдут все codex-агенты, а не только выбранные.

Claude (файл настроек создается руками, апстрим свой):

```sh
printf '%s\n' '{"env":{"ANTHROPIC_BASE_URL":"http://127.0.0.1:8787/bili/<АПСТРИМ>"}}' \
  > ~/.ductor/billion-context/claude-bili-settings.json
```

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

Codex (если без config.toml, помня про resume-дырку выше):

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
Upstream URL оборачивается по схеме `<proxy>/bili/<текущий апстрим агента>`;
в примерах стоит условный Z.ai — подставляется реальный апстрим агента.

## Когда удалять ветку

- Если upstream реализовал то же поведение, ветка удаляется из списка и recipe.
- Если локальное поведение больше не требуется, ветка снимается до следующей
  пересборки `deploy`.
