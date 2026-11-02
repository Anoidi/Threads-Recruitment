"""Synthetic preview only: never creates drafts, jobs, schedules or publications."""
import asyncio
import json
import logging
import time

logging.disable(logging.CRITICAL)
from app.ai import AIService
from app.database import engine


async def main():
    results = []
    for language, question in [
        ("uk", "Як перевірити житло перед виїздом, щоб не дізнатися про погані умови вже на місці?"),
        ("pl", "O co zapytać rekrutera, żeby wiedzieć, jak naprawdę wygląda grafik zmian?"),
    ]:
        started = time.monotonic()
        try:
            ideas = await AIService().plan_content(
                vacancies=[], audience_questions=[(0, question)], trend_topics=[],
                editorial_analysis=None, language=language,
                brand_voice="Calm, practical recruiter. Respectful, specific, natural wording. Give the answer now. No emoji, no promotion, no private-message CTA.",
                count=1,
            )
            results.append({"language": language, "seconds": round(time.monotonic() - started, 2),
                            "ideas": ideas, "saved_posts": 0})
        except Exception as error:
            results.append({"language": language, "error_type": type(error).__name__, "saved_posts": 0})
    await engine.dispose()
    print(json.dumps(results, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
