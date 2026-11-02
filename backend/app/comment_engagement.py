"""Safe, source-grounded comment engagement primitives.

The module deliberately keeps facts and outbound copy separate.  The only
data available to the writer is the captured comment, its real username and
the local post context.  A caller still has to file a draft and obtain an
operator approval before transport.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from .ai import AIService

MAX_THREADS_CHARS = 500
STYLES = ("moderate", "sarcastic", "discussion")
MODES = ("MONITOR", "DRAFT", "AUTO")

_SERVICE_MARKERS = (
    "не вказано в оголошенні", "не указано в объявлении", "[google-news-rss]",
    "службовий", "internal", "system prompt", "assistant:", "<|",
)
_DISALLOWED = re.compile(
    r"\b(?:дурн(?:ий|а|і)|туп(?:ий|а|і)|дебіл(?:и|ка)?|інвалід|циган|жид|лох(?:и|а)?|\b(?:женщинам|мужчинам)\s+не)\b",
    re.IGNORECASE,
)
_UK_HINTS = re.compile(r"\b(?:і|та|що|як|ви|вам|пишіть|уточніть|робота|вакансія|можу|є)\b", re.IGNORECASE)


def normalize_username(value: str | None) -> str:
    """Return a stable lower-case Threads username without a leading @."""
    raw = (value or "").strip().lstrip("@").strip().lower()
    return re.sub(r"[^a-z0-9_.-]", "", raw)


def mention_username(value: str | None) -> str:
    normalized = normalize_username(value)
    return f"@{normalized}" if normalized else ""


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def validate_engagement_text(
    text: str,
    *,
    expected_usernames: Iterable[str] = (),
    require_ukrainian: bool = True,
) -> str:
    """Fail closed on transport-dangerous or fabricated-looking copy."""
    value = re.sub(r"\s+", " ", (text or "").replace("\x00", "")).strip()
    if not value:
        raise ValueError("Порожній текст взаємодії")
    if len(value) > MAX_THREADS_CHARS:
        raise ValueError(f"Текст перевищує ліміт Threads у {MAX_THREADS_CHARS} символів")
    lowered = value.casefold()
    if any(marker in lowered for marker in _SERVICE_MARKERS):
        raise ValueError("У тексті є службовий маркер або незаповнений шаблон")
    if _DISALLOWED.search(value):
        raise ValueError("Текст містить дискримінаційну або принизливу лексику")
    allowed = {normalize_username(item) for item in expected_usernames if normalize_username(item)}
    mentions = {normalize_username(item) for item in re.findall(r"@([A-Za-z0-9_.-]+)", value)}
    if not mentions.issubset(allowed):
        unknown = sorted(mentions - allowed)
        raise ValueError(f"Є непідтверджені username: {', '.join('@' + x for x in unknown)}")
    if require_ukrainian and not _UK_HINTS.search(value):
        raise ValueError("Текст не схожий на українську розмовну подачу")
    if not value.endswith(("?", "!", ".")):
        raise ValueError("Текст має завершуватися питанням або конкретним CTA")
    return value


def analyze_comment(text: str) -> dict[str, Any]:
    """Classify one real comment with the existing community AI contract."""
    classified = AIService().classify(text)
    value = text.casefold()
    if any(token in value for token in ("дякую", "клас", "супер", "🔥", "подоба")):
        sentiment = "positive"
    elif any(token in value for token in ("не", "шкода", "дорого", "проблем", "скарг")):
        sentiment = "negative"
    else:
        sentiment = "neutral"
    topic = {
        "HOUSING_QUESTION": "housing",
        "SALARY_QUESTION": "salary",
        "SCHEDULE_QUESTION": "schedule",
        "DOCUMENTS_QUESTION": "documents",
        "JOB_SEARCH": "job_search",
        "COMPLAINT": "complaint",
    }.get(classified["classification"], "other")
    return {
        **classified,
        "topic": topic,
        "sentiment": sentiment,
        "needs_recruiter_response": classified["classification"] not in {"OTHER"},
        "humor_opportunity": topic in {"salary", "schedule", "housing"} and sentiment != "negative",
    }


async def analyze_comment_with_ai(text: str) -> dict[str, Any]:
    """Ask the configured community model, retaining deterministic fallback."""
    return await AIService().analyze_comment_engagement(text)


def analyze_comments(comments: Iterable[dict[str, Any]]) -> dict[str, Any]:
    items = [analyze_comment(str(item.get("text") or item.get("message") or "")) for item in comments]
    return {
        "count": len(items),
        "topics": dict(Counter(item["topic"] for item in items)),
        "sentiments": dict(Counter(item["sentiment"] for item in items)),
        "recruiter_response_count": sum(1 for item in items if item["needs_recruiter_response"]),
        "humor_opportunity_count": sum(1 for item in items if item["humor_opportunity"]),
        "items": items,
        "source": "real_threads_comments",
    }


def generate_variants(
    *,
    username: str,
    comment_text: str,
    topic: str,
    source_context: str | None = None,
) -> list[dict[str, Any]]:
    """Create three editable Ukrainian variants from captured evidence only."""
    user = mention_username(username)
    quote = re.sub(r"\s+", " ", comment_text).strip().replace("@", "")[:150]
    context = re.sub(r"\s+", " ", source_context or "").strip()
    context_line = f" Факти з поста: {context[:180]}." if context else ""
    topic_line = {
        "housing": "житло",
        "salary": "оплату",
        "schedule": "графік",
        "documents": "документи",
        "job_search": "сам пошук роботи",
    }.get(topic, "деталі")
    drafts = [
        ("moderate", f"{user}, бачу ваше питання про {topic_line}: «{quote}». Перевірю саме цю деталь у рекрутера й повернуся з відповіддю. Що ще важливо уточнити?" + context_line),
        ("sarcastic", f"{user}, без магії й «та там усе ясно» 🙂 Питання про {topic_line} «{quote}» якраз варте нормальної відповіді. Яку деталь розібрати першою?" + context_line),
        ("discussion", f"{user}, чесна дискусія починається з конкретики. Ви питаєте про {topic_line}: «{quote}». Який факт має бути в оголошенні обов’язково, щоб не їхати навмання?" + context_line),
    ]
    variants: list[dict[str, Any]] = []
    for style, draft in drafts:
        try:
            checked = validate_engagement_text(draft, expected_usernames=[username])
        except ValueError:
            # Context is optional editorial evidence. If it makes the variant
            # too long, remove it; never truncate a fact silently.
            checked = validate_engagement_text(draft.split(" Факти з поста:", 1)[0], expected_usernames=[username])
        variants.append({"style": style, "content": checked, "character_count": len(checked), "content_hash": content_hash(checked)})
    return variants


class MentionManager:
    """Deterministic recipient gate for opt-outs, cooldowns and spam limits."""

    def __init__(self, *, cooldown_hours: int = 24, max_group_size: int = 3):
        self.cooldown = timedelta(hours=max(1, cooldown_hours))
        self.max_group_size = max(1, max_group_size)

    def select(
        self,
        usernames: Iterable[str],
        *,
        opted_out: Iterable[str] = (),
        history: Iterable[dict[str, Any]] = (),
        now: datetime | None = None,
    ) -> list[str]:
        now = now or datetime.now(timezone.utc)
        blocked = {normalize_username(item) for item in opted_out}
        latest: dict[str, datetime] = {}
        for row in history:
            normalized = normalize_username(row.get("normalized_username") or row.get("username"))
            if not normalized:
                continue
            stamp = row.get("created_at") or row.get("mentioned_at")
            if isinstance(stamp, str):
                try:
                    stamp = datetime.fromisoformat(stamp)
                except ValueError:
                    stamp = None
            if isinstance(stamp, datetime):
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=timezone.utc)
                latest[normalized] = max(latest.get(normalized, datetime.min.replace(tzinfo=timezone.utc)), stamp)
        chosen: list[str] = []
        for candidate in usernames:
            normalized = normalize_username(candidate)
            if not normalized or normalized in blocked or normalized in chosen:
                continue
            recent = latest.get(normalized)
            if recent and now - recent < self.cooldown:
                continue
            chosen.append(normalized)
            if len(chosen) >= self.max_group_size:
                break
        return chosen

