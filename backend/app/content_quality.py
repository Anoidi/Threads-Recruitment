from __future__ import annotations

import re
from decimal import Decimal
from dataclasses import dataclass
from typing import Any

from .models import Vacancy

THREADS_LIMIT = 500
AUTO_POST_MIN_CHARS = 160
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_PHONE_RE = re.compile(r"(?<!\w)(?:\+?\d[\d ()-]{7,}\d)")
_TELEGRAM_RE = re.compile(r"(?:https?://)?(?:t\.me|telegram\.me)/\S+|(?<!\w)@[A-Za-z0-9_]{4,32}\b", re.I)
_STUDENT_RE = re.compile(r"студент|student|ucze[nń]|dla\s+student", re.I)
_RATE_RE = re.compile(r"\d+(?:[,.]\d+)?(?:\s*[–—-]\s*\d+(?:[,.]\d+)?)?\s*(?:zł|zl|зл|pln|грн|€|eur)\s*(?:/\s*(?:годину|год|godz\.?|hour|h)\b|за\s+годину)", re.I)


def hourly_rate_signatures(text: str) -> set[tuple]:
    """Compare explicit hourly amounts across language/decimal formatting changes."""
    rates = set()
    for match in _RATE_RE.finditer(text):
        value = match.group(0).lower()
        amounts = tuple(Decimal(number.replace(',', '.')) for number in re.findall(r'\d+(?:[,.]\d+)?', value))
        currency = re.search(r'zł|zl|зл|pln|грн|€|eur', value).group(0)
        currency = {'zł': 'pln', 'zl': 'pln', 'зл': 'pln', '€': 'eur'}.get(currency, currency)
        rates.add((amounts, currency))
    return rates


def hourly_rate_conditions(text: str) -> set[tuple]:
    """Check explicit student/net/gross labels; ambiguous clauses require review."""
    result = set()
    # Decimal dots are not sentence separators. A semicolon/newline also
    # separates eligibility; free-form multiple rates in one clause are unsafe.
    parse_text = re.sub(r'(/\s*godz)\.', r'\1', text, flags=re.I)
    clauses = re.split(r'[;\n]|(?<=[.!?])\s+', parse_text)
    for clause in clauses:
        matches = list(_RATE_RE.finditer(clause))
        if not matches:
            continue
        if len(matches) > 1:
            raise ValueError('Put each hourly rate and its eligibility in a separate sentence or semicolon-delimited clause')
        student = bool(_STUDENT_RE.search(clause))
        if student and re.search(r'не\s+студент|not\s+(?:a\s+)?student|nie\s+(?:jest|są).*student|без\s+статус', clause, re.I):
            raise ValueError('Student eligibility is ambiguous; operator review required')
        net = bool(re.search(r'\b(?:netto|net|нетто)\b', clause, re.I))
        gross = bool(re.search(r'\b(?:brutto|gross|брутто)\b', clause, re.I))
        if net and gross:
            raise ValueError('Net/gross basis is ambiguous; operator review required')
        for signature in hourly_rate_signatures(clause):
            result.add((signature, student, 'net' if net else 'gross' if gross else None))
    return result


def _line_value(text: str, labels: str) -> str | None:
    match = re.search(rf"(?im)^[ \t]*(?:[^\w\n]{{0,3}})[ \t]*(?:{labels})[ \t]*[:—-][ \t]*(.+?)[ \t]*$", text)
    return match.group(1).strip()[:1000] if match else None


def _clean(value: str | None) -> str | None:
    if not value:
        return None
    value = re.sub(r"\s+", " ", value).strip(" •\t")
    return value or None


def _phone(text: str) -> str | None:
    for line in (text or '').splitlines():
        labelled = bool(re.search(r'телефон|контакт|phone|contact|telefon|viber|whatsapp|☎', line, re.I))
        for match in _PHONE_RE.finditer(line):
            value = match.group(0).strip()
            digits = re.sub(r'\D', '', value)
            international = value.startswith('+')
            standalone = line.strip() == value
            if international:
                valid = 8 <= len(digits) <= 15
            else:
                valid = len(digits) == 9 or (len(digits) == 11 and digits.startswith('48'))
                # Short numeric ranges are shifts/ages, not phone groups.
                if '-' in value and any(len(group.strip()) < 3 for group in value.split('-')):
                    valid = False
            if valid and (international or labelled or standalone):
                return re.sub(r'\s+', ' ', value)
    return None


def _rate_lines(text: str) -> tuple[list[str], list[str]]:
    regular: list[str] = []
    student: list[str] = []
    category = "regular"
    block_basis = None
    for raw in (text or "").splitlines():
        line = _clean(raw)
        if not line:
            continue
        low = line.lower()
        if re.search(r"дохід|доход|income|zarob|miesi[aą]c|за\s+місяц|за\s+месяц", low):
            category = "ignore"
            block_basis = None
            continue
        basis = re.search(r"\b(netto|brutto)\b", low)
        is_heading = bool(re.search(r"ставк|оплат|зарплат|wynagrodzenie|stawk|salary|rate|студент|student", low))
        if is_heading:
            block_basis = basis.group(1) if basis else None
        if _STUDENT_RE.search(low) and not _RATE_RE.search(line):
            category = "student"
            continue
        if re.search(r"ставк|оплат|зарплат|wynagrodzenie|stawk|salary|rate", low):
            if category == "ignore" and not re.search(r"(?:ставка|оплата|зарплата|для\s+студент|студентам?)\s*:", low):
                continue
            category = "student" if _STUDENT_RE.search(low) else "regular"
        if not _RATE_RE.search(line):
            if re.search(r"граф|житло|жиль|хостел|догов|umowa|обов|вимог|контакт|телефон|транспорт|harmonogram|zakwaterowanie", low):
                category = "regular"
                block_basis = None
            continue
        # Split polluted legacy fields at semicolons. Preserve eligibility
        # alongside each actual hourly rate, never unrelated numeric facts.
        if category == "ignore":
            continue
        for part in line.split(";"):
            if not _RATE_RE.search(part):
                continue
            value = re.sub(r"^\s*(?:Ставка|Оплата|Зарплата|Wynagrodzenie|Stawka|Salary|Rate|Студенти|Студенты|Студентам|Students|Для студентів|Для студентов|Dla studentów)\s*:", "", part, count=1, flags=re.I).strip(" •🎓💰")
            value = re.sub(r"^[\s🎓💰•]*(?:Студентам|Студенти|Студенты|Для студентів|Для студентов|Dla studentów)\s*:\s*", "", value, flags=re.I)
            value = re.sub(r"\b(?:Ставка|Оплата|netto)\s*:\s*", "", value, flags=re.I)
            if block_basis and not re.search(r"\b(?:netto|brutto)\b", value, re.I):
                value = f"{value} {block_basis}"
            # A rate line can carry its own eligibility marker even when the
            # previous heading was a regular-rate block.
            target = student if category == "student" or _STUDENT_RE.search(part) else regular
            if value not in target:
                target.append(value)
    return regular, student


def extract_vacancy_fields(text: str) -> dict[str, str]:
    """Extract explicit vacancy facts while retaining all rate variants."""
    result: dict[str, str] = {}
    labelled = {
        "location": r"місто|город|місце\s+роботи|место\s+работы|miejsce\s+pracy|lokalizacja|location",
        "schedule": r"графік(?:\s+роботи)?|график(?:\s+работы)?|harmonogram|grafik",
        "housing": r"житло|жиль[её]|zakwaterowanie|mieszkanie",
        "transport": r"транспорт|доїзд|дойзд|dojazd|dowóz",
        "contract_type": r"договір|договор|umowa|тип\s+оформлення",
        "requirements": r"вимоги|требования|wymagania",
    }
    for key, labels in labelled.items():
        value = _line_value(text, labels)
        if not value and key == "schedule":
            lines = (text or "").splitlines()
            for index, raw in enumerate(lines):
                if re.search(rf"^\s*[^\w\n]{{0,3}}\s*(?:{labels})\s*:\s*$", raw, re.I):
                    block = []
                    for following in lines[index + 1:]:
                        cleaned = _clean(following)
                        if not cleaned:
                            break
                        if re.match(r"^(?:💰|🎓|📄|🏠|🚌|📍|📅|🕒|📲|контакт|телефон|вимоги|обов'язки|обов’язки)\b", cleaned, re.I):
                            break
                        block.append(cleaned)
                    value = "; ".join(block)[:1000] or None
                    break
        if value:
            result[key] = value
    regular, student = _rate_lines(text)
    if regular:
        result["salary"] = "; ".join(regular)[:1000]
    if student:
        result["student_salary"] = "; ".join(student)[:1000]
    phone = _phone(text)
    if phone:
        result["phone"] = phone
        result["contact"] = phone
    return result


def _safe_cta(language: str = "uk", phone_contact: str | None = None) -> str:
    phone = _phone(phone_contact or "")
    if phone:
        return f"☎️ {phone}"
    return {"uk": "Напишіть у приватні повідомлення.", "ru": "Напишите в личные сообщения.", "pl": "Napisz w wiadomości prywatnej.", "en": "Send us a private message."}.get((language or "uk").lower(), "Напишіть у приватні повідомлення.")


def _prefer_richer(current: str | None, extracted: str | None) -> str | None:
    # Stored legacy fields may already contain housing, age and phone numbers.
    # Re-extract only supported hourly-rate segments without rewriting the DB.
    if current:
        rates = [part.strip() for part in current.split(";") if _RATE_RE.search(part)]
        # Legacy normalized English rate fields use "gross/hour PLN".
        if not rates:
            rates = [part.strip() for part in current.split(";") if re.search(r"\d+(?:[,.]\d+)?\s+(?:gross|net)/hour\b", part, re.I)]
        current = "; ".join(rates) or None
    if not extracted:
        return current
    if current and re.search(r"дохід|доход|income|zarob|за\s+місяц|за\s+месяц", current, re.I):
        return extracted
    if not current:
        return extracted
    count = lambda value: len(re.findall(r"\d+(?:[,.]\d+)?", value))
    return extracted if count(extracted) > count(current) or (count(extracted) == count(current) and len(extracted) > len(current) + 8) else current


def _display_fact_value(value: str | None) -> str | None:
    """Remove source labels that would otherwise be repeated by our renderer."""
    if not value:
        return None
    value = re.sub(r"^[\s💰🎓📄📲☎️]+", "", value).strip()
    value = re.sub(
        r"^(?:оплата|ставка|зарплата|wynagrodzenie|stawka|salary|rate)\s*(?::\s*|(?=\d))|"
        r"^(?:для\s+студентів|для\s+студентов|студенти|студенты|dľa\s+studentów|dla\s+studentów)\s*:\s*|"
        r"^(?:договір|договор|умова)\s*:\s*",
        "",
        value,
        count=1,
        flags=re.I,
    )
    return value.strip(" •") or None


_UNCONFIRMED_VALUE_RE = re.compile(
    r"^(?:не\s+вказано(?:\s+в\s+оголошенні)?|не\s+зазначено|не\s+указано|not\s+specified|n/?a|tbd|-)$",
    re.I,
)


def _confirmed(value: str | None) -> str | None:
    value = _clean(value)
    if not value or _UNCONFIRMED_VALUE_RE.match(value):
        return None
    if re.search(r"\[(?:google-news-rss|source|internal|debug)[^\]]*\]", value, re.I):
        return None
    return value


def _location_value(value: str | None) -> str | None:
    value = _confirmed(value)
    if not value:
        return None
    return re.sub(r"^(?:місто|место\s+роботи|місце\s+роботи|miejsce\s+pracy|локація|location)\s*:\s*", "", value, flags=re.I).strip() or None


@dataclass(frozen=True)
class VacancyFactSource:
    """Canonical read-only set of facts allowed in generated vacancy posts."""

    title: str
    location: str | None
    salary: str | None
    student_salary: str | None
    schedule: str | None
    contract_type: str | None
    housing: str | None
    transport: str | None
    requirements: str | None
    contact: str | None
    cta: str | None

    def as_dict(self) -> dict[str, str | None]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


def structured_vacancy_facts(vacancy: Vacancy, cta: str | None = None) -> VacancyFactSource:
    """Build the only fact source used by generation, rendering and validation.

    Legacy imports are read through labelled extraction, but unresolved
    placeholders and service markers are never promoted to confirmed facts.
    """
    extracted = extract_vacancy_fields(vacancy.description or "")
    salary = _display_fact_value(_confirmed(_prefer_richer(vacancy.salary, extracted.get("salary"))))
    student_salary = _display_fact_value(_confirmed(_prefer_richer(vacancy.student_salary, extracted.get("student_salary"))))
    if salary and re.search(r"(?:gross|net)/hour$", salary, re.I) and vacancy.currency:
        salary = f"{salary} {vacancy.currency}"
    if student_salary and re.search(r"(?:gross|net)/hour$", student_salary, re.I) and vacancy.currency:
        student_salary = f"{student_salary} {vacancy.currency}"
    housing = _confirmed(vacancy.housing or extracted.get("housing"))
    housing_cost = _confirmed(getattr(vacancy, "housing_cost", None))
    if housing_cost and (not housing or housing_cost.casefold() not in housing.casefold()):
        housing = f"{housing}; {housing_cost}" if housing else housing_cost
    phone = _phone(vacancy.contact or "") or extracted.get("phone")
    safe_cta = _confirmed(cta)
    if safe_cta and (_TELEGRAM_RE.search(safe_cta) or re.search(r"telegram|t\.me", safe_cta, re.I)):
        safe_cta = None
    return VacancyFactSource(
        title=_confirmed(vacancy.title) or "",
        location=_location_value(vacancy.location) or _location_value(extracted.get("location")),
        salary=salary,
        student_salary=student_salary,
        schedule=_confirmed(vacancy.schedule or extracted.get("schedule")),
        contract_type=_confirmed(extracted.get("contract_type") or vacancy.contract_type),
        housing=housing,
        transport=_confirmed(vacancy.transport or extracted.get("transport")),
        requirements=_confirmed(vacancy.requirements or extracted.get("requirements")),
        contact=phone,
        cta=safe_cta,
    )


def vacancy_fact_lines(vacancy: Vacancy, cta: str | None = None) -> list[str]:
    source = structured_vacancy_facts(vacancy, cta)
    facts = [source.title, source.location or ""]
    values = {
        "Оплата": source.salary,
        "Для студентів": source.student_salary,
        "Графік": source.schedule,
        "Договір": source.contract_type,
        "Житло": source.housing,
        "Транспорт": source.transport,
        "Вимоги": source.requirements,
    }
    for label, value in values.items():
        if value:
            facts.append(f"{label}: {value}")
    if source.contact:
        facts.append(f"Контакт: {source.contact}")
    if source.cta:
        facts.append(source.cta)
    return [fact for fact in facts if fact]


def _vacancy_post_text(vacancy: Vacancy, language: str | None = None, phone_contact: str | None = None, requested_cta: str | None = None) -> str:
    """Build a bounded vacancy post; critical facts never depend on model wording."""
    language = language or vacancy.language or "uk"
    source = structured_vacancy_facts(vacancy, requested_cta)
    salary = _display_fact_value(source.salary)
    student_salary = _display_fact_value(source.student_salary)
    contract = _display_fact_value(source.contract_type)
    phone = _phone(phone_contact or "") or source.contact
    cta = _safe_cta(language, phone)
    if source.cta:
        cta = source.cta
    labels = {
        "uk": ("Оплата", "Для студентів", "Договір"),
        "ru": ("Оплата", "Для студентов", "Договор"),
        "pl": ("Stawka", "Dla studentów", "Umowa"),
        "en": ("Pay", "Students", "Contract"),
    }.get(language, ("Оплата", "Для студентів", "Договір"))
    title = re.sub(r"^(?:ВАКАНСІЯ|ВАКАНСИЯ|VACANCY)\s*:\s*", "", source.title.strip(), flags=re.I)
    mandatory = [title]
    if source.location:
        mandatory.append(f"📍 {source.location}")
    if salary:
        mandatory.append(f"{labels[0]}: {salary}")
    if student_salary:
        mandatory.append(f"{labels[1]}: {student_salary}")
    if contract:
        mandatory.append(f"{labels[2]}: {contract}")
    mandatory.append(cta)
    conditions = []
    for label, value in (("🕒", source.schedule), ("🏠", source.housing), ("🚐", source.transport), ("📋", source.requirements)):
        if value:
            conditions.append(f"{label} {value}")
    text = "\n".join(mandatory + conditions)
    text = _TELEGRAM_RE.sub("", text).replace(" — ", ", ").replace(" – ", ", ").strip()
    return text


def render_vacancy_post(vacancy: Vacancy, language: str | None = None, phone_contact: str | None = None, requested_cta: str | None = None) -> str:
    text = _vacancy_post_text(vacancy, language, phone_contact, requested_cta)
    if len(text) > THREADS_LIMIT:
        raise ValueError("Vacancy facts exceed 500 characters; split or review the draft instead of dropping rates or conditions")
    return text


def vacancy_post_part_count(vacancy: Vacancy, language: str | None = None, phone_contact: str | None = None, requested_cta: str | None = None) -> int:
    """Return the number of review drafts needed when facts exceed one post."""
    lines = _vacancy_post_text(vacancy, language, phone_contact, requested_cta).splitlines()
    if any(len(line) > THREADS_LIMIT for line in lines):
        raise ValueError("A single vacancy condition is longer than the Threads limit and needs operator editing")
    parts = 1
    size = 0
    for line in lines:
        extra = len(line) + (1 if size else 0)
        if size and size + extra > THREADS_LIMIT:
            parts += 1
            size = len(line)
        else:
            size += extra
    return parts


@dataclass(frozen=True)
class QualityAssessment:
    approved: bool
    score: int
    reasons: tuple[str, ...]


def assess_post(content: str, content_type: Any) -> QualityAssessment:
    text = " ".join((content or "").split())
    kind = str(getattr(content_type, "value", content_type)).upper()
    reasons: list[str] = []
    if kind in {'LEGAL_INFORMATION', 'DOCUMENTS_INFORMATION', 'CANDIDATE_STORY', 'RECRUITER_STORY'}:
        reasons.append('нужна проверка оператором источников, актуальности и согласия на историю; автоматическое одобрение запрещено')
    if len(text) < AUTO_POST_MIN_CHARS:
        reasons.append(f"текст короче {AUTO_POST_MIN_CHARS} символов")
    if len(content or "") > THREADS_LIMIT:
        reasons.append(f"текст длиннее {THREADS_LIMIT} символов")
    urls = _URL_RE.findall(content or "")
    if any("news.google.com/rss/" in url.lower() or len(url) > 180 for url in urls):
        reasons.append("сырой или слишком длинный служебный URL")
    if _TELEGRAM_RE.search(content or "") or re.search(r"telegram|t\.me", content or "", re.I):
        reasons.append("контакты Telegram запрещены")
    if "деталі — у джерелі" in text.lower() or "details — in the source" in text.lower():
        reasons.append("нет самостоятельной ценности без перехода по ссылке")
    if kind == "VACANCY":
        signals = sum(bool(re.search(pattern, text, re.IGNORECASE)) for pattern in (r"\d+\s*(?:zł|pln|грн|€|eur)", r"графік|график|змін|zmian|godzin|годин", r"житло|жиль|zakwaterowanie|mieszkanie", r"umowa|договір|договор", r"транспорт|dojazd|dowóz", r"вимоги|требования|wymagania"))
        if signals < 2:
            reasons.append("в вакансии меньше двух проверяемых условий")
    score = max(0, 100 - 25 * len(reasons))
    return QualityAssessment(approved=not reasons, score=score, reasons=tuple(reasons))
