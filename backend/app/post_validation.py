"""Deterministic checks for generated drafts and the final publish gate.

The model is allowed to choose wording, but it is never the source of vacancy
facts.  ``validate_generated_post`` is intentionally safe to use while asking
the model for a retry.  ``validate_post_for_publication`` is the stronger gate
used immediately before a Threads publish call.
"""
import re
import unicodedata
from difflib import SequenceMatcher

from fastapi import HTTPException

from .content_quality import structured_vacancy_facts, vacancy_fact_lines


_SERVICE_MARKER_RE = re.compile(
    r"\[(?:google-news-rss|source|internal|debug|system)[^\]]*\]|"
    r"(?<!\w)(?:no vacancy selected|source marker|internal only|todo|tbd|n/?a)\b",
    re.IGNORECASE,
)
_PLACEHOLDER_RE = re.compile(
    r"\b(?:не\s+вказано(?:\s+в\s+оголошенні)?|не\s+указано|не\s+зазначено|"
    r"not\s+specified|не\s+вказана|не\s+указан)\b",
    re.IGNORECASE,
)
_CURRENCY_DUPLICATE_RE = re.compile(
    r"\b(?:zł|zl|pln)\s*(?:/\s*(?:год|годину|hour|h|godz\.?)\b)?\s*"
    r"(?:zł|zl|pln)\b|\b(?:pln|zł|zl)\s*/\s*(?:год|годину|hour|h)\b\s*(?:pln|zł|zl)\b",
    re.IGNORECASE,
)
_PHONE_RE = re.compile(r"(?<!\w)\+?\d[\d ()-]{7,}\d")
_RATE_CANDIDATE_RE = re.compile(
    r"(?P<amount>\d+(?:[,.]\d+)?)\s*"
    r"(?P<currency>zł|zl|pln|зл|грн|€|eur)?\s*"
    r"(?P<basis>netto|net|brutto|gross|брутто|нетто)?\s*"
    r"(?:/\s*(?:годину|год|hour|h|godz\.?)\b|за\s+годину)"
    r"(?:\s*(?P<currency_after>zł|zl|pln|зл|грн|€|eur))?",
    re.IGNORECASE,
)


def _normalise_currency(value: str | None) -> str:
    return {"zł": "pln", "zl": "pln", "зл": "pln", "€": "eur"}.get((value or "").casefold(), (value or "").casefold())


def _rate_facts(text: str) -> set[tuple[str, str, str | None]]:
    result: set[tuple[str, str, str | None]] = set()
    for match in _RATE_CANDIDATE_RE.finditer(text or ""):
        currency = _normalise_currency(match.group("currency") or match.group("currency_after"))
        # A rate without a currency can be valid only when the vacancy source
        # itself uses a currency-less rate.  Keep it visible for comparison.
        amount = match.group("amount").replace(",", ".")
        basis = (match.group("basis") or "").casefold()
        basis = {"netto": "net", "net": "net", "нетто": "net", "brutto": "gross", "gross": "gross", "брутто": "gross"}.get(basis) or None
        result.add((amount, currency, basis))
    return result


def _source_rate_facts(vacancy) -> set[tuple[str, str, str | None]]:
    facts = structured_vacancy_facts(vacancy)
    source = "\n".join(value for value in (facts.salary, facts.student_salary) if value)
    return _rate_facts(source)


_LOCATION_ALIASES = {
    "краків": ("краків", "кракові"),
    "кракові": ("краків", "кракові"),
    "krakow": ("краків", "кракові", "кракові"),
    "kraków": ("краків", "кракові", "кракові"),
    "warszawa": ("варшава", "варшаві"),
    "wroclaw": ("вроцлав", "вроцлаві"),
    "wrocław": ("вроцлав", "вроцлаві"),
    "poznan": ("познань", "познані"),
    "poznań": ("познань", "познані"),
    "gdansk": ("гданськ", "гданську"),
    "gdańsk": ("гданськ", "гданську"),
    "lodz": ("лодзь", "лодзі"),
    "łódź": ("лодзь", "лодзі"),
}


def _fold_location(value: str) -> str:
    value = unicodedata.normalize("NFKD", value.casefold())
    return "".join(char for char in value if not unicodedata.combining(char))


def _location_appears(location: str, text: str) -> bool:
    folded_location = _fold_location(location)
    folded_text = _fold_location(text)
    if folded_location and folded_location in folded_text:
        return True
    aliases = _LOCATION_ALIASES.get(location.casefold(), ())
    return any(_fold_location(alias) in folded_text for alias in aliases)


def validate_post_for_publication(*, content: str, language: str, vacancy=None, content_type=None, account_contact=None) -> str:
    """Validate the exact text that is about to be sent to Threads.

    This function deliberately does not run when a draft is saved.  It raises
    an HTTPException so API and worker callers can expose the same actionable
    diagnostic and, importantly, stop the external request.
    """
    text = (content or "").strip()
    if not text:
        raise HTTPException(422, "Публикация не может быть пустой")
    if len(text) > 500:
        raise HTTPException(422, "Публикация превышает лимит Threads в 500 символов")
    if _PLACEHOLDER_RE.search(text):
        raise HTTPException(422, "Публикация содержит незаполненный шаблон вакансии")
    if _SERVICE_MARKER_RE.search(text):
        raise HTTPException(422, "Публикация содержит служебный маркер или внутреннее значение")
    if _CURRENCY_DUPLICATE_RE.search(text):
        raise HTTPException(422, "Публикация дублирует валюту или единицу ставки")
    if re.search(r"(?:t\.me/|telegram\.me/|@\w+|\btelegram\b)", text, re.IGNORECASE):
        raise HTTPException(422, "Публикация содержит запрещённый Telegram-контакт")
    if language == "uk":
        russian_markers = r"\b(?:соискател\w*|отклик\w*|сравните|стоимость жилья|напишите|первым|жиль[её]|график)\b"
        if re.search(russian_markers, text.casefold()):
            raise HTTPException(422, "Украинская публикация содержит русскую формулировку")
    if vacancy is None:
        return text

    source_rates = _source_rate_facts(vacancy)
    post_rates = _rate_facts(text)
    if any(rate not in source_rates for rate in post_rates):
        raise HTTPException(422, "Публикация содержит неподтверждённую ставку")
    kind = str(getattr(content_type, "value", content_type or "")).upper()
    source_facts = structured_vacancy_facts(vacancy)
    if kind == "VACANCY":
        # An incomplete vacancy may remain a visible draft for operator
        # editing, but it must never cross the final external publish gate.
        if not source_facts.location:
            raise HTTPException(422, "Вакансия не готова к публикации: у підтверджених даних немає міста")
        if not source_facts.salary:
            raise HTTPException(422, "Вакансия не готова к публикации: не подтверждена ставка")
        if source_facts.location and not _location_appears(source_facts.location, text):
            raise HTTPException(422, "Публикация вакансии не содержит подтверждённый город")
    # A human may intentionally save a short, non-vacancy editorial text while
    # keeping it linked to a vacancy for attribution.  We therefore reject
    # *new* factual claims, but do not force every confirmed field into every
    # hand-written draft.  The deterministic vacancy renderer and provocative
    # mode include the relevant rates and conditions themselves.
    source_text = "\n".join(vacancy_fact_lines(vacancy))
    low = text.casefold()
    source_low = source_text.casefold()
    for label, markers in {
        "жильё": ("житло", "жиль", "housing", "zakwaterowanie", "mieszkanie"),
        "транспорт": ("транспорт", "доїзд", "дойзд", "dojazd", "dowóz", "transport"),
        "график": ("графік", "график", "schedule", "harmonogram", "grafik"),
        "договор": ("договір", "договор", "contract", "umowa"),
    }.items():
        if any(marker in low for marker in markers) and not any(marker in source_low for marker in markers):
            raise HTTPException(422, f"Публикация добавляет неподтверждённое условие: {label}")
    source_phone = structured_vacancy_facts(vacancy).contact
    post_phone = next(iter(_PHONE_RE.findall(text)), None)
    contacts = {re.sub(r"\D", "", str(value)) for value in (source_phone, account_contact) if value}
    if post_phone and re.sub(r"\D", "", post_phone) not in contacts:
        raise HTTPException(422, "Публикация содержит неподтверждённый номер телефона")
    return text


def validate_generated_post(data):
    for key in ('hook', 'content', 'reasoning_summary'):
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise HTTPException(502, f"AI response is missing the '{key}' field")
    content = data['content'].strip()
    if data.get('language') in {'uk', 'ru'} and not re.search(r'[\u0400-\u04ff]', content):
        raise HTTPException(502, 'Draft has no Cyrillic text for requested Ukrainian/Russian language')
    if data.get('language') == 'uk':
        # Cyrillic alone is insufficient: reject common Russian-only forms so
        # a fallback or weak model cannot be stored as Ukrainian content.
        russian_markers = r'\b(?:соискател\w*|отклик\w*|сравните|точн(?:ую|ые) ставк\w*|стоимость жилья|напишите|первым|жиль[её]|график)\b'
        if re.search(russian_markers, content.casefold()):
            raise HTTPException(502, 'Draft contains Russian wording for requested Ukrainian language')
    if data.get('language') == 'pl' and re.search(r'[\u0400-\u04ff]|\butočnij\b', content + data['hook'], re.I):
        raise HTTPException(502, 'Polish draft contains non-Polish alphabet characters; use native Polish wording')
    if len(content) > 500:
        raise HTTPException(502, 'AI draft exceeds 500 characters; do not truncate facts')
    if re.search(r'\b(?:не вказано|не указано|не зазначено|not specified|не вказана|не указан)\b', content.casefold()):
        raise HTTPException(502, 'AI draft contains an unresolved vacancy placeholder')
    if re.search(r'\b(?:zł|zl|pln)\s*/\s*(?:год|годину|hour)\s+(?:zł|zl|pln)\b', content, re.I):
        raise HTTPException(502, 'AI draft duplicates the currency or unit')
    if re.search(r'@\w+|t\.me/|telegram\.me/|\btelegram\b', content + '\n' + data['hook'], re.I):
        raise HTTPException(502, 'AI draft contains a forbidden Telegram contact or handle')
    content = content.replace(' — ', ', ').replace('—', ',')
    lines = content.splitlines()
    if len(lines) > 1 and lines[0].strip() == lines[1].strip():
        raise HTTPException(502, 'AI draft repeats its opening')
    hook = data['hook'].strip().replace(' — ', ', ').replace('—', ',')
    if len(hook) > 500:
        raise HTTPException(502, 'AI hook exceeds 500 characters')
    # Only unambiguous direct-address forms; quoted candidate speech is exempt.
    address_text = re.sub(r'«[^»]*»|"[^"]*"', '', hook + '\n' + content).lower()
    informal = re.search(r'\b(?:ти|ты|запитай|уточни|спроси|попроси|тобі|тебе)\b', address_text)
    formal = re.search(r'\b(?:ви|вы|запитайте|уточніть|спросите|попросіть|вам)\b', address_text)
    if informal and formal:
        raise HTTPException(502, 'AI draft mixes informal and polite forms of address across hook and body')
    # Hook is display/search metadata; publishing sends content alone. Derive
    # it from the actual opening rather than trusting an independent slogan.
    opening = content.splitlines()[0].strip()
    hook = re.split(r'(?<=[.!?])\s+', opening, maxsplit=1)[0]
    return {**data, 'content': content, 'hook': hook}


# ---------------------------------------------------------------------------
# Near-duplicate detection. Everything else in this module is deterministic,
# so "did we already say this" must be deterministic as well: an exact or
# near-exact repeat of an active post trained the audience that the account
# repeats itself, and it also breaks the honesty of the measurements.
#
# The 0.78 threshold was calibrated on the live failure the owner reported:
# published pairs measured 1.00 (wage), 0.97 (contract), 0.92 (housing),
# 0.87/0.85 (experiment round 2 vs round 1), while genuinely different posts
# of the same feed stayed below 0.63.

NEAR_DUPLICATE_RATIO = 0.78
NEAR_DUPLICATE_OPENING_RATIO = 0.55
# A format experiment deliberately repeats one confirmed fact across its
# rounds, so an experiment post only counts against *unrelated* content when
# it is a near-exact copy.
RELAXED_DUPLICATE_RATIO = 0.90


def normalized_for_similarity(text: str) -> str:
    """Case-folded, whitespace-collapsed text used for all comparisons."""
    return " ".join((text or "").split()).casefold()


def text_similarity(candidate: str, existing: str) -> float:
    """Similarity of two texts in ``[0, 1]`` after normalisation."""
    a = normalized_for_similarity(candidate)
    b = normalized_for_similarity(existing)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def _opening_of(normalized: str) -> str:
    """The first sentence of an already normalized text."""
    match = re.search(r"[.!?]", normalized)
    return normalized[:match.end()] if match else normalized


def duplicate_gate_mode(candidate: str, existing: str, *, relaxed: bool = False) -> str | None:
    """``IDENTICAL`` / ``NEAR`` / ``None`` for one candidate-existing pair.

    The verdict is text-only and never calls a model: the same two texts must
    always produce the same verdict, exactly like the rest of this gate.
    """
    a = normalized_for_similarity(candidate)
    b = normalized_for_similarity(existing)
    if not a or not b:
        return None
    if a == b:
        return "IDENTICAL"
    ratio = SequenceMatcher(None, a, b).ratio()
    if relaxed:
        return "NEAR" if ratio >= RELAXED_DUPLICATE_RATIO else None
    if ratio >= NEAR_DUPLICATE_RATIO:
        return "NEAR"
    # The same opening with a mostly shared body is the same post in a
    # different costume even without a high overall ratio.
    if ratio >= NEAR_DUPLICATE_OPENING_RATIO and _opening_of(a) == _opening_of(b):
        return "NEAR"
    return None
