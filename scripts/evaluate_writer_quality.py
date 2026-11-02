"""Read-only, sequential native-language writing probes. Never saves posts."""
import argparse
import json
import time
import re
import urllib.request

CASES = {
    'uk': 'Напишіть короткий практичний допис до 400 символів: що запитати рекрутера про житло перед виїздом на роботу. Без вигаданих цін, емодзі, реклами та заклику писати у приват.',
    'pl': 'Napisz praktyczny post do 400 znaków: jakie pytania zadać o harmonogram zmian przed pracą na magazynie. Bez wymyślonych godzin, typu umowy, emoji i reklamy. Grafik oznacza rozkład dni i godzin pracy, nie projektanta.',
}
LANGUAGE_NAMES = {'uk': 'Ukrainian (українська мова)', 'pl': 'Polish (język polski)'}


def shape_failures(content, finish_reason, language):
    """Mechanical gate only; a clean shape is not native semantic acceptance."""
    failures = []
    if not content.strip(): failures.append('empty')
    if len(content) > 400: failures.append('over_limit')
    if finish_reason != 'stop': failures.append('not_completed')
    if re.search(r'<think>|</think>|^\s*(?:okay,? i need|let me think|first, i)', content, re.I):
        failures.append('reasoning_in_post')
    if re.search(r'@\w+|t\.me/|\btelegram\b', content, re.I): failures.append('forbidden_contact')
    if language == 'pl' and re.search(r'[\u0400-\u04ff]', content): failures.append('mixed_alphabet')
    if language in {'uk', 'ru'} and content.strip() and not re.search(r'[\u0400-\u04ff]', content):
        failures.append('wrong_language_alphabet')
    return failures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('models', nargs='+')
    parser.add_argument('--base-url', default='http://localhost:11434')
    args = parser.parse_args()
    failed = 0
    for model in args.models:
        for language, prompt in CASES.items():
            payload = dict(model=model, stream=False, think=False, keep_alive='1m',
                messages=[dict(role='system', content=f'Write strictly in {LANGUAGE_NAMES[language]}. You are a recruitment writer for warehouse and production workers in Poland. Use clear concrete questions, polite consistent address. Output only the post, no analysis or commentary.'),
                          dict(role='user', content=prompt)],
                options=dict(num_predict=250, num_ctx=4096, temperature=0.3, seed=42))
            request = urllib.request.Request(args.base_url.rstrip('/') + '/api/chat',
                data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'})
            started = time.monotonic()
            try:
                with urllib.request.urlopen(request, timeout=180) as response:
                    result = json.load(response)
                body = result.get('message', {}).get('content', '').strip()
                problems = shape_failures(body, result.get('done_reason'), language)
                failed += bool(problems)
                print(json.dumps(dict(model=model, language=language,
                    seconds=round(time.monotonic()-started, 1), finish=result.get('done_reason'),
                    chars=len(body), within_limit=0 < len(body) <= 400, content=body,
                    shape_failures=problems,
                    acceptance='requires_native_semantic_review'), ensure_ascii=False), flush=True)
            except Exception as exc:
                failed += 1
                print(json.dumps(dict(model=model, language=language, error_type=type(exc).__name__)), flush=True)
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
