# Ductor: запуск на сервере (Docker)

Этот гайд для быстрого запуска `ductor` на сервере через Docker с сохранением данных и авторизаций.

## 1. Подготовка

```bash
cd /path/to/ductor
docker pull ghcr.io/attid/ductor:latest

```

## 2. Первичная настройка (onboarding)

```bash
docker compose run --rm ductor ductor onboarding
```

Onboarding создаст и заполнит конфигурацию в volume `ductor_data`.

## 3. Авторизация Antigravity

Образ уже содержит `agy`, D-Bus и постоянный keyring. Для первого входа запусти:

```bash
docker compose run --rm ductor agy
```

Открой напечатанную OAuth-ссылку, заверши вход и вставь полученный код в терминал.
После входа закрой TUI через `Ctrl+D`. Авторизация сохранится в volume
`antigravity_keyring`, а настройки и история — в `gemini_auth`.

По умолчанию пароль keyring генерируется автоматически и хранится внутри его volume.
Чтобы задать стабильный пароль самостоятельно, добавь переменную до первого входа:

```yaml
environment:
  DUCTOR_KEYRING_PASSWORD: change_me
```

## 4. Запуск через Compose

В репозитории уже есть готовый `docker-compose.yml` на image
`ghcr.io/attid/ductor:latest`.

```bash
docker compose up -d
docker compose logs -f
```

## 5. Обновление

```bash
docker pull ghcr.io/attid/ductor:latest
docker compose up -d
```

## 6. Остановка

```bash
docker compose down
```

## 7. Что сохраняется (volumes)

- `ductor_data` — конфиг, сессии, cron/webhooks, память и логи
- `codex_auth` — авторизация Codex CLI
- `claude_auth` — авторизация Claude CLI
- `gemini_auth` — авторизация Gemini, настройки и история Antigravity
- `antigravity_keyring` — OAuth-токены Antigravity и пароль локального keyring
- `ductor_projects` — рабочие проекты агента

## 8. Сброс авторизации Antigravity

Удаление keyring требует повторного OAuth-входа:

```bash
docker compose down
docker volume rm ductor_antigravity_keyring
```

## 9. Webhook (опционально)

Для минимального запуска webhook не нужен.
Если понадобится — открой `ports` в `docker-compose.yml` и включи `webhooks` в `config.json`.
