"""Select useful anonymized audience questions for NEW drafts only."""
from collections import Counter
import re

from .audience_profile import is_question
from .content_subjects import theme_of


def anonymize_question(text: str) -> str:
    text = re.sub(r"https?://\S+|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|@\w+", "[contact]", text)
    text = re.sub(r"(?<!\w)\+?\d[\d ()-]{7,}\d(?!\w)", "[contact]", text)
    return " ".join(text.split())[:400]


def select_audience_questions(rows, limit: int = 5) -> list[tuple[int, str]]:
    """Prefer unanswered, repeated needs; keep caller's newest-first order on ties."""
    questions = [row for row in rows if not row.ignored and is_question(row.message or "", row.classification)]
    frequency = Counter(theme_of(row.message, row.classification) for row in questions)
    questions.sort(key=lambda row: (bool(row.answered), -frequency[theme_of(row.message, row.classification)]))
    selected, seen = [], set()
    for row in questions:
        text = anonymize_question(row.message or "")
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        selected.append((row.id, text))
        if len(selected) >= limit:
            break
    return selected
