# Дополнительные интеграции

[К полной настройке](SETUP_RU.md)

## Instagram

В проекте реализованы OAuth, хранение токена и проверка профиля через Instagram Login. **Планировщик публикаций Instagram этим модулем не реализован.** Кнопка подключения и scope на публикацию не означают готовый кросспостинг.

1. Подготовьте профессиональный профиль Business/Creator и приложение Meta с Instagram API / Instagram Login.
2. Настройте в кабинете redirect URI, роли тестировщиков и доступные разрешения. Код запрашивает `instagram_business_basic` и `instagram_business_content_publish`.
3. Заполните `.env`:

```dotenv
INSTAGRAM_APP_ID=YOUR_INSTAGRAM_APP_ID
INSTAGRAM_APP_SECRET=YOUR_PRIVATE_INSTAGRAM_SECRET
INSTAGRAM_REDIRECT_URI=https://recruit.example.com/api/social/oauth/instagram/callback
```

4. Задайте постоянный `SOCIAL_TOKEN_KEY`. Настройте HTTPS, reverse proxy и `FRONTEND_URL` по [руководству Threads](THREADS_RU.md). Сохраняйте операторскую защиту. Маршруты также проверяют `SOCIAL_LOCAL_ONLY`; если удалённая схема даёт 403, разберите сетевую границу, не отключайте проверку вслепую.
5. Пересоздайте backend и workers. На странице аккаунта выберите Instagram → подключить → подтвердите в браузере → проверьте профиль.

Текущий social OAuth хранит ожидаемый state **в памяти процесса**. Используйте один backend и не перезапускайте его между началом и callback; несколько реплик требуют доработки хранения state. Не обещайте пользователю HA OAuth на текущем коде.

Источник: [официальная коллекция Instagram API Meta](https://www.postman.com/meta/instagram/documentation/6yqw8pt/instagram-api).

## TikTok

Есть OAuth/Login Kit, зашифрованное сохранение токена и проверка профиля. Готовой очереди публикаций TikTok в этом модуле нет.

1. В [TikTok for Developers](https://developers.tiktok.com/) создайте своё приложение и настройте Login Kit. Код запрашивает `user.info.basic`, `video.upload`, `video.publish`: доступ к последним может требовать настройки и проверки Content Posting API, а не только Login Kit.
2. Зарегистрируйте HTTPS redirect URI и заполните:

```dotenv
TIKTOK_CLIENT_KEY=YOUR_CLIENT_KEY
TIKTOK_CLIENT_SECRET=YOUR_PRIVATE_CLIENT_SECRET
TIKTOK_REDIRECT_URI=https://recruit.example.com/api/social/oauth/tiktok/callback
```

3. Выполните те же шаги с `SOCIAL_TOKEN_KEY`, `FRONTEND_URL`, защитой и пересозданием backend, что для Instagram. Подключите TikTok через страницу аккаунта и проверьте username.
4. Если провайдер отклоняет scope, проверьте доступ приложения к продукту и review. Не считайте подключение профиля доказательством права на публичную публикацию видео.

Источники: [Login Kit Web](https://developers.tiktok.com/doc/login-kit-web/), [Content Posting API](https://developers.tiktok.com/doc/content-posting-api-get-started/).

## ComfyUI: настоящие изображения

ComfyUI не входит в Docker-стек проекта. Установите его отдельно по [официальной инструкции](https://docs.comfy.org/installation/desktop/windows), проверьте совместимость GPU и выбранного checkpoint. Веса и лицензии проверяет владелец установки. Начните с небольшого изображения 512×512.

1. Запустите ComfyUI на хосте и проверьте `http://localhost:8188/system_stats` (порт может отличаться).
2. Установите совместимый checkpoint. В репозитории есть `workflows/image-sd15-api.json`, который требует SD1.5 checkpoint и стандартные узлы. Сам JSON не скачивает модель.
3. В `.env` укажите:

```dotenv
COMFYUI_BASE_URL=http://host.docker.internal:8188
COMFYUI_MOCK_MODE=false
COMFYUI_IMAGE_WORKFLOW=image-sd15-api.json
IMAGE_CHECKPOINT=YOUR_INSTALLED_CHECKPOINT.safetensors
```

Имя checkpoint должно точно совпадать с установленным файлом в ComfyUI. Для другого workflow экспортируйте **API-format JSON** в `workflows/`; формат графа редактора не подходит. Поддерживаемые placeholders перечислены в [workflows/README.md](../workflows/README.md).

4. Проверьте доступ из Docker: сервис хоста не находится по `localhost` контейнера. Если ComfyUI слушает только loopback и Docker не может подключиться, настройте интерфейс прослушивания и firewall для локального Docker-доступа, сохранив запрет внешнего доступа.
5. Пересоздайте backend и workers, создайте одну генерацию через UI и дождитесь READY с реальным файлом. Протестируйте изображение глазами. Не публикуйте его автоматически.

Скрипты `start-comfyui.ps1` и `download-image-checkpoint.ps1` рассчитаны на отдельную установку исходников в `.cache/ComfyUI`, первый — на её `.venv`. Это **не** универсальные установщики для Desktop. Для ручной установки из исходников агент должен следовать upstream, выбрать совместимые Python/PyTorch, создать изолированное окружение и затем выполнить проверки. Не запускайте эти скрипты с ожиданием, что они сами установят весь ComfyUI.

`LOCAL_VIDEO_FALLBACK=true` допускает локальный fallback; он не доказывает работу генеративной видеомодели. Для настоящего видео нужны свой API workflow, все checkpoint/encoder-файлы и достаточная память. В комплекте нет готовой установки Wan. `MEDIA_PUBLIC_BASE_URL` нужен, когда внешняя платформа должна скачать файл по доступному HTTPS URL; localhost, закрытый URL с операторским токеном и путь Docker для неё недоступны. Размещайте только специально подготовленные медиа, не открывайте весь API ради скачивания файла.

## Hermes

Не нужен для обычной генерации через LiteLLM. Это дополнительный агентный runtime с собственным состоянием, а не способ получить API-ключ модели.

1. Сначала добейтесь настоящего ответа LiteLLM.
2. Установите `HERMES_ENABLED=true`, отдельный `HERMES_API_KEY` и, если хотите использовать его для стратегии, `HERMES_FOR_STRATEGIST=true` в `.env`.
3. Запустите профиль:

```powershell
docker compose --profile hermes up -d
docker compose --profile hermes up -d --force-recreate backend worker worker-agents
docker compose --profile hermes ps
```

Проверьте актуальность и доступность `HERMES_IMAGE`, зафиксированного в `.env.example`. Если образ недоступен, не подменяйте его произвольным; сверяйтесь с upstream Nous Research. Порт 8642 оставляйте локальным. Затем проверьте одну агентную задачу и историю результата.

`hermes-init` копирует seed-конфигурацию только при первом создании volume. Правка `hermes/config.yaml` не переписывает существующий volume. Сначала сделайте backup состояния и прочитайте [hermes/README.md](../hermes/README.md); не удаляйте volume как обычный шаг настройки.
