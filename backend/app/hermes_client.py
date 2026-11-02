from __future__ import annotations

import json
import uuid

import httpx

from .config import settings

MAX_RESPONSE_BYTES = 128_000


class HermesError(RuntimeError):
    """The Hermes gateway could not complete a task. Safe to fall back from."""


async def ask(task: str, *, system: str | None = None, max_tokens: int = 600) -> str:
    """Send one orchestration task to the Hermes Agent gateway.

    Hermes reaches LiteLLM (and through it the local Ollama model) using its own
    seeded configuration, so a single call exercises the whole
    backend -> Hermes -> LiteLLM -> Ollama chain. Business rules and write
    authorization always stay with this application.
    """
    messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": task}]
    payload = {
        "model": settings.hermes_model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.4,
    }
    headers = {"Authorization": f"Bearer {settings.hermes_api_key}"} if settings.hermes_api_key else {}
    # Without this header Hermes derives a stable session id from the prompt and
    # keeps appending to it; history then overflows the local model context.
    # A fresh id per task keeps every call independent and bounded.
    headers["X-Hermes-Session-Id"] = f"freebuff-{uuid.uuid4().hex[:16]}"
    url = f"{settings.hermes_base_url.rstrip('/')}/v1/chat/completions"
    try:
        async with httpx.AsyncClient(timeout=settings.hermes_timeout_seconds) as client:
            response = await client.post(url, json=payload, headers=headers)
    except httpx.TimeoutException as exc:
        raise HermesError(f"Hermes did not answer within {settings.hermes_timeout_seconds}s") from exc
    except httpx.HTTPError as exc:
        raise HermesError(f"Hermes gateway unreachable: {type(exc).__name__}") from exc
    if len(response.content) > MAX_RESPONSE_BYTES:
        raise HermesError("Hermes response exceeded the 128 KB safety limit")
    if response.status_code >= 400:
        raise HermesError(f"Hermes returned HTTP {response.status_code}: {response.text[:200]}")
    try:
        choice = response.json()["choices"][0]
        content = choice["message"]["content"]
        finish = choice.get("finish_reason") or ""
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise HermesError("Hermes returned an unexpected response shape") from exc
    content = (content or "").strip()
    if not content:
        raise HermesError(f"Hermes returned an empty answer (finish_reason={finish or 'unknown'})")
    if finish == "error":
        # Hermes reports agent-turn failures (context overflow, tool errors) as
        # normal content with finish_reason=error.
        raise HermesError(f"Hermes agent turn failed: {content[:300]}")
    return content


def parse_json(text: str) -> dict:
    """Parse a Hermes reply into an object, tolerating fences and stray prose."""
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = candidate.strip("`")
        if candidate.lstrip().lower().startswith("json"):
            candidate = candidate.lstrip()[4:]
    start, end = candidate.find("{"), candidate.rfind("}")
    if start != -1 and end > start:
        candidate = candidate[start : end + 1]
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise HermesError(f"Hermes returned invalid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise HermesError("Hermes returned JSON that is not an object")
    return parsed
