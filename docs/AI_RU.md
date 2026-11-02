# Подключение AI

[К полной настройке](SETUP_RU.md)

## Как устроен маршрут

Backend и worker → LiteLLM → модель. Внутренние имена моделей: `free-fast`, `free-default`, `free-reasoning`, `free-coder`, `free-long-context`, `hermes-agent`, `local`. Слово `free` — имя маршрута, оно не означает бесплатный тариф.

`AI_MOCK_MODE=true` и `/app/config.mock.yaml` дают тестовые ответы. Для реального AI нужно изменить обе настройки. Текущий `litellm/config.yaml` читает основные маршруты из `OMNIROUTE_*`; переменные `LITELLM_FREE_*`, хотя встречаются в Compose, этим YAML не используются. Один только `OPENROUTER_API_KEY` тоже не переключит маршрут.

`LITELLM_MASTER_KEY` — ваш внутренний пароль шлюза. `OMNIROUTE_API_KEY` — ключ внешнего провайдера выбранного маршрута. Это разные секреты.

## Вариант 1: облачный AI через OpenRouter

1. Создайте собственный аккаунт в [OpenRouter](https://openrouter.ai/), откройте [Keys](https://openrouter.ai/keys), создайте API-ключ с ограничением расходов.
2. В [каталоге моделей](https://openrouter.ai/models) выберите доступную вашему аккаунту chat-модель. Скопируйте её точный ID вида `provider/model`; проверьте поддержку нужных языков и JSON. Цена и доступность зависят от модели и аккаунта.
3. В вашей `.env` замените значения ниже. `YOUR_PROVIDER/YOUR_MODEL` — заполнитель, его обязательно заменить. Ключ вводите локально, не в GitHub.

```dotenv
AI_MOCK_MODE=false
LITELLM_CONFIG_FILE=/app/config.yaml
OMNIROUTE_BASE_URL=https://openrouter.ai/api/v1
OMNIROUTE_API_KEY=YOUR_PRIVATE_OPENROUTER_KEY
OMNIROUTE_MODEL_FAST=openai/YOUR_PROVIDER/YOUR_MODEL
OMNIROUTE_MODEL_DEFAULT=openai/YOUR_PROVIDER/YOUR_MODEL
OMNIROUTE_MODEL_REASONING=openai/YOUR_PROVIDER/YOUR_MODEL
```

Префикс `openai/` здесь выбирает совместимый протокол LiteLLM; сам провайдер определяется `OMNIROUTE_BASE_URL`. Название переменной OmniRoute не требует устанавливать OmniRoute. Для начала все три маршрута можно направить на одну модель.

4. Примените настройки:

```powershell
docker compose up -d --force-recreate litellm backend worker worker-agents
docker compose ps
```

В текущем YAML `local` остаётся резервом Ollama. Если Ollama не установлена, при отказе облака резерв тоже завершится ошибкой. Для облачной установки без локального резерва удалите блок `router_settings.fallbacks` из своей копии `litellm/config.yaml`; сохраните `model_list` и `general_settings`. Не считайте резерв проверенным без отдельного запроса к `local`.

## Вариант 2: Ollama на своём компьютере

1. Установите [Ollama](https://ollama.com/download). Запустите приложение.
2. Выберите доступную instruct/chat-модель из [библиотеки](https://ollama.com/library), подходящую памяти компьютера. Скачайте её командой `ollama pull ТОЧНЫЙ_ТЕГ`; затем проверьте `ollama list` и `ollama run ТОЧНЫЙ_ТЕГ`.
3. Для полностью локального маршрута используйте Ollama OpenAI-compatible API. В `.env`:

```dotenv
AI_MOCK_MODE=false
LITELLM_CONFIG_FILE=/app/config.yaml
OMNIROUTE_BASE_URL=http://host.docker.internal:11434/v1
OMNIROUTE_API_KEY=ollama
OMNIROUTE_MODEL_FAST=openai/YOUR_INSTALLED_OLLAMA_TAG
OMNIROUTE_MODEL_DEFAULT=openai/YOUR_INSTALLED_OLLAMA_TAG
OMNIROUTE_MODEL_REASONING=openai/YOUR_INSTALLED_OLLAMA_TAG
LITELLM_LOCAL_API_BASE=http://host.docker.internal:11434
LITELLM_LOCAL_MODEL=ollama_chat/YOUR_INSTALLED_OLLAMA_TAG
LITELLM_PROVIDER_API_KEY=ollama
```

Замените все `YOUR_INSTALLED_OLLAMA_TAG` одним точным тегом из `ollama list`. `ollama` в поле ключа — непустой заполнитель для локального API, не облачный ключ. Наличие в шаблоне `qwen3:4b-instruct` не гарантирует, что этот тег существует и скачан на вашей машине.

4. Проверьте API на хосте: `Invoke-RestMethod http://localhost:11434/api/tags`.
5. Пересоздайте четыре сервиса той же командой, что для облака. Проверьте запрос из Docker через LiteLLM.

Из контейнера `localhost` обозначает контейнер. Для сервиса Windows используйте `host.docker.internal`. На Linux может потребоваться `extra_hosts: ["host.docker.internal:host-gateway"]` для LiteLLM. Если Docker не видит Ollama, проверьте её адрес прослушивания и firewall. Меняйте `OLLAMA_HOST` только при необходимости и не открывайте незащищённый порт всему интернету. Сначала проверьте доступность хоста, затем контейнера.

## Вариант 3: свой совместимый шлюз / LM Studio / OmniRoute

Получите base URL с `/v1`, ключ и точный ID модели у своего шлюза. Заполните `OMNIROUTE_BASE_URL`, `OMNIROUTE_API_KEY` и все три `OMNIROUTE_MODEL_*` с префиксом `openai/`. В контейнере нельзя использовать `localhost` для сервиса хоста. Не переносите модельные ID автора проекта: они принадлежат его шлюзу.

Для нативных Anthropic, Gemini, Groq и других API нужно настроить соответствующий адаптер в `litellm/config.yaml` по [документации провайдеров LiteLLM](https://docs.litellm.ai/docs/providers), сохранив внутренние `model_name`. Эти API не обязательно принимают OpenAI-совместимые запросы. Секреты подключайте через `os.environ/ИМЯ_ПЕРЕМЕННОЙ`, не вписывайте ключ в YAML.

## Проверка: настоящий ответ и черновик

В PowerShell выполните (master key вводится скрыто):

```powershell
$gatewaySecret = Read-Host 'LITELLM_MASTER_KEY' -AsSecureString
$gatewayCredential = [PSCredential]::new('gateway', $gatewaySecret)
$gatewayHeaders = @{ Authorization = 'Bearer ' + $gatewayCredential.GetNetworkCredential().Password }
$requestBody = @{
  model = 'free-default'
  messages = @(@{ role = 'user'; content = 'Ответь одним словом: подключено' })
  max_tokens = 100
} | ConvertTo-Json -Depth 5
$result = Invoke-RestMethod http://localhost:4000/v1/chat/completions -Method Post -Headers $gatewayHeaders -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($requestBody))
$result.choices[0].message.content
Remove-Variable gatewaySecret, gatewayCredential, gatewayHeaders
```

Порт замените, если меняли `LITELLM_PORT`. Повторите запрос с нужными алиасами; `local` проверяйте только при установленной Ollama. В приложении создайте одну вакансию, заполните факты и получите черновик. Убедитесь в правильном языке, отсутствии вымышленных условий и успешном model QA. До этого не включайте AUTO.

Есть расширенная проверка `python scripts/test_litellm.py`: она требует Python на хосте, делает много модельных запросов, в том числе к `local`, и может расходовать баланс. Запускайте осознанно; это не обязательный шаг первой установки.

| Ошибка | Что проверить |
| --- | --- |
| 401 LiteLLM | В запросе внутренний master key, он совпадает с `.env` |
| 401/403 upstream | Ключ провайдера, его права и выбранный base URL |
| Model not found | ID в каталоге провайдера, префикс адаптера, установлена ли модель Ollama |
| 429 | Квота/лимит провайдера; не устраивайте бесконечные повторы |
| Connection refused | Шлюз запущен, адрес доступен из Docker |
| Пустой текст / timeout | Модель возвращает reasoning вместо текста, слишком мала память или бюджет; проверьте один короткий запрос |
| Вместо модели тестовый ответ | Отключены ли оба mock-переключателя, пересозданы ли backend и workers |

Источники: [LiteLLM compatible endpoints](https://docs.litellm.ai/docs/providers/openai_compatible), [OpenRouter quickstart](https://openrouter.ai/docs/quickstart), [Ollama quickstart](https://docs.ollama.com/quickstart), [Ollama FAQ](https://docs.ollama.com/faq).
