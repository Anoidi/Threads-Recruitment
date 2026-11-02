# SaltGroup Threads — аудит интеграции

> **Обновление 26.10.2026, 15:16 Europe/Warsaw:** после действий пользователя
> действующий токен `_salt_group_` проверен повторно: is_valid=true, оба scope
> `threads_manage_mentions` и `threads_keyword_search` теперь выданы.
> GET /v1.0/me/mentions и GET /v1.0/keyword_search?q=praca вернули HTTP 200,
> оба ответа содержали пустой список data. Доступ к endpoint подтверждён;
> обнаружение чужих публичных обсуждений этими результатами не подтверждено.
> Всего debug вернул 11 scopes, включая delete, location_tagging,
> profile_discovery и share_to_instagram; подготовленная ранее форма с семью
> правами не описывает итоговое согласие пользователя. Дополнительные права
> аудитором не использовались. Local scopes/expiry синхронизированы.
> emergency_stop остаётся true, публикации и ответы не выполнялись.
> Ниже сохранена история диагностики; прежние записи «два scope отсутствуют»
> описывают состояние до этого обновления.

Дата: 26.10.2026, Europe/Warsaw. Проверен существующий проект, без создания нового.

## Итог

**Действующий токен `@_salt_group_` работает.** Шесть безопасных GET-проверок Meta
вернули HTTP 200. Ошибка OAuth 190 на этом токене не воспроизвелась.
Причина неправильной таблицы прав подтверждена: Meta возвращает `data.scopes`,
а приложение читало только `granted_scopes`. Локальные права и срок действия
исправлены по проверенному ответу API. Код чтения прав исправлен.

**Общий результат: PARTIAL / BLOCKED для нового сквозного OAuth-подключения.**
В конфигурации HTTP callback с localhost; Meta требует HTTPS и локальный домен.
Новый вход в браузере и обмен реального authorization code не выполнялись.
Для второго аккаунта нет сохранённого действующего токена.

## 1. Архитектура и действующие процессы

| Компонент | Реализация / точка входа | Проверенное состояние |
| --- | --- | --- |
| Frontend | React, TypeScript, Vite; `frontend/src`, nginx | `http://127.0.0.1:3000`, HTTP 200 |
| Backend | FastAPI; `backend/app/main.py`, facade `app/api.py`, domain routers | `http://127.0.0.1:8000`, health/live и health/ready HTTP 200 |
| Threads API | `backend/app/threads_client.py`, LiveThreadsClient | Bearer header; API `https://graph.threads.net/v1.0` |
| OAuth и аккаунт | `backend/app/routers/accounts_media.py` | start, callback, connect, token, test, disconnect реализованы |
| Хранилище | MySQL 8.4, SQLAlchemy async, Alembic | отдельные ThreadsAccount по profile_id; Fernet ciphertext в БД |
| Background jobs | `worker/main.py`, publishing, scheduler, community sync | основной worker и worker-agents в Docker |
| Вспомогательные сервисы | Redis, LiteLLM, Adminer | Docker Compose; Hermes опционален |

PID 23892 принадлежит `com.docker.backend.exe`. Порты 3000 и 8000 слушают
127.0.0.1 и опубликованы контейнерами именно этого Compose-проекта.
Frontend proxy `/api/health/live` также вернул HTTP 200.
Чужие процессы не завершались. Способ локального запуска: Docker Desktop,
затем `docker compose up -d --build` из корня проекта. При этом соблюдаются
текущие флаги автономности; команда не нужна для уже работающего стека.

Исходники перед аудитом были новее запущенных контейнеров: например, в
развёрнутом Settings ещё отсутствовал operator_token. Это подтверждено
проверкой runtime, а не предположением по времени создания контейнера.

## 2. Обнаруженные проблемы и исправления

| Проблема | Доказательство / влияние | Результат |
| --- | --- | --- |
| Не читалось `data.scopes` | Meta вернула пять прав, БД хранила два | Исправлены общий parser, OAuth callback и импорт токена; local metadata синхронизированы |
| Callback при сбое debug присваивал все запрошенные права | `permissions = list(OAUTH_SCOPES)` | Убрано; остаются только полученные права либо basic, подтверждённый профилем |
| OAuth state не привязан к браузеру | Проверялись только подпись и время | Добавлена HttpOnly cookie, SameSite=Lax для внешнего GET callback, Secure при HTTPS; сравнение constant time, удаление cookie после callback |
| Будущий state проходил проверку возраста | Проверялось только age <= 600 | Теперь возраст должен быть от 0 до 600 секунд |
| Переподключение старого аккаунта могло оставить другой аккаунт выбранным | OAuth оставлял несколько connected, чтение выбирало максимальный id | OAuth приводит active selection к одному аккаунту, как уже делал импорт токена |
| Успешный JSON не обязательно является объектом | Array/null вызывали ненормализованные ошибки | Проверка формы ответа, ThreadsAPIError вместо AttributeError |
| Риск URL с OAuth-секретами в HTTPX INFO logs | OAuth exchange/refresh используют GET query | HTTPX/httpcore INFO/DEBUG отключены; uvicorn access log отключён, middleware пишет только path |
| UI безусловно отправлял отсутствующие scope в App Review | Отсутствие права токена не устанавливает статус доступа приложения | Инструкции учитывают token selection, роль tester и отдельную проверку App Review |
| Миграция 0018 не запускалась на MySQL | MySQL 1101: literal DEFAULT у error TEXT | Убран server_default; ORM по-прежнему заполняет пустую строку. Миграции реально применены до 0019 head |
| Callback `http://localhost:8000/...` не соответствует требованиям Meta | Официальный sample требует HTTPS и domain mapping | **BLOCKED** до согласованной настройки HTTPS callback |
| Нет выделенного THREADS_TOKEN_KEY | Runtime и .env: пустой | Ограничение: development использует ключ, производный от LITELLM_MASTER_KEY |
| OPERATOR_TOKEN не настроен | Runtime / локальная конфигурация | Ограничение: development допускает доступ без токена; сервисы остаются loopback-only |
| `.env AUTO_*=false` не означает остановку публикаций | В БД действовала policy AUTO с emergency_stop=false; старые флаги не являются источником истины | Включён штатный emergency_stop через локальный API, с записью причины аудита. Режим AUTO сохранён, внешние действия остановлены |

Изменённые файлы:

- `backend/app/threads_client.py`: scopes parser, проверка state и JSON.
- `backend/app/routers/accounts_media.py`: OAuth cookie, права, active account.
- `backend/app/logging.py`, `backend/Dockerfile`: защита журналов.
- `backend/app/capabilities.py`, `docs/meta-scopes-request.md`: корректные инструкции.
- `backend/alembic/versions/0018_ai_usage.py`, `tests/backend/test_migrations.py`:
  совместимость TEXT-поля с MySQL и offline SQL regression.
- `tests/backend/test_oauth_and_webhook.py`, `tests/backend/test_threads_audit_regressions.py`: регрессии.
- `scripts/threads_readonly_audit.py`: повторяемые GET-пробы; опциональный
  `--sync-metadata` изменяет только local scopes/expiry, не снимает паузы с публикаций.

## 3. Проверка OAuth

| Проверка | Результат |
| --- | --- |
| Threads App ID | PASS: настроен `1649309346853697`, а не Meta App ID `1104985815242970` |
| Threads App Secret | Присутствует; значение не выводилось. Правильность пары через реальный code exchange не подтверждена |
| Authorization URL | `https://threads.com/oauth/authorize`, client_id, scope, response_type=code, redirect_uri, state |
| Redirect URI в authorize и exchange | Оба используют settings.threads_redirect_uri; mock-тест проверяет значение при обмене |
| Реальный callback endpoint | Реализован `/api/threads/oauth/callback`; неверный state отклоняется до обмена кода |
| CSRF | Регрессионно проверены tampered state, действительный state без исходного браузера и будущий state |
| Code exchange | POST `/oauth/access_token` server-side; реальный обмен SKIP, mock flow PASS |
| Long-lived exchange | GET `/access_token`, th_exchange_token; реальный обмен SKIP |
| Refresh | GET `/refresh_access_token`, th_refresh_token; scheduler ставит refresh в последние 7 дней до expiry, не после expiry |
| Реальный refresh | SKIP: не нужен для текущего токена; отсутствие ошибки при GET /me не доказывает успешность refresh |
| Token audience / app binding | Передан настроенный app_id при debug; ответ не содержал app_id. Независимое подтверждение audience **BLOCKED**, не выдаётся за PASS |
| Browser login end-to-end | **BLOCKED**: HTTPS callback, whitelist Meta и действие пользователя |

State cookie требует одного и того же hostname для начала OAuth и callback.
Нельзя начинать flow на 127.0.0.1, а возвращаться на localhost или домен туннеля.
Удаление cookie ограничивает обычный повторный callback; отдельного серверного
хранилища одноразовых state не добавлялось.

## 4. Токены и разрешения

В .env нет THREADS_ACCESS_TOKEN: действующий токен хранится зашифрованным в БД.
Расшифрование проверено локально без вывода значения. GET /me вернул ожидаемый
профиль, его ID совпал с сохранённым. Debug: `is_valid=true`.
Expiry: **25.12.2026 14:24:34 Europe/Warsaw**. Срок сохранён в БД.

| Scope | Meta debug текущего токена |
| --- | --- |
| threads_basic | Есть |
| threads_content_publish | Есть |
| threads_manage_replies | Есть |
| threads_read_replies | Есть |
| threads_manage_insights | Есть |
| threads_manage_mentions | Нет |
| threads_keyword_search | Нет |

Scope content_publish/manage_replies подтверждены метаданными; способность
создать реальный пост/ответ не проверялась согласно ограничениям пользователя.
Наличие остальных пяти прав опровергает прежнее объяснение, что у приложения
есть лишь basic/content_publish и обязательно нужно согласовать все пять.

У `saltgroup_artem` обнаружены две отключённые исторические записи без токенов;
live-проверки этого аккаунта SKIP. Mock-запись demo_recruiter также SKIP.
Исторические записи и контент не удалялись.

Модель БД хранит разные токены по profile_id, но продукт работает с одним
активным Threads-аккаунтом. UI-переключатель двух одновременно работающих
аккаунтов и полная изоляция всех jobs по аккаунтам не реализованы. Это
ограничение архитектуры, а не подтверждённая поддержка multi-account.

## 5. Результаты безопасных GET-тестов

Все строки ниже действительно выполнены на действующем токене `@_salt_group_`.
Текст публикаций, токены и секреты в отчёт не включены.

| Тест | Endpoint на graph.threads.net | HTTP | Итог | Ошибка |
| --- | --- | --- | --- | --- |
| Профиль | GET /v1.0/me?fields=id,username,name | 200 | PASS, username и сохранённый ID совпали | — |
| Диагностика | GET /debug_token | 200 | PASS, is_valid, scopes, expiry получены | — |
| Свои публикации | GET /v1.0/me/threads?fields=id&limit=1 | 200 | PASS, найдена собственная публикация | — |
| Метрики | GET /v1.0/{own-post-id}/insights | 200 | PASS, разрешённые метрики доступны | — |
| Ответы | GET /v1.0/{own-post-id}/replies | 200 | PASS, endpoint доступен; не доказывает наличие новых ответов | — |
| Квота | GET /v1.0/me/threads_publishing_limit | 200 | PASS, endpoint доступен; не доказывает право публикации | — |
| Второй аккаунт | GET /me | — | SKIP | Нет сохранённого live-токена |
| Упоминания, keyword search | GET /mentions, /keyword_search | — | SKIP | Нет соответствующих scopes |
| Реальная публикация / ответ | POST /threads, /threads_publish | — | SKIP | Требуется отдельное согласование пользователя |

Ошибка code 190 не воспроизвелась. Классификация API-ошибок, transient retry,
Bearer header, неизвестный результат POST и двухэтапная публикация проверяются
существующими mock HTTP tests, без обращений к Meta POST.

## 6. Автоматические проверки

- Финальный полный backend после всех исправлений: **432 passed**, 69.01 секунд.
- Изолированный Docker test runtime, `--network none`, актуальные исходники
  смонтированы read-only, временная SQLite БД; production БД тестами не используется.
- Дополнительный целевой прогон OAuth/client/monitoring: **38 passed**.
- После обнаружения ошибки MySQL: migration + AI usage tests — **7 passed**;
  затем настоящий MySQL upgrade до `0019_agent_runs_finished_index (head)` — PASS.
- Frontend: **24 passed**; production `tsc -b && vite build` — PASS.
- Предупреждения frontend: существующий bundle >500 kB, размеры графиков в jsdom,
  future flags React Router. Ошибок сборки и проваленных тестов нет.
- `git diff --check` — PASS.
- .env не отслеживается Git, игнорируется; секреты не добавлены в новые файлы.
- Проверены последние 2000 строк журналов каждого backend/worker/worker-agents:
  URL с OAuth access_token/input_token/client_secret не обнаружены. Это не
  является проверкой всей истории логов или ранее раскрытых пользователем токенов.

## 7. Что реально реализовано

Публикация текста, создание ответов, own-post sync, чтение ответов, метрики,
mentions, keyword search и token refresh имеют backend/worker реализацию.
Первые GET-функции подтверждены live; публикация — mock-тестами. Mentions и
search существуют в коде, но с текущим токеном недоступны. Нельзя считать их
работающими только по наличию интерфейса. UI capability report читает БД,
сам по себе не является новым запросом Meta.

## 8. Локальное применение и оставшиеся ограничения

Резервная копия БД создана до обновления контейнеров:
`backups/threads-recruitment-20261026-143948.sql.gz` (проверка gzip выполнена
скриптом backup; полное восстановление в отдельную БД в этом аудите SKIP).
Backend и два worker image пересобраны из актуальных исходников и обновлены.
Первый запуск выявил ошибку миграции 0018; после её исправления повторный запуск
успешен. Финальная проверка: backend, frontend, worker, worker-agents, MySQL,
Redis, LiteLLM и Adminer running/healthy. Alembic находится на 0019 head.
После обновления повторены все шесть GET Meta — HTTP 200. Через frontend proxy
`GET /api/threads/capabilities` вернул HTTP 200, пять granted и только два missing:
mentions, keyword_search. Реальный callback с неверным state перенаправил на
`/account?threads_error=state`, страница назначения вернула HTTP 200.

Meta-настройки, роли пользователей, App Secret, App Review не менялись.
Туннели не запускались, backend в интернет не публиковался.
AUTO_POST/AUTO_REPLY/AUTO_EXTERNAL_COMMENT в .env false; эти флаги не менялись.
Однако фактическая policy в БД была AUTO, emergency_stop=false. Во время
финальной проверки включён emergency_stop=true штатным локальным API с actor
threads-integration-audit и причиной: соблюдать пользовательский запрет на
реальные внешние действия. Остановка оставлена включённой, mode AUTO сохранён.
Этот механизм останавливает публикации/ответы, а не read-only аналитику.
Реальные посты, ответы, черновики и контейнеры не создавались аудиторскими запросами.

## 9. Следующие действия пользователя

1. Для работающего аккаунта переподключение ради метрик/чтения ответов сейчас
   не требуется: токен имеет соответствующие права, исправлено их распознавание.
2. Для нового OAuth подготовить локальный домен, например `saltgroup-threads.test`,
   запись hosts на 127.0.0.1 и доверенный сертификат mkcert. Локальный HTTPS proxy
   на 127.0.0.1:8443 направляет UI и /api на frontend:3000. Предлагаемый callback:
   `https://saltgroup-threads.test:8443/api/threads/oauth/callback`.
   Начинать вход на этом же HTTPS hostname. Сертификат/hosts/proxy пока не настроены.
3. **Требуется отдельное подтверждение пользователя перед изменением Meta:**
   сохранить точно этот callback в Threads use case приложения SaltGroup Threads,
   Meta App ID 1104985815242970; затем указать то же значение THREADS_REDIRECT_URI,
   согласовать FRONTEND_URL и allowed origins, пересоздать локальные сервисы.
   App ID/Secret должны быть именно Threads-парой, не основной Meta-парой.
4. Cloudflared/ngrok не найдены в PATH. Публичный туннель технически возможен,
   но его запуск запрещён текущим заданием без подтверждения; для локальной
   разработки подходит HTTPS domain mapping из официального sample Meta.
5. Для saltgroup_artem пройти отдельный browser consent после настройки callback
   либо использовать существующий локальный UI импорта токена. Не отправлять
   токен в чат. Импорт выбирает один активный аккаунт.
6. Для mentions/search проверить доступ этих двух прав в Meta и выдать новый
   токен с ними, если разрешено. Проверить необходимость App Review отдельно;
   обновление срока старого токена не добавляет scopes. Заявка не подавалась.
7. До доступа извне настроить OPERATOR_TOKEN и отдельный THREADS_TOKEN_KEY с
   резервной копией и штатной миграцией шифрования. Текущий fallback работает
   только как development-конфигурация; публичный доступ сейчас не включён.
8. Для возобновления внешних действий отдельно согласовать снятие
   emergency_stop. Простое изменение AUTO_POST в .env не управляет действующей
   policy AUTO. Аудит оставил остановку включённой намеренно.

## Источники и границы проверки документации

### Найден экран явного выбора прав через Graph API Explorer

В официальном Graph API Explorer выбран graph.threads.net/v1.0 и приложение
SaltGroup Threads. Кнопка Generate Threads Access Token открыла согласие
Threads от имени `_salt_group_`, с redirect на собственный token generator Meta.
В этом flow запрошены 11 scopes, включая mentions и keyword_search.
По ссылке «Edytuj uprawnienia dostępu» доступны переключатели этих прав.
В подготовленной форме оставлены семь нужных разрешений; лишние delete,
location_tagging, profile_discovery и share_to_instagram отключены.
Это пока только выбор в форме: финальная кнопка «Kontynuuj jako _salt_group_»
не нажималась, новый доступ и новый токен в этом flow ещё не получены.
Подтверждение расширения доступа ожидается от пользователя.
Не установлено, какие именно scopes запрашивали два предыдущих генератора:
их токены фактически содержали только пять прав. Рекомендация просто повторять
выдачу токена заменена явной проверкой consent с двумя нужными правами.

### Повторная проверка предоставленного токена

После явного разрешения пользователя использован предоставленный им токен.
Его значение и ссылка с ним в отчёт не включены. GET /me и /debug_token
вернули HTTP 200, профиль `_salt_group_`, is_valid=true и те же пять scopes.
`threads_manage_mentions` и `threads_keyword_search` у этого токена отсутствуют.
Токен импортирован штатным локальным endpoint, хранится зашифрованным.

Обнаружена дополнительная причина потери прав при импорте: внутренний
debug_token отправлял input_token/app_id без Authorization, в отличие от
успешной live-пробы с Bearer. Добавлен Bearer header в этот GET-запрос и
регрессионный HTTP mock-тест, проверяющий аутентификацию и чтение scopes.
Целевые OAuth/client/regression tests после исправления: **32 passed**.
Локальные scopes/expiry восстановлены по успешному авторизованному GET.
Предыдущий полный прогон 432 тестов относится к состоянию до этого
дополнительного исправления; новый полный прогон не заявляется.
Раскрытый в чате токен всё ещё рекомендуется заменить; пользователь явно
попросил использовать его. emergency_stop сохраняется включённым.

### Дополнительная проверка Meta Developers, 26.10.2026

В открытой авторизованной вкладке SaltGroup Threads непосредственно проверена
таблица Permissions and Features: `threads_manage_mentions` и
`threads_keyword_search` имеют статус **Ready for testing**. В Settings →
User Token Generator присутствуют обе строки `_salt_group_` и `saltgroup_artem`,
с кнопками Generate Access Token. Описание генератора указывает выдачу
long-lived токенов тестировщикам с публичными Threads-профилями.

Это подтверждает доступность тестирования на стороне приложения, но не выдачу
этих двух прав действующему токену и не доступ к чужим результатам поиска.
Кнопки выпуска токена не нажимались, новые разрешения не предоставлялись,
App Review и настройки Meta не изменялись. Следующий шаг: пользователь выпускает
токен для `_salt_group_`, проверяет включение обоих scope в consent, сохраняет
его через локальный интерфейс приложения; затем выполняются debug и GET-пробы.
Выпуск новых учётных данных передан пользователю согласно правилам безопасности
браузера. Токен не следует отправлять в чат.

- [Официальный sample Meta](https://github.com/fbsamples/threads_api): различие
  основной и Threads-пары, HTTPS callback, локальное сопоставление домена и mkcert.
- [Официальная коллекция Meta Threads API](https://www.postman.com/meta/threads/documentation/dht3nzz/threads-api):
  серверная авторизация и endpoints; коллекция предупреждает о неполноте новых функций.
- Прямые страницы developers.facebook.com возвращали 429/ошибку доступа. Полное
  соответствие всем последним изменениям документации не объявляется PASS.
  Фактическая форма scopes подтверждена непосредственно live-ответом Meta.
