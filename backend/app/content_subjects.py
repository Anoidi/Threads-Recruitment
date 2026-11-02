"""Deterministic subject detection for content diversity.

Why this exists
---------------
Two posts that say the same thing in different words are the same post. The
exact-text duplicate check cannot see that: a plan produced six posts about
the student rate and the housing deduction, each with a different wording,
and every one of them passed the duplicate check.

Every post is therefore reduced to the set of subjects it covers and to one
primary subject. The planner is told which subjects are already covered, and
the worker refuses two posts that share the same primary subject.

The detection is deliberately keyword based. It never invents a fact; it only
reads which condition a sentence talks about.
"""
from __future__ import annotations

import re

# Order matters: the first match wins as the primary subject. A post that
# explains a housing deduction is a housing post even when it opens with a
# rate.
SUBJECTS = (
    "HOUSING",
    "PAY",
    "SCHEDULE",
    "CONTRACT",
    "CITY",
    "TRANSPORT",
    "REQUIREMENTS",
    "DOCUMENTS",
    "JOB_SEARCH",
    "GENERAL",
)

_HOUSING_DEDUCTION = re.compile(
    r"(вираховується|утримується|вирахован|удержання|за\s+вирахуванням|"
    r"після\s+вирахування|вычитается|удерживается|deducted)",
    re.I,
)
_HOUSING = re.compile(
    r"(житл|жиль[еёя]|хостел|hostel|housing|zakwaterowanie|mieszkanie|квартир)",
    re.I,
)
_PAY = re.compile(
    r"(студент|student|ucze|udt|wjo|дозвіл|зарплат|оплат|pay|salary|"
    r"ставк|wynagrodz)",
    re.I,
)
_RATE = re.compile(
    r"\d+(?:[,.]\d+)?\s*(?:zł|zl|зл|pln|грн|€|eur)|"
    r"(?:zł|zl|зл|pln)\s*/\s*(?!міс|month|mes|kwot)|"
    r"(?:gross|net|netto|brutto)\s*/\s*hour",
    re.I,
)
_SCHEDULE = re.compile(
    r"(графік|график|schedule|harmonogram|zmian|змін|зміну|годин|hours|зміни)",
    re.I,
)
_CONTRACT = re.compile(r"(договір|договор|umowa|contract)", re.I)
_CITY = re.compile(
    r"(місто|місце\s+роботи|локація|location|краків|краков|вроцлав|катовіце|"
    r"вічліц|krakow|wrocław|katowice|krzyżowice|czechowice|mintH)",
    re.I,
)
_TRANSPORT = re.compile(r"(транспорт|доїзд|дойзд|dojazd|dowóz|потяг|автобус)", re.I)
_REQUIREMENTS = re.compile(r"(вимог|требован|wymagan|досвід|doświadcze)", re.I)
_DOCUMENTS = re.compile(r"(документ|document|dokument|посвідчення|довідк|справк)", re.I)
# The same markers the community classifier uses, so a question about looking
# for work is recognised as such.
_JOB_SEARCH = re.compile(
    r"(шукаю\s+роботу|ищу\s+работу|szukam\s+pracy|можу\s+вийти|могу\s+вийти|"
    r"хочу\s+працювати|хочу\s+работать)",
    re.I,
)

# The stored classification is coarse but semantically reliable, so it is
# mapped onto the same taxonomy.
CLASSIFICATION_THEME = {
    "HOUSING_QUESTION": "HOUSING",
    "SALARY_QUESTION": "PAY",
    "SCHEDULE_QUESTION": "SCHEDULE",
    "DOCUMENTS_QUESTION": "DOCUMENTS",
    "JOB_SEARCH": "JOB_SEARCH",
    "COMPLAINT": "COMPLAINT",
}


def detect_subjects(text: str) -> set[str]:
    """Return every subject the post talks about."""
    value = text or ""
    found: set[str] = set()
    if _HOUSING_DEDUCTION.search(value) or _HOUSING.search(value):
        found.add("HOUSING")
    if _PAY.search(value) or _RATE.search(value):
        found.add("PAY")
    if _SCHEDULE.search(value):
        found.add("SCHEDULE")
    if _CONTRACT.search(value):
        found.add("CONTRACT")
    if _CITY.search(value):
        found.add("CITY")
    if _TRANSPORT.search(value):
        found.add("TRANSPORT")
    if _REQUIREMENTS.search(value):
        found.add("REQUIREMENTS")
    if _DOCUMENTS.search(value):
        found.add("DOCUMENTS")
    if _JOB_SEARCH.search(value):
        found.add("JOB_SEARCH")
    if not found:
        found.add("GENERAL")
    return found


def theme_of(text: str, classification: str | None = None) -> str:
    """Primary subject of a message, using the stored classification as backup.

    The keyword classifier marks most real comments as OTHER. The text
    detection is finer, and the stored classification only fills in a theme
    the text did not reveal.
    """
    found = detect_subjects(text)
    mapped = CLASSIFICATION_THEME.get((classification or "").upper())
    if mapped and mapped not in found:
        found.add(mapped)
    for subject in SUBJECTS:
        if subject in found:
            return subject
    return "GENERAL"


def primary_subject(text: str) -> str:
    """Return the subject the post is mainly about."""
    found = detect_subjects(text)
    for subject in SUBJECTS:
        if subject in found:
            return subject
    return "GENERAL"


def subjects_of(texts) -> list[str]:
    """Primary subjects for a list of texts, in order, without duplicates."""
    result: list[str] = []
    for text in texts:
        subject = primary_subject(text)
        if subject not in result:
            result.append(subject)
    return result
