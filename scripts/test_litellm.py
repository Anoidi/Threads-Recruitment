"""End-to-end tests for LiteLLM -> Ollama local inference.

Usage: python scripts/test_litellm.py [base_url]
Reads LITELLM_MASTER_KEY from .env (never printed). Verifies:
  * every logical alias returns non-empty content with finish_reason=stop
  * uk / pl / ru / en prompts all produce usable text
  * the JSON contract used by backend/app/ai.py parses successfully
"""
from __future__ import annotations

import json
import pathlib
import sys
import time
import urllib.error
import urllib.request

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:4000").rstrip("/")
REPO = pathlib.Path(__file__).resolve().parents[1]


def master_key() -> str:
    env = (REPO / ".env")
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith("LITELLM_MASTER_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("LITELLM_MASTER_KEY not found in .env")


KEY = master_key()
FACTS = {
    "title": "Працівник складу",
    "location": "Kraków",
    "salary": "30.50 gross/hour",
    "currency": "PLN",
    "schedule": "2 shifts",
    "housing": "Optional shared housing",
    "transport": "Company transport from selected stops",
    "requirements": "Adult applicants; readiness for shift work",
}
PROMPTS = {
    "uk": "Напиши короткий пост Threads про вакансію з цих фактів. До 60 слів. Лише текст поста.",
    "pl": "Napisz krótki post Threads o tej ofercie pracy. Maksymalnie 60 słów. Tylko treść posta.",
    "ru": "Напиши короткий пост Threads про эту вакансию. До 60 слов. Только текст поста.",
    "en": "Write a short Threads post about this vacancy. Max 60 words. Post text only.",
}


def completion(model: str, prompt: str, max_tokens: int, response_format: dict | None = None, timeout: int = 150) -> tuple[dict, float]:
    payload: dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": "Ти — рекрутер. Відповідай мовою користувача. Використовуй лише надані факти, нічого не вигадуй. Виводь лише готовий текст."},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": max_tokens,
    }
    if response_format:
        payload["response_format"] = response_format
    req = urllib.request.Request(
        f"{BASE}/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {KEY}"},
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"HTTP {exc.code}: {body}") from exc
    return data, time.monotonic() - started


def main() -> int:
    failures: list[str] = []

    def check(label: str, ok: bool, detail: str = "") -> None:
        print(f"[{'OK ' if ok else 'BAD'}] {label} {detail}")
        if not ok:
            failures.append(label)

    facts_block = "\n".join(f"- {k}: {v}" for k, v in FACTS.items())

    # 1. Plain generation in four languages through free-default.
    for lang, prompt in PROMPTS.items():
        try:
            data, took = completion("free-default", f"{facts_block}\n\n{prompt}", 500)
        except Exception as exc:  # noqa: BLE001
            check(f"free-default/{lang}", False, str(exc))
            continue
        choice = data["choices"][0]
        content = (choice["message"].get("content") or "").strip()
        reasoning = len(choice["message"].get("reasoning_content") or "")
        check(
            f"free-default/{lang}",
            bool(content) and choice["finish_reason"] == "stop",
            f"finish={choice['finish_reason']} len={len(content)} reasoning={reasoning} took={took:.1f}s head={content[:60]!r}",
        )

    # 2. Other aliases answer and terminate.
    for alias in ("free-fast", "free-reasoning", "free-coder", "free-long-context"):
        try:
            data, took = completion(alias, f"{facts_block}\n\n{PROMPTS['en']}", 400)
        except Exception as exc:  # noqa: BLE001
            check(alias, False, str(exc))
            continue
        choice = data["choices"][0]
        content = (choice["message"].get("content") or "").strip()
        check(alias, bool(content) and choice["finish_reason"] == "stop", f"finish={choice['finish_reason']} len={len(content)} took={took:.1f}s")

    # 3. Exact JSON contract used by backend/app/ai.py.
    json_prompt = (
        "Return a JSON object with keys hook, content, reasoning_summary for this vacancy. "
        f"Facts:\n{facts_block}\nLanguage: uk. Tone: professional. CTA: Напишіть нам у приватні повідомлення."
    )
    for use_format in (True, False):
        try:
            data, took = completion(
                "free-default",
                json_prompt,
                800,
                response_format={"type": "json_object"} if use_format else None,
            )
        except Exception as exc:  # noqa: BLE001
            check(f"json_contract/format={use_format}", False, str(exc))
            continue
        choice = data["choices"][0]
        raw = (choice["message"].get("content") or "").strip()
        parsed = None
        if raw:
            candidate = raw[raw.find("{"): raw.rfind("}") + 1] if not raw.startswith("{") else raw
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                parsed = None
        ok = bool(parsed) and all(k in parsed for k in ("hook", "content", "reasoning_summary"))
        check(
            f"json_contract/format={use_format}",
            ok,
            f"finish={choice['finish_reason']} took={took:.1f} keys={sorted(parsed) if parsed else None} head={raw[:80]!r}",
        )

    print("FAILURES:", len(failures))
    for name in failures:
        print("  -", name)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
