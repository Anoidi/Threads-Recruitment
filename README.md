# Threads Recruitment Manager

Панель управления рекрутингом и контентом для Meta Threads: от вакансии и черновика публикации до ответов аудитории и учёта кандидатов.

## Подробные инструкции

- [Полная настройка с нуля](docs/SETUP_RU.md)
- [AI: облако, Ollama и свой шлюз](docs/AI_RU.md)
- [Threads: Meta, разрешения, OAuth и токены](docs/THREADS_RU.md)
- [Telegram: бот и импорт вакансий канала](docs/TELEGRAM_RU.md)
- [Instagram, TikTok, ComfyUI и Hermes](docs/OPTIONAL_RU.md)
- [Работа, автоматизация, backup и ошибки](docs/OPERATIONS_RU.md)
- [Готовое задание вашему агенту](docs/AGENT_PROMPT_RU.md) · [Runbook агента](docs/AGENT_SETUP_RU.md) · [AGENTS.md](AGENTS.md)

## Что делает проект

- Хранит вакансии, условия работы и подтверждение актуальности источников.
- Готовит контент с помощью AI через LiteLLM, проверяет факты и качество черновиков.
- Ведёт календарь публикаций, очередь и резерв готовых постов.
- Поддерживает согласование публикаций и режимы MANUAL, SEMI_AUTO и AUTO с ограничениями и аварийной остановкой.
- Получает ответы и упоминания Threads, выделяет вопросы аудитории и сигналы интереса кандидатов.
- Ведёт воронку кандидатов с подтверждёнными контактами и ручным изменением этапов.
- Показывает аналитику, историю действий и доступные разрешения аккаунта.
- Импортирует новые вакансии из публичного Telegram-канала через бота. Для Instagram/TikTok есть подключение и проверка профиля; полноценный планировщик кросспостинга не реализован. Генерация медиа через ComfyUI требует отдельной установки.

По умолчанию Threads и AI работают в тестовом режиме: реальные публикации и автоматические ответы выключены. Наличие кода интеграции не заменяет токены и разрешения соответствующей платформы.

## Установка на Windows

### 1. Требования

- Git.
- Docker Desktop с WSL2 и Docker Compose v2.
- PowerShell 5.1 или новее.
- Не менее 8 ГБ памяти для Docker (12 ГБ при использовании Hermes).

Python, Node.js, MySQL и Redis отдельно устанавливать для запуска через Docker не требуется.

### 2. Скачать проект

```powershell
git clone https://github.com/Anoidi/Threads-Recruitment.git
cd Threads-Recruitment
Copy-Item .env.example .env
```

### 3. Настроить `.env`

Сгенерируйте отдельное случайное значение для каждого секрета. Команду можно повторять:

```powershell
docker run --rm python:3.12-alpine python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Заполните `MYSQL_PASSWORD`, `MYSQL_ROOT_PASSWORD`, `LITELLM_MASTER_KEY`, `HERMES_API_KEY` и `OPERATOR_TOKEN`. Пароли пользователя MySQL и root должны быть разными. В `DATABASE_URL` укажите пользователя `MYSQL_USER`, его пароль `MYSQL_PASSWORD` и базу `MYSQL_DATABASE`:

```dotenv
DATABASE_URL=mysql+aiomysql://threads_app:YOUR_MYSQL_PASSWORD@mysql:3306/threads_recruitment
```

Для `THREADS_TOKEN_KEY` и `SOCIAL_TOKEN_KEY` используйте отдельные Fernet-ключи, особенно перед подключением реальных аккаунтов:

```powershell
docker run --rm python:3.12-alpine sh -c "pip install --quiet cryptography && python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
```

Не меняйте ключи шифрования после сохранения токенов без процедуры миграции. Не добавляйте `.env` в GitHub.

Для первого запуска оставьте:

```dotenv
THREADS_MOCK_MODE=true
AI_MOCK_MODE=true
LITELLM_CONFIG_FILE=/app/config.mock.yaml
AUTO_POST=false
AUTO_REPLY=false
AUTO_EXTERNAL_COMMENT=false
```

### 4. Проверить и запустить

```powershell
.\scripts\validate-infrastructure.ps1
docker compose up -d --build
docker compose ps
```

Первый запуск скачивает образы и собирает приложение. Миграции базы выполняются при старте backend.

- Интерфейс: <http://localhost:3000>
- Документация API: <http://localhost:8000/docs>
- Состояние сервисов: <http://localhost:8000/api/health>
- Adminer: <http://localhost:8081> (сервер `mysql`, данные входа из `.env`).

Если интерфейс запросит токен оператора, используйте `OPERATOR_TOKEN` из `.env`. API защищён этим токеном.

### 5. Подключить реальные сервисы

Для Threads настройте приложение Meta, `THREADS_APP_ID`, `THREADS_APP_SECRET` и redirect URI, подключите аккаунт через интерфейс и проверьте разрешения. Перед переходом с mock на реальные аккаунты используйте отдельную базу или отдельный Compose project, чтобы не смешивать тестовые и реальные данные.

Для настоящего AI настройте модели и провайдера в LiteLLM, смените `LITELLM_CONFIG_FILE` на `/app/config.yaml` и отключите `AI_MOCK_MODE`. Проверьте настройки шлюза в `.env.example`: доступность локальных моделей и внешних провайдеров зависит от вашего компьютера и ключей.

Автоматизацию включайте после проверки вакансий, черновиков и политики автономности. Подробности интеграций и архитектуры: [README.en.md](README.en.md), [docs](docs), [Hermes](hermes/README.md).

## Управление

```powershell
# Логи
docker compose logs --tail 100 backend worker frontend

# Остановка с сохранением данных
docker compose down

# Повторный запуск
docker compose up -d
```

Данные хранятся в Docker volumes. Команда `docker compose down -v` удаляет их.

## Устройство проекта

| Каталог | Назначение |
| --- | --- |
| `frontend/` | Интерфейс React + TypeScript + Vite |
| `backend/` | FastAPI, модели SQLAlchemy и миграции Alembic |
| `worker/` | Планирование, публикация и фоновые задачи |
| `litellm/` | Конфигурация шлюза моделей |
| `hermes/` | Необязательный агентный сервис |
| `tests/` | Проверки backend и инфраструктуры |
| `scripts/` | Диагностика и обслуживание |

Основные хранилища — MySQL и Redis. Полная техническая документация на английском сохранена в [README.en.md](README.en.md).
