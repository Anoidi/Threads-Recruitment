# LiteLLM gateway

New installation: follow [AI setup in Russian](../docs/AI_RU.md) for cloud,
Ollama or your own compatible gateway. The OmniRoute addresses and model IDs
below describe the original installation, not services bundled with this repo.

Application code addresses only these stable aliases: `free-fast`, `free-default`,
`free-reasoning`, `free-coder`, `free-long-context`, `hermes-agent`, and
`local` (the reserve).

## Primary route: the local OmniRoute gateway

Every alias except `local` resolves through the local OmniRoute instance,
which exposes an OpenAI-compatible `/v1` API and routes across a dozen
upstream providers.

```dotenv
OMNIROUTE_BASE_URL=http://host.docker.internal:20128/v1
OMNIROUTE_API_KEY=<ключ OmniRoute>
OMNIROUTE_MODEL_FAST=openai/antigravity/gemini-3.1-flash-lite
OMNIROUTE_MODEL_DEFAULT=openai/antigravity/gemini-3-flash
OMNIROUTE_MODEL_REASONING=openai/antigravity/gemini-3.8-flash-high
```

The model ids are explicit on purpose.

- **The `auto/*` aliases of OmniRoute are not used.** They pick a model per
  request, and the answer language follows that model: the same Ukrainian
  recruitment prompt produced a Polish answer from `auto/best-reasoning` and
  an English answer from `auto/best-free`. The publish gate rejects a
  non-Ukrainian draft, so an unpredictable language is a content outage.
- **The reasoning levels are not used.** `gemini-3.8-flash-low` spent 565 of
  696 output tokens on reasoning and returned no text at all.
- **The Claude variants answered with an empty body** and
  `finish_reason=length`.

Measured on a Ukrainian recruitment prompt: `gemini-3.1-flash-lite` 1.6 s,
`gemini-3-flash` 1.7 s, `gemini-3.8-flash-high` 2.8 s — valid Ukrainian, no
reasoning tokens. The same prompt on the local 4B model needed 90–130 s. The
daily content plan went from 176 s to 13 s.

## Reserve: the local model

`local` points at the verified Ollama tag `qwen3:4b-instruct` on the host.
It is the last link of every fallback chain:

```
free-fast     -> [free-default, local]
free-default  -> [free-reasoning, local]
free-reasoning-> [free-default, local]
```

If the gateway, its credentials or its upstream providers run out, the
content pipeline keeps working on the machine's own model. The local model is
slow on a planner-sized prompt (90–130 s), so the gateway timeouts are set
accordingly: the cloud routes get 45–60 s, the local route 180 s, and the
gateway request timeout 260 s.

Why `qwen3:4b-instruct` and not plain `qwen3:4b`:

- `qwen3:4b` is a hybrid thinking model. It spends the whole token budget on
  reasoning, so `message.content` comes back empty with `finish_reason=length`.
- `qwen3:4b-instruct` is an official Ollama library tag. It answers in
  uk/pl/ru/en, always returns non-empty content and terminates with
  `finish_reason=stop`.

## Mock mode

`config.mock.yaml` returns a deterministic response and makes no provider
network calls. Switch with:

```dotenv
AI_MOCK_MODE=true
LITELLM_CONFIG_FILE=/app/config.mock.yaml
```

Then recreate the gateway:

```powershell
docker compose up -d --force-recreate litellm
```

## Pointing a route at a different provider

Each alias reads its own model id and endpoint, so a route can be moved
without touching the YAML. The `openai/` prefix tells LiteLLM to speak the
OpenAI-compatible protocol; any OpenAI-compatible endpoint works, including
another local gateway or a cloud provider.

The gateway requires the `LITELLM_MASTER_KEY` bearer token on every request
except the health endpoints. Never put a provider key in either YAML file.

`scripts/test_litellm.py` runs the end-to-end checks (four languages, every
alias, the backend JSON contract) against a running stack:

```bash
python scripts/test_litellm.py
```
