# Ductor: запуск на сервере (Docker)

Этот гайд для быстрого запуска `ductor` на сервере через Docker с сохранением данных и авторизаций.

## 1. Подготовка

```bash
cd /path/to/ductor
docker pull ghcr.io/montelibero/ductor:latest

mkdir -p .ductor projects
mkdir -p "$HOME/.codex" "$HOME/.claude"
```

## 2. Первичная настройка (onboarding)

```bash
docker run --rm -it \
  -e DUCTOR_HOME=/home/node/.ductor \
  -e CODEX_HOME=/home/node/.codex \
  -e TZ=UTC \
  -v "$(pwd)/.ductor:/home/node/.ductor" \
  -v "$HOME/.codex:/home/node/.codex" \
  -v "$HOME/.claude:/home/node/.claude" \
  -v "$(pwd)/projects:/home/node/.ductor/workspace/projects" \
  ghcr.io/montelibero/ductor:latest \
  ductor onboarding
```

Onboarding создаст и заполнит `./.ductor/config/config.json`.

## 3. Запуск через Compose

В репозитории уже есть готовый `docker-compose.yml` (на image `ghcr.io/montelibero/ductor:latest`).

```bash
docker compose up -d
docker compose logs -f
```

## 4. Обновление

```bash
docker pull ghcr.io/montelibero/ductor:latest
docker compose up -d
```

## 5. Остановка

```bash
docker compose down
```

## 6. Что сохраняется (volumes)

- `./.ductor` — конфиг, сессии, cron/webhooks, память, логи
- `$HOME/.codex` — авторизация Codex CLI
- `$HOME/.claude` — авторизация Claude CLI
- `./projects` — рабочие проекты агента (доступны в `~/.ductor/workspace/projects`)

## 7. Частая проблема: Permission denied на `.ductor/config`

Если видишь ошибку записи в `/home/ductor/.ductor/...`:

```bash
sudo chown -R "$(id -u):$(id -g)" .ductor
chmod -R u+rwX .ductor
```

## 8. Webhook (опционально)

Для минимального запуска webhook не нужен.  
Если понадобится — открой `ports` в `docker-compose.yml` и включи `webhooks` в `config.json`.
