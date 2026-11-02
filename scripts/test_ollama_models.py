"""Probe local Ollama models for short, non-thinking answers.

Usage: python scripts/test_ollama_models.py [base_url]
Runs a short-post prompt in uk/pl/ru/en against each configured model and
reports whether content is non-empty, whether leaked reasoning appears, and
the finish reason. Never prints secrets.
"""
from __future__ import annotations

import json
import sys
import urllib.request

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:11434"
MODELS = ["qwen3:4b", "qwen3:4b-instruct"]
PROMPTS = {
    "uk": "Напиши один короткий пост для Threads про вакансію: зварювальник, Краків, 6500 злотих, житло надається. Максимум 50 слів. Поверни лише текст поста.",
    "pl": "Napisz jeden krótki post na Threads o ofercie: spawacz, Kraków, 6500 zł, zakwaterowanie zapewnione. Maksymalnie 50 słów. Zwróć tylko treść posta.",
    "ru": "Напиши один короткий пост для Threads про вакансию: сварщик, Краков, 6500 злотых, жильё предоставляется. Максимум 50 слов. Верни только текст поста.",
    "en": "Write one short Threads post about this vacancy: welder, Krakow, 6500 PLN, housing provided. Max 50 words. Return only the post text.",
}
SYSTEM = "You are a concise recruitment copywriter. Answer in the user's language. Output only the final post text: no preamble, no reasoning, no meta commentary."


def chat(model: str, lang: str, think: bool, num_predict: int = 300) -> dict:
    payload = json.dumps(
        {
            "model": model,
            "stream": False,
            "think": think,
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": PROMPTS[lang]},
            ],
            "options": {"num_predict": num_predict, "temperature": 0.7},
        }
    ).encode("utf-8")
    req = urllib.request.Request(f"{BASE}/api/chat", data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as resp:
        return json.loads(resp.read().decode("utf-8"))


def looks_like_reasoning(text: str) -> bool:
    lowered = text.lower()
    markers = ("спочатку", "думаю", "考えて", "let's think", "first, i", "помітно", "надо включить", "хорошо, мне нужно")
    return any(m in lowered for m in markers)


def main() -> int:
    tags = json.loads(urllib.request.urlopen(f"{BASE}/api/tags", timeout=10).read().decode("utf-8"))
    available = {m["name"] for m in tags.get("models", [])}
    print("available models:", ", ".join(sorted(available)))
    failures = 0
    for model in MODELS:
        if model not in available:
            print(f"[skip] {model} not present locally")
            continue
        for think in (False, True):
            for lang in PROMPTS:
                try:
                    data = chat(model, lang, think)
                except Exception as exc:  # noqa: BLE001
                    print(f"[error] {model} think={think} {lang}: {type(exc).__name__}: {exc}")
                    failures += 1
                    continue
                msg = data.get("message", {})
                content = (msg.get("content") or "").strip()
                reasoning = bool(msg.get("thinking"))
                flag = "OK " if content and not looks_like_reasoning(content) else "BAD"
                if flag == "BAD":
                    failures += 1
                print(
                    f"[{flag}] {model} think={think} lang={lang} done={data.get('done_reason')} "
                    f"evals={data.get('eval_count')} len={len(content)} reasoning_field={reasoning} "
                    f"leak={looks_like_reasoning(content)} head={content[:90]!r}"
                )
    print("FAILURES:", failures)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
