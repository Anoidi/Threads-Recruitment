"""Deterministic, source-grounded editorial angles.

Why this exists
---------------
The local writer model is small and slow: asking it for several posts in one
completion times out, and when it converges on the same wording the planner
rejects the proposal as a duplicate. The result was an empty content pipeline
that only moved when an operator intervened.

These angles keep the daily pipeline producing something new without inventing
a single fact. Every sentence is built from confirmed vacancy fields, exactly
like the deterministic vacancy renderer, and each angle needs its own fact to
exist - otherwise the angle is skipped.
"""
from __future__ import annotations

import re

from .content_quality import structured_vacancy_facts

THREADS_LIMIT = 500

# A marker that splits the template into the sentences and the call to
# action. It never appears in a vacancy fact.
_CTA_SENTINEL = "\u241f"

EDITORIAL_ANGLES = ("RATE", "SCHEDULE", "HOUSING", "CONTRACT", "CITY", "TRANSPORT", "REQUIREMENTS")

# The subject each angle covers. Two angles with the same subject would be
# the same post with different words, so every angle owns a distinct one.
_ANGLE_SUBJECT = {
    "RATE": "PAY",
    "SCHEDULE": "SCHEDULE",
    "HOUSING": "HOUSING",
    "CONTRACT": "CONTRACT",
    "CITY": "CITY",
    "TRANSPORT": "TRANSPORT",
    "REQUIREMENTS": "REQUIREMENTS",
}

# Each template only mentions the condition it is built from, because the
# final publish gate rejects a post that names a condition the vacancy does
# not confirm.
_TEMPLATES: dict[str, dict[str, str]] = {
    "RATE": {
        "uk": "Ставка: {rate}. Перед виїздом уточніть у роботодавця: чи це оплата за годину, за який період і коли приходить перша зарплата. Так ви перевірите цифру до того, як приймете рішення. {cta}",
        "ru": "Ставка: {rate}. Перед выездом уточните у работодателя: это оплата за час, за какой период и когда приходит первая зарплата. Так вы проверите цифру до того, как примете решение. {cta}",
        "pl": "Stawka: {rate}. Przed wyjazdem zapytaj pracodawcy: czy to stawka za godzinę, za jaki okres i kiedy wypłata. Dzięki temu sprawdzisz kwotę, zanim podejmiesz decyzję. {cta}",
        "en": "Rate: {rate}. Before you travel ask the employer: is it per hour, for which period, and when is the first payday. That way you check the number before you decide. {cta}",
    },
    "SCHEDULE": {
        "uk": "Графік: {schedule}. Уточніть до виїзду, скільки змін на тиждень, як розподілені перерви та чи є робота у вихідні. Графік у вакансії задано загально, а деталі потрібно звірити. {cta}",
        "ru": "График: {schedule}. Уточните до выезда, сколько смен в неделю, как распределены перерывы и есть ли работа в выходные. В вакансии график указан в общем виде, детали нужно сверить. {cta}",
        "pl": "Grafik: {schedule}. Przed wyjazdem zapytaj, ile zmian w tygodniu, jak rozkładane są przerwy i czy są wolne. W ogłoszeniu grafik jest podany ogólnie, szczegóły trzeba potwierdzić. {cta}",
        "en": "Schedule: {schedule}. Before you travel check how many shifts per week, how breaks are arranged and whether there is weekend work. The vacancy states it in general terms, the details need confirming. {cta}",
    },
    "HOUSING": {
        "uk": "Житло: {housing}. Перш за все уточніть, скільки це на одну людину, чи входить комуналка і який депозит. Такі деталі зазвичай не вказані в оголошенні, хоча змінюють реальний бюджет. {cta}",
        "ru": "Жильё: {housing}. Первым делом уточните, сколько это на одного человека, входит ли коммуналка и какой депозит. Такие детали обычно не указаны в объявлении, хотя меняют реальный бюджет. {cta}",
        "pl": "Zakwaterowanie: {housing}. Najpierw zapytaj, ile to za osobę, czy wliczono media i jaka jest kaucja. Takich szczegółów zwykle nie ma w ogłoszeniu, choć zmieniają realny budżet. {cta}",
        "en": "Housing: {housing}. First ask whether the price is per person, whether utilities are included and how large the deposit is. These details are usually missing from the listing, yet they change the real budget. {cta}",
    },
    "CONTRACT": {
        "uk": "Договір: {contract}. Уточніть, як нараховуються суми, коли узгоджують відпустку та як рахують понаднормові години. Назва договору вказує лише форму, а умови треба прочитати. {cta}",
        "ru": "Договор: {contract}. Уточните, как начисляются суммы, когда согласуют отпуск и как считают сверхурочные. Название договора указывает только форму, условия нужно прочитать. {cta}",
        "pl": "Umowa: {contract}. Zapytaj, jak naliczane są sumy, kiedy ustala się urlop i liczy się nadgodziny. Nazwa umowy wskazuje tylko formę, warunki trzeba przeczytać. {cta}",
        "en": "Contract: {contract}. Ask how the amounts are calculated, when holiday is agreed and how overtime is counted. The contract name states the form only, the terms have to be read. {cta}",
    },
    "CITY": {
        "uk": "Місто: {city}. Перед виїздом уточніть, як добиратися до роботи, скільки часу займає дорога та де зупиняється автобус. Місто в оголошенні названо, але час на дорогу залежить від адреси. {cta}",
        "ru": "Город: {city}. Перед выездом уточните, как добираться до работы, сколько времени занимает дорога и где останавливается автобус. Город в объявлении назван, но время в пути зависит от адреса. {cta}",
        "pl": "Miasto: {city}. Przed wyjazdem ustal, jak dojechać do pracy, ile trwa dojazd i gdzie zatrzymuje się autobus. Miasto jest podane, ale czas dojazdu zależy od adresu. {cta}",
        "en": "City: {city}. Before you travel check how to get to work, how long the journey takes and where the bus stops. The listing names the city, but the travel time depends on the address. {cta}",
    },
    "TRANSPORT": {
        "uk": "Транспорт: {transport}. Уточніть до виїзду, звідки й коли їде автобус, скільки часу займає дорога та чи є зупинка поруч із житлом. Графік руху змінюється, тому деталі варто підтвердити. {cta}",
        "ru": "Транспорт: {transport}. Уточните до выезда, откуда и когда идёт автобус, сколько времени занимает дорога и есть ли остановка рядом с жильём. Расписание меняется, поэтому детали стоит подтвердить. {cta}",
        "pl": "Transport: {transport}. Przed wyjazdem ustal, skąd i o której jedzie autobus, ile trwa dojazd i czy jest przystanek blisko zakwaterowania. Rozkład się zmienia, więc szczegóły warto potwierdzić. {cta}",
        "en": "Transport: {transport}. Before you travel check where the bus leaves from and when, how long the journey takes and whether there is a stop near the housing. Timetables change, so confirm the details. {cta}",
    },
    "REQUIREMENTS": {
        "uk": "Вимоги: {requirements}. Уточніть до виїзду, який досвід потрібен, чи потрібні документи та як проходить перший день. У вакансії вимоги вказані загально, а деталі краще уточнити. {cta}",
        "ru": "Требования: {requirements}. Уточните до выезда, какой опыт нужен, нужны ли документы и как проходит первый день. В вакансии требования указаны в общем виде, детали лучше уточнить. {cta}",
        "pl": "Wymagania: {requirements}. Przed wyjazdem ustal, jakie doświadczenie jest potrzebne, czy trzeba mieć dokumenty i jak wygląda pierwszy dzień. W ogłoszeniu wymagania są ogólne, szczegóły warto doprecyzować. {cta}",
        "en": "Requirements: {requirements}. Before you travel check what experience is needed, whether documents are required and how the first day works. The listing states the requirements in general terms, the details are worth confirming. {cta}",
    },
}

_CTA = {
    "uk": "Напишіть у приватні повідомлення.",
    "ru": "Напишите в личные сообщения.",
    "pl": "Napisz w wiadomości prywatnej.",
    "en": "Send us a private message.",
}

# Additional wording variants beyond ``_TEMPLATES``. Every variant must
# satisfy three rules: the confirmed vacancy fact is unchanged (only the
# wording around it differs), no second condition is introduced, and no
# question mark appears (the FACT format is defined by its absence).
# A single fixed template per angle was the root cause of near-identical
# published posts: same fact + same angle rendered word-for-word the same
# text on another day. ``variant 0`` is always the original template, so
# every existing caller keeps producing the same texts it did before.
_TEMPLATE_VARIANTS: dict[str, dict[str, list[str]]] = {
    "RATE": {
        "uk": [
            "Ставка в оголошенні: {rate}. Перевірте до виїзду три пункти: це оплата за годину чи за зміну, "
            "за який період її нараховують і в який день приходить перша виплата. Цифра без цих уточнень мало що розповість. {cta}",
            "Ставка за умовами оголошення: {rate}. Уточніть у роботодавця до виїзду: оплата рахується за "
            "годину чи за зміну, за який період нарахування та в який день перша виплата. Так цифра стає зрозумілою до рішення. {cta}",
            "Виміряйте число добросовісно: {rate}. Розберіть його в розмові: рахунок за годину чи за зміну, "
            "період нарахування й день видачі перших грошей. Тоді цифра перестане бути загадкою. {cta}",
        ],
    },
    "SCHEDULE": {
        "uk": [
            "Графік роботи: {schedule}. З'ясуйте до виїзду: скільки змін у тиждень, коли починається та "
            "закінчується робочий день і чи бувають суботи. У оголошенні графік задано загально, деталі уточнює роботодавець. {cta}",
            # The SCHEDULE fact is the longest one (~266 chars), so these two
            # extra wordings keep their advice short: the render must stay
            # under the 500-char quality limit while the pair stays below the
            # NEAR-duplicate threshold (0.78).
            "Ритм тижня такий: {schedule}. Початок зміни та вихідні краще зафіксувати окремо. "
            "Розклад у оголошенні задано загалом, деталі підтверджує роботодавець. {cta}",
            "Ваш тижневий план уже показано: {schedule}. Оцініть, наскільки він підходить вашим "
            "намірам. Проміжки між змінами та суботу звіряють при особистій розмові. {cta}",
        ],
    },
    "HOUSING": {
        "uk": [
            "Житло коштує {housing} — стільки вказано в оголошенні. Перш ніж погоджуватися, уточніть: чи сума "
            "за одну людину, чи комунальні платежі окремо та яким буде завдаток. Від цього залежить реальний бюджет. {cta}",
        ],
    },
    "CONTRACT": {
        "uk": [
            "Тип договору: {contract}. Перед підписом розберіть ключові пункти: звідки береться сума, що "
            "прописано про відпочинок і години понад норму. Заголовок лише називає форму, а умови живуть "
            "у рядках. {cta}",
            "Форма співпраці: {contract}. Перечитайте умови в самому документі: як нараховуються виплати, "
            "чи згадується відпустка та як оплачують додаткові години. Назва договору про це мовчить. {cta}",
            "Вид угоди: {contract}. Сам документ розкаже більше за назву: насамперед як рахуються гроші, "
            "де вписаний відпочинок і що з годинами понад норму. Прочитайте текст до підпису. {cta}",
        ],
    },
    "CITY": {
        "uk": [
            "Місто роботи: {city}. Що перевірити перед першим виїздом: маршрут до місця праці, скільки часу "
            "займає дорога та де зупиняється автобус. Час у дорозі залежить від адреси, тож дізнайтеся про нього заздалегідь. {cta}",
            "Адреса роботи: {city}. Маршрут заздалегідь — головне перед першим виїздом: скільки триває "
            "дорога від вашого місця та на якій зупинці висаджує автобус. Відстань до адреси впливає на "
            "час у дорозі. {cta}",
            "Маршрут для першого дня: {city}. Накидайте собі в карті заздалегідь: скільки хвилин від житла "
            "до зупинки, коли автобус відправляється і скільки всього йде шлях. Хвилини запасу зберігають "
            "спокій. {cta}",
        ],
    },
    "TRANSPORT": {
        "uk": [
            "Доїхати до роботи просто: {transport}. Занотуйте собі два пункти напередодні зміни: звідки "
            "відправляється автобус і о котрій годині від'їзд, щоб не бігти в останню хвилину. Розклад "
            "оновлюється, тож свіжий погляд доречний. {cta}",
            "Транспорт до роботи: {transport}. Сплануйте поїздку за двома пунктами: точка старту автобуса "
            "та час від'їзду напередодні зміни. Розклад оновлюється, тому перевірте його ще раз. {cta}",
            "Автобус береже нерви: {transport}. Напередодні зміни звірте два моменти: чи не змінилися години "
            "руху та чи збігається зупинка біля житла з вашим маршрутом. Розпис оновлюється, тож свіжий "
            "погляд доречний. {cta}",
        ],
    },
    "REQUIREMENTS": {
        "uk": [
            "Вимоги: {requirements}. Перш ніж відгукнутися, з'ясуйте: які документи будуть потрібні, чи "
            "враховується досвід і як виглядає перший день. Решту уточнюють під час співбесіди. {cta}",
        ],
    },
}


def _template_for(angle: str, language: str, variant: int) -> str:
    """The template for one angle, its language and a rotation index.

    ``variant 0`` is the original (and only, for non-Ukrainian languages)
    template. A higher variant cycles through all available wordings for the
    language so the same fact gets a different — but equally confirmed —
    text on the next day.
    """
    try:
        pool = (_TEMPLATES[angle].get(language) if language else None) or _TEMPLATES[angle]["uk"]
    except KeyError:
        return ""
    extras = (_TEMPLATE_VARIANTS.get(angle) or {}).get(language) or []
    if not extras:
        return pool
    index = int(variant) % (len(extras) + 1)
    return pool if index == 0 else extras[index - 1]

_CONTENT_TYPE = {
    "RATE": "FAQ",
    "SCHEDULE": "FAQ",
    "HOUSING": "FAQ",
    "CONTRACT": "FAQ",
    "CITY": "EDUCATIONAL",
    "TRANSPORT": "EDUCATIONAL",
    "REQUIREMENTS": "FAQ",
}


def angle_parts(vacancy, angle: str, language: str = "uk", phone_contact: str | None = None, variant: int = 0) -> tuple[list[str], str] | None:
    """The sentences and the call to action of one angle, or None.

    The sentences are the confirmed fact and the advice built from it. Keeping
    them apart from the call to action is what lets a format experiment
    re-shape the same confirmed fact without repeating the whole template.

    ``variant`` rotates through the wording pool of the language. The facts
    are identical in every variant - only the wording differs, so the daily
    pipeline stops producing the same post for the same vacancy.
    """
    if angle not in _TEMPLATES:
        return None
    language = (language or "uk").lower()[:2]
    template = _template_for(angle, language, variant)
    if not template:
        return None
    facts = structured_vacancy_facts(vacancy)
    cta = _CTA.get(language, _CTA["uk"])
    if phone_contact:
        digits = "".join(ch for ch in str(phone_contact) if ch.isdigit())
        if 8 <= len(digits) <= 15:
            cta = f"☎️ {phone_contact}"
    values: dict[str, str] = {}
    if angle == "RATE":
        rate = facts.salary or facts.student_salary
        if not rate:
            return None
        values["rate"] = rate
    elif angle == "SCHEDULE":
        if not facts.schedule:
            return None
        values["schedule"] = facts.schedule
    elif angle == "HOUSING":
        housing = facts.housing
        if not housing:
            return None
        values["housing"] = housing
    elif angle == "CONTRACT":
        if not facts.contract_type:
            return None
        values["contract"] = facts.contract_type
    elif angle == "CITY":
        if not facts.location:
            return None
        values["city"] = facts.location
    elif angle == "TRANSPORT":
        if not facts.transport:
            return None
        values["transport"] = facts.transport
    elif angle == "REQUIREMENTS":
        if not facts.requirements:
            return None
        values["requirements"] = facts.requirements
    try:
        text = template.format(cta=_CTA_SENTINEL, **values)
    except KeyError:
        return None
    head, _, _ = text.partition(_CTA_SENTINEL)
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", head.strip()) if part.strip()]
    if not sentences:
        return None
    return sentences, cta


def angle_post(vacancy, angle: str, language: str = "uk", phone_contact: str | None = None, variant: int = 0) -> str | None:
    """Build one source-grounded editorial post, or None when a fact is missing."""
    parts = angle_parts(vacancy, angle, language, phone_contact, variant=variant)
    if parts is None:
        return None
    sentences, cta = parts
    text = " ".join(" ".join(sentences + [cta]).split())
    if len(text) > THREADS_LIMIT:
        return None
    return text


def angle_content_type(angle: str) -> str:
    return _CONTENT_TYPE.get(angle, "EDUCATIONAL")


def angle_subject(angle: str) -> str:
    """The subject an angle covers. Every angle owns a distinct one."""
    return _ANGLE_SUBJECT.get(angle, "GENERAL")


def angle_hook(text: str) -> str:
    """The first sentence is the search/display metadata for the post."""
    head = " ".join((text or "").split())
    for separator in (". ", "? ", "! "):
        if separator in head:
            return head.split(separator)[0][:500]
    return head[:500]
