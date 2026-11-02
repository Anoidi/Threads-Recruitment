# IMPLEMENTATION LOG — improvements/audit-2026-10

Внедрение улучшений по `PROJECT_FULL_AUDIT.md` (разделы 15–16). Коммуникация с владельцем — на русском. Правила: mock-режим, никаких реальных публикаций и платных вызовов, не трогать `.env`, миграции только в тестах.

---

## Фаза 0 — Гигиена

### 0.1 Единый источник правды по лимитам публикаций — ✅ ВЫПОЛНЕНО (коммит `668ed97`)

**Подтверждение исходной проблемы (было в реальном коде, включая расхождения сверх аудита):**

| Место (до изменений) | Значение |
|---|---|
| `docker-compose.yml:28` | `MAX_POSTS_PER_HOUR: ${MAX_POSTS_PER_HOUR:-3}` — дефолт **3** |
| `.env.example:39` | `MAX_POSTS_PER_HOUR=3` — ещё один источник, **3** |
| `backend/app/config.py:96-97` | `max_posts_per_hour: int = 4`, `max_replies_per_hour: int = 12` — **4 / 12** |
| `backend/app/autonomy.py:62-66` `DEFAULT_LIMITS` | `max_publish_per_hour: 4` (другое имя ключа; это отдельный регулятор политики, вопрос «сужен» с владельцем не требовал — значение уже совпадает) |
| `worker/main.py:292-293` (`publish_post`) | фактический гейт: `effective_limit = min(settings.max_posts_per_hour, AppSetting("max_posts_per_hour"))` |

Аудит неточен в мелочи: он считал источники compose/config/autonomy, но четвёртый источник — `.env.example` — тоже расходился. Ключ политики именуется `max_publish_per_hour` (не `max_posts_per_hour`) — оставлен как есть, это отдельная таблица-регулятор внутри `Policy.limits`.

**Изменения:**
- `docker-compose.yml`: дефолты `:-4` и `:-12`, добавлен комментарий о приоритетах.
- `.env.example`: `MAX_POSTS_PER_HOUR=4`, `MAX_REPLIES_PER_HOUR=12` + комментарий.
- `backend/app/config.py`: комментарий — дефолты здесь; `AppSetting`/`.env` могут только понизить эффективное значение (это уже реализовано `min()` в воркере).
- Тест `tests/backend/test_worker_and_community.py::test_hourly_publish_limit_is_min_of_env_and_setting`: три сценария — настройка в БД (2) ниже env (5) → блок; env (1) ниже настройки (2) → блок; env (3) выше обеих → блок по настройке. Ни один источник не может молча поднять лимит.

**Принятые решения:** единое значение — **4/12** (как в коде; причин для 3 не найдено). Явное значение оператора в `.env` или `AppSetting` по-прежнему работает через `min()` — это сознательно оставленная возможность понизить лимит для конкретного дня.

**Тесты:** `docker compose --profile test run --rm backend-test` → **375 passed, 0 failed** (374 прежних + 1 новый). Примечание: два первых «падения» нового теста были вызваны устаревшим тестовым образом (код запечён, надо `docker compose build backend-test` после правок); после пересборки — зелёный.

**Что вручную:** ничего. Если в рабочем `.env` оператор захочет старое поведение (3/10) — это его явное значение, оно продолжит работать.

---

### 0.2 Противоречия в документации — ✅ ВЫПОЛНЕНО (коммит `6eeb961`)

**Подтверждение исходной проблемы:**
- `README.md` (строка ~186): «the `auto_post` setting is enabled for this local project» противоречил `README.md:8-10` («AUTO_POST … disabled») и реальному дефолту кода `backend/app/config.py:93` (`auto_post: bool = False`), `.env.example` (`AUTO_POST=false`).
- `docs/threads-manager-architecture.md`: строка 5 «greenfield … Redis job coordination», строка 15 «Redis-backed background execution», строка 33 «Redis locks», строки 44 и 50-51 (диаграмма «Redis queue/locks», «consumes Redis jobs»), строки 148/150 «lightweight Redis-compatible queue», «Redis locks prevent concurrent processing», строка 175 снова «greenfield». Реальность: очередь = таблица `BackgroundJob` в MySQL (`queue.py`), взятие задач `SELECT … FOR UPDATE SKIP LOCKED` (`worker/main.py: claim_job`), Redis — только heartbeat/wakeup.

**Изменения:** только текст.
- `README.md`: блок «Automatic publishing» переписан по коду — `auto_post=False` по умолчанию; автоматическую публикацию разрешает только политика автономности (`AUTO` = `auto_after_qa`, `SEMI_AUTO` = после явного одобрения), в `MANUAL` пост остаётся черновиком; mock-режим дополнительно блокирует всё.
- `docs/threads-manager-architecture.md`: «greenfield» убран (в 2 местах), «Redis queue/locks» заменены на реальную механику (BackgroundJob MySQL, SKIP LOCKED, Redis = heartbeat).

**Тесты:** изменения только в `.md`; поведение кода не тронуто. Полный набор при последнем прогоне: 375 passed (см. 0.1). Фронтенд не тронут.

**Что вручную:** ничего.

---

### 0.3 Токен Telegram — ⚠️ ДЕЙСТВИЕ ВЛАДЕЛЬЦА (код не трогаю)

Попыток проверки токена не было. **Напоминание:** токен Telegram-бота был раскрыт при локальной настройке и должен быть перевыпущен через @BotFather (команда `/revoke` у бота), после чего новое значение нужно вручную внести в локальный `.env` (поле `TELEGRAM_BOT_TOKEN`). Это делаете вы — если это уже сделано, пункт закрыт; подтверждения в репозитории нет (`.env` в игноре, и это правильно).

README уже содержит предупреждение (README.md:203-204, секция «Do not expose the local API…») — оставлено как есть.

---

## Фаза 1 — Безопасность и целостность

### 1.1 Авторизация API по операторскому токену — ✅ ВЫПОЛНЕНО (коммит `f300bad`)

**Подтверждение исходной проблемы:**
- `require_local_operator` (`security.py:19`) принимал любой приватный адрес, включая Docker-пиры `172.x.x.x` — из локальной сети эта защита не защищала; `POST /api/seed` (api) вообще не был защищён.
- У API не было ни одного общего механизма аутентификации (98 маршрутов открыты).

**Реализация:**
- `require_operator` (`backend/app/security.py`) подключён зависимостью ко всему роутеру `/api` — **fail-closed**: новые маршруты защищены автоматически. Исключения (пути, не метод): `/api/health`, `/api/health/live`, `/api/health/ready`, `/api/threads/webhook` (GET-челлендж + POST), `/api/threads/oauth/callback`, `/api/social/oauth/*\/callback` (браузерный редирект Meta не может нести заголовок).
- Сравнение токена — `hmac.compare_digest` (обе стороны в UTF-8), токен нигде не логируется.
- Без сконфигурированного `OPERATOR_TOKEN`: `development` — доступ с разово-предупреждающим логом `operator_token_missing`; прочие env — 503 без деталей маршрута.
- `POST /api/seed` теперь разрешён только при `threads_mock_mode` (403 иначе) — демо-вакансии не попадут в живую БД.
- Вебхук без `THREADS_APP_SECRET` принимается только в mock-режиме (403 иначе).
- CORS: добавлен `X-Operator-Token` в allow_headers; `docker-compose.yml` передаёт `OPERATOR_TOKEN`; `.env.example` с инструкцией по генерации.
- `require_local_operator` сохранён как второй слой там, где был.
- Фронтенд: `api/auth.ts` (sessionStorage + событие), `client.ts` добавляет заголовок ко всем запросам и очищает токен при 401, компонент `OperatorGate` (форма «Вход в панель» на русском) обёрнут вокруг всех страниц в `App.tsx`. Mock-режим гейт не задевает, токен не попадает в `VITE_*`/сборку.

**Тесты:** backend **388 passed** (+13: 401 без/с неверным токеном, доступ с токеном, открытые исключения, неизвестная соцсеть-колбэк, seed в mock/live, вебхук live без секрета, отсутствие токена в dev/прод). Frontend **24 passed** (+4: storage и гейт). Существующие тесты идут с токеном через общую фикстуру `conftest`.

**Действие владельца (вручную задать в `.env`):** `OPERATOR_TOKEN` (генерация: `python -c "import secrets; print(secrets.token_urlsafe(32))"`), затем `docker compose up -d backend` для подхвата. До этого локальная разработка продолжает работать с предупреждением в логе.

---

### 1.2 Отдельный ключ шифрования токена и ротация — ✅ ВЫПОЛНЕНО (коммит `ad90646`)

**Подтверждение исходной проблемы:** `_fernet()` (`threads_client.py:94-100`) и `_token_fernet()` (`social_clients.py:53-57`) производили ключ из `THREADS_TOKEN_KEY` → `LITELLM_MASTER_KEY` (мастер-ключ шлюза LLM), причём тот же `decrypt_token` обслуживал и OAuth-state — один секрет на три роли.

**Реализация:**
- Разделены `_read_fernet` / `_write_fernet` (`threads_client.py`): запись — только `THREADS_TOKEN_KEY`; прежний SHA-256 от `LITELLM_MASTER_KEY` остаётся в цепочке чтения (MultiFernet).
- Запись без выделенного ключа вне development → `ThreadsAPIError` (category=config); в development — прежняя логика + разовое предупреждение.
- OAuth-state — отдельный производный ключ `sha256("threads-oauth-state-v1:"+secret)`; у соцсетей HMAC-state — `sha256("social-oauth-state-v1:"+секрет)` в hexdigest (в отличие от прежнего сырого секрета).
- `social_clients.py`: `SOCIAL_TOKEN_KEY` → `THREADS_TOKEN_KEY` для записи, master — только чтение; `*_uses_primary_key` для определения «уже на новом ключе».
- `scripts/rotate_token_key.py`: dry-run по умолчанию; `--apply` требует `--i-have-a-backup`; Fernet недетерминирован → «уже на ключе» определяется проверяемостью первичным ключом, а не сравнением строк; нечитабельные строки не трогает.

**Тесты:** 7 новых (`tests/backend/test_token_key_rotation.py`): legacy читается после установки ключа, новые токены пишутся новым ключом, fail-closed вне dev, чужой ключ → понятная ошибка, отдельный ключ state (Threads + соцсети), dry-run/apply скрипта на SQLite. **Backend 395 passed.**

**Действие владельца:** задать `THREADS_TOKEN_KEY` в `.env`, пересоздать backend-контейнер, затем `python scripts/rotate_token_key.py` (dry-run) и `--apply --i-have-a-backup` **только после** `scripts/backup-db.ps1`. Временный эффект: OAuth-state, выданные до ротации (до 10 минут), станут невалидными — это отказоустойчивый transient.

---

### 1.3 Автоматические бэкапы и проверка восстановления — ✅ ВЫПОЛНЕНО (коммит `e692147`)

**Подтверждение исходной проблемы:** `backup-db.ps1` запускался только вручную; копии лежали на одной машине; регулярного учения по восстановлению не было.

**Реализация:**
- `scripts/schedule-backups.ps1`: **dry-run по умолчанию** (ничего не меняет, печатает точную команду задачи); `-Register` регистрирует ежедневную задачу планировщика Windows; `-SecondaryPath` — копия свежайшего архива на второй носитель; `-KeepCopies` (по умолчанию 14, минимум 2) — чистка старых `threads-recruitment-*.sql.gz`; `-Run` — один цикл (то, что вызывает задача).
- `scripts/restore-db.ps1`: опциональный `-TargetDatabase` — **только** для имён `threads_restore_test_*`: создаёт учебную БД и восстанавливает туда, не трогая рабочие данные. Прежнее поведение не изменено.
- README: новые секции «Scheduled backups», «Monthly restore drill», «Token key rotation».

**Честная пометка:** исполнены dry-run скрипта и статическая проверка синтаксиса PowerShell (SYNTAX OK). Полный цикл `-Run` и учение восстановления не запускались — это требует поднятого Docker/MySQL и делает запись на диске; процедура описана в README, вы executing по желанию.

**Действие владельца:** запустить `schedule-backups.ps1 -Register -SecondaryPath "D:\..."` (пример в README); раз в месяц — учение восстановления.

---

## Фаза 2 — Надёжность воркеров

### 2.1 Реальное разделение воркеров — ✅ ВЫПОЛНЕНО (коммит `db1ddb9`)

**Подтверждение исходной проблемы (D2/I-2):** механизм `WORKER_JOB_KINDS` в коде уже был (`worker/main.py:_job_kinds`), но основной воркер в compose его не задавал → пустой список означал «брать всё», включая `run_agent` и `content_plan`. `worker-agents` в compose задаёт `WORKER_JOB_KINDS: run_agent,content_plan` — изоляция работала только в одну сторону.

**Реализация:**
- В `claim_job` — вычитающий фильтр: при `worker_role != "agents"` разновидности из `AGENT_EXCLUSIVE_KINDS = ("run_agent", "content_plan")` не берутся. Выбран `notin_`, а не явный список «разрешённых» kind — иначе будущий новый тип задач оказался бы недоступен основному воркеру.
- compose не менялся: основному воркеру остаётся пустой список → всё, кроме агентных kinds.
- Закрыт риск аудита «при выключенном worker-agents задачи копятся»: основной воркер в цикле heartbeat проверяет агентные PENDING-задачи (`run_agent`, `content_plan`) и пишет предупреждение `agent_jobs_pending`, если прогресса нет 30+ минут (`AGENT_STUCK_PENDING_MINUTES = 30`).

**Тесты:** 3 новых — основной воркер не берёт агентные kinds; основной воркер берёт прочие kinds; агентный воркер (`worker_role="agents"`) берёт `content_plan`.

---

### 2.2 Продление блокировки на время живого процесса — ✅ ВЫПОЛНЕНО (коммит `db1ddb9`)

**Подтверждение исходной проблемы (I-3/D3):** `process_with_heartbeat` обновлял только `SyncState` (worker heartbeat), а `BackgroundJob.locked_at` оставался с момента захвата. Порог восстановления в `recover_stale_jobs_in_session` — 10 минут, а агентные запуски и медиа-генерации могут идти дольше → работающая задача помечалась `PENDING` («Recovered stale worker lock») и могла выполниться вторым воркером параллельно.

**Реализация:** `prolong_job_lock(job_id)` — обновляет `locked_at` только при `status == RUNNING`; вызывается в цикле heartbeat `process_with_heartbeat` рядом с существующим heartbeat.

**Тесты:** 2 новых — продлённый замок (RUNNING, `locked_at` 15 минут назад) не восстанавливается; контр-кейс без продления восстанавливается как раньше, чтобы первый тест не был тривиальным.

---

### 2.3 Мелочи надёжности инфраструктуры — ✅ ВЫПОЛНЕНО (коммит `1a6bba6`)

- **D6/I-10:** `ai_retries: int = 1` в `config.py` (был 0 — цикл повторов в `AIService._request` был мёртвым кодом). Теперь ровно один корректирующий повтор **только** для 502 (невалидный JSON, пустой ответ, неожиданная форма), таймаут (504) по-прежнему не повторяется. Существующий тест ретрая (`ai_retries=1`) теперь соответствует дефолту.
- **D8/I-10:** `pool_recycle=1800` в `database.py` — долгоживущие воркер-сессии не упираются в серверный idle-таймаут MySQL (`pool_pre_ping=False` остался, причина задокументирована в комментарии).
- **D12/I-10:** `depends_on: backend: condition: service_healthy` для `worker` и `worker-agents` — воркеры стартуют только после того, как backend выполнит `alembic upgrade head` (backend CMD). Проверено `docker compose config --quiet` → OK.

---

### P3 из «Найдено дополнительно» (закрыт в `db1ddb9`)

- `publish_post` / `publish_reply`: нечисловое значение в `threads_settings` больше не роняет обработку — откат к env-дефолту лимита + предупреждение `invalid_hourly_setting`. Семантика `min(settings, AppSetting)` сохранена и покрыта тестом (включая испорченную ветвь).

**Отклонение от «1 пункт = 1 коммит»:** правки 2.1, 2.2 и P3 живут в одном файле `worker/main.py`; чтобы не дробить файл на partial-staging, они вошли одним коммитом `db1ddb9` (тесты 2.1/2.2/P3 в нём же), 2.3 — отдельным `1a6bba6`. Сообщение коммита `db1ddb9` содержит опечатки — содержимое и тесты корректны, история не переписывается.

**Прогоны Фазы 2:** backend **401 → 400** (один тест упал на сравнении наивного `run_at` с aware — исправлен) → финально **400 passed, 0 failed**.

---

## Фаза 3 — Наблюдаемость и данные

### 3.1 Учёт вызовов ИИ (I-4/D5) — ✅ ВЫПОЛНЕНО (коммит `ad2d50f`)

**Подтверждение исходной проблемы:** самый затратный путь в системе — `AIService._chat` (LiteLLM-шлюз): расходует токены и секунды на каждый пост/агентный запуск, но нигде не учитывается: ни счётчиков, ни длительности, ни того, какая модель реально отвечала.

**Реализация:**
- Таблица `ai_usage` + миграция `0018_ai_usage`: `route` (логический алиас, кодирующий роль: `free-default`, …), `resolved_model` (что фактически ответил шлюз), prompt/completion/total токены, `duration_ms`, `finish_reason`, статус `ok/error` с коротким контент-фри-диагностикой, `created_at`. Индексы по `route` и `created_at`.
- Запись из `AIService._chat` и `AIService._record_usage`: inline после завершения вызова (один INSERT), отдельной сессией `SessionLocal`; ошибка записи логируется `ai_usage_write_failed` и **не ломает сам AI-вызов**. Mock-AI (`ai_mock_mode`) `_chat` не вызывает и строк не пишет.
- API `GET /api/threads/agents/usage` (за `OperatorGate`, как весь `/api`): агрегаты по маршрутам (вызовы, токены, средняя/максимальная длительность) + последние 50 вызовов.
- Фронт: блок «Учёт вызовов ИИ» на странице агентов, обновление раз в 60 с. UI — русский.

**Решения:**
- Автоматический флаг fallback **не введён**: шлюз не репортит смену модели надёжно, а выдуманный флаг создавал бы ложные данные. Оператор видит fallback по смене `resolved_model` в рамках одного `route` — это отражено в docstring endpoint и подписи в UI.
- «Роль» = алиас маршрута (у проекта нет отдельной роли в `_chat`); не создаётся параллельная таксономия.

**Тесты:** `tests/backend/test_ai_usage.py` — 4 passed (запись успешного вызова, запись ошибки с коротким текстом без контента, агрегаты/recent в API, отсутствие записей в mock-режиме). Полный backend-набор **411 passed** (с п.3.2/3.3), фронтенд **24 passed**.

**Действие владельца:** ничего для работы; учёт начнёт наполняться при первом реальном AI-вызове. Непроверено на живом шлюзе (всё — mock): при первом включении сверить появление строк с логом backend.

---

### 3.2 Очистка старых данных (I-7/D13) — ✅ ВЫПОЛНЕНО (код), ⏳ ВКЛЮЧЕНИЕ ОСТАЁТСЯ ЗА ВЛАДЕЛЬЦЕМ (коммит `9ca10dc`)

**Подтверждение исходной проблемы:** `activity_events`, `policy_decisions`, фоновые задания и `agent_runs` растут неограниченно; автоматически удалять их нельзя было начать без риска — по правилам работы любое массовое удаление на рабочей БД требует бэкапа и подтверждения владельца (и все прежние шаги фаз это соблюдали).

**Реализация:**
- `worker/main.py: retention_cleanup_in_session` — по группам: `activity_events` 90 дн., `policy_decisions` 180 дн., фоновые задачи 30 дн. (только терминальные DONE/FAILED по `updated_at`), `agent_runs` 90 дн. (только терминальные COMPLETED/FAILED/BLOCKED по `finished_at`). Удаления батчами по 1000 (`RETENTION_BATCH`).
- Еженедельное расписание в `schedule_periodic` (ключ `retention:{год-неделя}` — раз в неделю независимо от рестартов воркера). Выполняет **основной** воркер: `retention` не входит в `AGENT_EXCLUSIVE_KINDS`, так что с п.2.1 все согласовано.
- Миграция `0019`: индекс `agent_runs.finished_at` (выборка терминальных старых прогонов).
- `.env.example` + README («Data retention (audit 3.2)»): что чистится, что никогда не трогается (живой контент, сигналы, кандидаты, вакансии).

**Fail-closed по удалению (главное решение):** `RETENTION_ENABLED=false` по умолчанию. В этом режиме задача **только считает** и пишет предупреждение `retention_dry_run` в лог. Массовое удаление невозможно без явного включения владельцем.

**Тесты:** `tests/backend/test_retention.py` — 5 passed: dry-run ничего не удаляет, enabled удаляет только терминальные старые строки (свежие и нетерминальные остаются), батчи, idempotency-ключ недели, dispatch `retention`-задачи. Тесты шедулера (`test_content_plan.py`) актуализированы под новый kind. Полный набор — в п.3.3.

**Действие владельца (закрытие пункта):**
1. Убедиться, что `scripts/backup-db.ps1` отработал свежий бэкап.
2. Внести в `.env`: `RETENTION_ENABLED=true` (и при желании `RETENTION_*_DAYS`).
3. Пересоздать worker-контейнер; на первый запуск сверить в логе `retention_cleanup_done` с числами dry-run.
Миграции 0018/0019 применятся к рабочей БД автоматически при пересоздании backend (`alembic upgrade head`) — **только после бэкапа** (обе миграции аддитивные: новая таблица и новый индекс, удаления/переименований нет).

---

### 3.3 Аналитика через SQL-агрегаты + кэш (I-8/D7) — ✅ ВЫПОЛНЕНО (коммит `336e742`)

**Подтверждение исходной проблемы:** `/threads/analytics` загружал в Python **все** строки `PostMetric` и все подписи-аттрибуты; `recruitment_intelligence` (`vacancy_demand`, `attribution_quality`) — все посты/взаимодействия/сигналы/кандидаты. На растущей истории это минуты.

**Реализация:**
- `content_analysis.py`: `COMPARABLE_FIELDS` — единый список сопоставимых полей (отсутствие/отрицательность любого = замер непригоден); `latest_metrics` — оконные `ROW_NUMBER()` — только последний валидный замер одного поста; `analytics_aggregates` — GROUP BY по дню и сегменту.
- `recruitment_intelligence.py`: `vacancy_demand` и `attribution_quality` — SQL GROUP BY. Счётные правила сохранены буквально: ignored-сигнал не считается интересом; сигнал без поста не аттрибуируется и не относится к вакансии; человек = один кандидат (DISTINCT по `candidate_id`), даже если писал дважды.
- Поиск дубликатов кандидатов остаётся в Python: нормализация хендлов — апп-логика, SQL-эквивалента нет (CRM-данные малы, ограничение честно отмечено в комментарии в коде).
- Кеш: `backend/app/cache.py` (собственный TTLCache, без новых зависимостей; bound 64). Только `/threads/analytics`, TTL `ANALYTICS_CACHE_SECONDS=120` (0 = выкл; тесты принудительно 0 через conftest).
- `conftest.py`: `ANALYTICS_CACHE_SECONDS=0`.

**Тесты:**
- Golden в `test_analytics.py`: предыдущая inline-агрегация сохранена в тесте как эталон (`_reference_analytics`) и endpoint сверяется с ним на смешанном сиде — посты с двумя замерами (берётся последний), со «сломанным» замером (отрицательные), без замеров, mock-пост, черновик с замером, ignored-сигналы/взаимодействия, кандидат-сигнал. Сверяются: performance по датам, сегменты, топ-10, sample_size и — самое сильное — `evidence` (`analyze_content`) байт-в-байт.
- Тест TTL-кэша: при TTL>0 ответ заморожен на изменении данных, при TTL=0 пересчитывается.
- `test_recruitment_intelligence.py`, `test_p0_measurement.py` — те же золотые регрессии остались зелёными (29 passed целевыми).

**Прогоны фазы 3:** backend `pytest tests/backend` — **411 passed, 0 failed**; frontend `npm run build` + `npm test` — **24 passed**.

**Честные пометки:**
- SQL (window/GROUP BY, `func.date`) проверен **только на SQLite** (тестовый движок): MySQL-вариант разницы не ожидается, но живой прогон не выполнялся — сверить после применения миграций на рабочей БД (нажим на /threads/analytics, сравнить с прежним скрином).
- Кэш — process-local: в multi-worker-развертывании каждый процесс кэширует отдельно (для рид-онли дашборда допустимо, TTL 120 с).

---

## Промежуточные находки

### Фаза 1

| Приоритет | Проблема | Где | Примечание |
|---|---|---|---|
| P3 | `effective_limit = min(settings.max_posts_per_hour, int(configured))` — нечисловое значение в `threads_settings` роняет публикацию с невнятным `ValueError` | `worker/main.py:293` | ✅ ЗАКРЫТО в Фазе 2 (коммит `db1ddb9`). |
| P4 | Тестовый образ кэширует код: после правок обязателен `docker compose build backend-test` | `docker-compose.yml` | Рабочий нюанс, не баг. |
| P4 | OAuth-старт (`/threads/oauth/start`, `/social/*/oauth/start`) теперь требует заголовок токена — это правильно (все мутации защищены), но пользовательский старт OAuth обязан идти после входа через `OperatorGate`; в текущем UI путь и так за гейтом | фронтенд-гейт | Проверено существующими OAuth-тестами. |
| P3 | Refresh-токены соцсетей продолжат читаться через legacy-слой до ротации (охвачены `rotate_token_key.py`); при этом refresh-токен для Threads-аккаунта не хранится вовсе — пере-выдача только через повторный OAuth-вход | `threads_client.py` / дизайн | Ограничение Threads API; вне плана. |

### Фаза 2

| Приоритет | Проблема | Где | Примечание |
|---|---|---|---|
| — | Существенных новых проблем не обнаружено. | | |

### Фаза 3

| Приоритет | Проблема | Где | Примечание |
|---|---|---|---|
| P4 | Полный сбор `pytest -q` (без пути) цепляет `scripts/test_litellm.py`, который требует живого `.env` — collection error вне `tests/backend` | `scripts/` | ✅ ЗАКРЫТО в Фазе 4 (`pytest.ini`: `testpaths = tests/backend`, коммит `1294b6b`). |
| P3 | SQL-агрегаты (`func.date`, window) проверены только на SQLite-движке тестов; живой MySQL-прогон не выполнялся | `content_analysis.py` / `recruitment_intelligence.py` | Разницы не ожидается; сверить визуально при первом применении миграций на рабочей БД. |
| P4 | Учёт вызовов ИИ не проверен на живом шлюзе: mock-режим не вызывает `_chat` | `ai.py` | При первом реальном вызове сверить появление строк `ai_usage` с логом backend. |
| P4 | Предупреждение сборки фронта «chunk >500 kB» (792 kB, gzip 228 kB) | `frontend/` | Существовало до фазы 3; code-splitting вне аудита. |

---

## Фаза 4 — Структура, контейнеры и реактивный воркер

### 4.1–4.2 Гигиена тестов (P4), харденинг Dockerfile (D11) и Redis BLPOP wakeup (D4) — ✅ ВЫПОЛНЕНО (коммит `1294b6b`)

**Реализация:**
- **P4 (`pytest.ini`):** добавлен `testpaths = tests/backend`, чтобы запуск `pytest -q` без аргументов не собирал утилитарные скрипты из `scripts/`.
- **D11 (`frontend/Dockerfile`, `backend/Dockerfile`, `worker/Dockerfile`):**
  - В `frontend/Dockerfile` `npm install` заменён на детерминированный `npm ci --no-audit --no-fund`.
  - В `backend/Dockerfile` и `worker/Dockerfile` создан непривилегированный пользователь `appuser` (UID `10001`), выданы права на `/app/data/media`, активирован `USER appuser`.
- **D4 (`backend/app/queue.py`, `worker/main.py`):**
  - Ролевая маршрутизация Redis wakeup-ключей через `wakeup_key_for_kind(kind)`: агентные задачи (`run_agent`, `content_plan`) будят `threads:jobs:wakeup:agents`, остальные — `threads:jobs:wakeup`. Добавлен `ltrim(..., 0, 99)` против неограниченного роста списка при остановленном воркере.
  - В воркере добавлен `wait_for_wakeup(timeout=2)` на базе `redis.blpop` с безопасным откатом к `asyncio.sleep(timeout)` при недоступности Redis или при монкипатче `asyncio.sleep` в unit-тестах.
- **Тесты:** +2 новых теста в `tests/backend/test_worker_loop.py` (`test_wait_for_wakeup_consumes_role_specific_redis_queue`, `test_wait_for_wakeup_falls_back_to_sleep_when_redis_fails`). Итого **413 passed**.

---

### 4.3 Декомпозиция монолита `backend/app/api.py` (I-9/C1) — ✅ ВЫПОЛНЕНО (коммит `e9d6a11`)

**Подтверждение исходной проблемы:** `backend/app/api.py` насчитывал 1975 строк и объединял 98 маршрутов всех доменов.

**Реализация:**
- `backend/app/api.py` (350 строк) оставлен точкой сборки главного `router` (`/api` с глобальным `require_operator`), маршрутами `/health/live`, `/health/ready`, `/health`, `/dashboard`, `/seed` и общими хелперами/реэкспортами для обратной совместимости с тестами.
- Выделены доменные модули в `backend/app/routers/`:
  - `recruitment.py` (297 строк): `/agencies`, `/vacancies/*`, `/candidates/*`.
  - `content.py` (293 строки): `/threads/posts/*`, `/threads/editorial-memory/*`, `/threads/calendar`, `/threads/content-plan`, `/threads/topics/*`, `/threads/experiments/*`.
  - `community.py` (326 строк): `/threads/community/*`, `/threads/comment-engagement/*`, `/threads/candidate-signals/*`.
  - `intelligence.py` (389 строк): `/threads/agents/*`, `/threads/analytics`, `/threads/attribution`, `/threads/settings`, `/threads/capabilities`, `/threads/audience`, `/threads/autonomy/*`, `/threads/supervisor/*`, `/threads/quality-guard`.
  - `accounts_media.py` (574 строки): `/threads/oauth/*`, `/threads/webhook`, `/threads/account/*`, `/social/*`, `/media/*`.
- **Тесты:** все **413 passed** без изменения единого теста.

---

### 4.4 Декомпозиция монолита `worker/main.py` (I-9/C1) — ✅ ВЫПОЛНЕНО (коммит `4c05748`)

**Подтверждение исходной проблемы:** `worker/main.py` насчитывал 2454 строки (цикл воркера, публикация, синхронизация комьюнити, планировщик контента, эксперименты, разведчик источников, запуск агентов, расписание и очистка).

**Реализация:**
- `worker/main.py` (296 строк) оставлен ядром цикла воркера (`heartbeat`, `warn_stuck_agent_jobs`, `claim_job`, `process_job`, `process_with_heartbeat`, `prolong_job_lock`, `wait_for_wakeup`, `run_forever`) и фасадом реэкспортов для 100% совместимости с `monkeypatch.setattr(worker_main, ...)` в тестах.
- Выделены доменные модули в `worker/`:
  - `publishing.py` (548 строк): публикация постов/ответов, антидубликатный гейт, слоты расписания, генерация медиа, сбор метрик, обновление токена.
  - `community_sync.py` (373 строки): чтение ответов и упоминаний Threads, черновики ответов кандидатам (`draft_replies`), кампании комментирования.
  - `planner.py` (463 строки): `content_plan`, `run_experiment_plan`, детерминированная замена дублирующихся ракурсов (`_substitute_angle`).
  - `sources.py` (370 строк): импорт вакансий из Telegram, разведчик источников (`scout_sources`: Threads keyword search + Google News RSS), бэкфилл фактов вакансий.
  - `agents.py` (285 строк): диспетчер `run_agent`, `run_experimenter`, `_analyst_summary`, `strategize`.
  - `scheduler.py` (399 строк): `schedule_periodic`, `retention_cleanup_in_session`, `recover_stale_jobs_in_session`.
- **Тесты:** все **413 passed** без изменения существующих тестов.

## Статус фаз

- [x] Фаза 0 — 0.1 ✅, 0.2 ✅, 0.3 — ожидает действия владельца (токен Telegram)
- [x] Фаза 1 — 1.1 ✅ (`f300bad`), 1.2 ✅ (`ad90646`), 1.3 ✅ (`e692147`)
- [x] Фаза 2 — 2.1/2.2/P3 ✅ (`db1ddb9`), 2.3 ✅ (`1a6bba6`)
- [x] Фаза 3 — 3.1 ✅ (`ad2d50f`), 3.2 ✅ код / ⏳ включение за владельцем (`9ca10dc`), 3.3 ✅ (`336e742`)
- [x] Фаза 4 — 4.1/4.2 ✅ (`1294b6b`), 4.3 ✅ (`e9d6a11`), 4.4 ✅ (`4c05748`)

## Исправление вырождения контента (2026-10-26)

Симптом: «генерация не становится лучше, становится скучнее». Причина — три разорванные петли обратной связи в дневном планировщике; все три закрыты, для каждого добавлен регрессионный тест.

### Баг 1: подтверждённые редакционные правила не доходили до планировщика
`worker/planner.py` (`content_plan`) создавал `AIService()` «голым»: инъекция `ai.editorial_rules = await approved_rules(db, language, None)` применялась только в ручном пути генерации. Дневной планировщик игнорировал исправления оператора → одни и те же проблемы воспроизводились ежедневно.
**Тест:** `tests/backend/test_planner_feedback_loops.py` (::test_daily_planner_receives_editorial_rules) — правила из `editorial_memory` попадают в `covered_subjects`-вызов планировщика.

### Баг 2: `content_evidence(days)` игнорировал окно
`backend/app/content_evidence.py` сканировал **все** опубликованные посты независимо от параметра `days`, поэтому отчёт «что сработало» со временем замораживался на старой статистике.
**Фикс:** фильтр `ThreadsPost.published_at >= now - timedelta(days=days)`.
**Тест:** `::test_content_evidence_respects_window` — старые посты выпадают из окна, свежие остаются.

### Баг 3: `covered_subjects` учитывал черновики и не имел окна
`worker/planner.py` считал «уже освещённые темы» по последним 40 постам **любого** статуса и без временного окна: черновики, заменённые ракурсы и заблокированные дубли съедали пространство тем, и промпт говорил «всё уже освещено», даже когда ничего нового не опубликовано.
**Фикс:** `status == PUBLISHED` + окно 21 день (`CONTENT_DEDUP_WINDOW_DAYS`).
**Тесты:** `::test_covered_subjects_ignores_unpublished_drafts`, `::test_fresh_published_post_still_steers_the_planner`.

### Сопутствующие фиксы флаки-тестов
`_substitute_angle` вращает порядок ракурсов по `tm_yday % 7`, поэтому два теста в `tests/backend/test_autonomous_content.py` проваливались в зависимости от дня календаря:
- `test_plan_creates_drafts_even_when_every_proposal_is_a_duplicate` — планировщикный датзаморожен (`_FrozenPlanDate`, 2026-04-01 → 91 % 7 == 0);
- `test_plan_refuses_two_posts_with_the_same_subject` — ассерт переведён на записанную планировщиком `draft.content_subject` (гарантированный инвариант) вместо пересчёта ключевой эвристики `primary_subject(content)`.

### Верификация
- Новый файл `tests/backend/test_planner_feedback_loops.py` (4 теста) + правки двух флаки-тестов.
- Полный бэкенд-прогон в контейнере: **417 passed**.
- Ревью code-reviewer: PASS WITH NITS; обязательная правка (`assert instance is not None` в тестах 3 и 4) применена до коммита.

