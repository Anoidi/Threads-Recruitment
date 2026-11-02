from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from sqlalchemy import desc, select

from app.config import settings
from app.content_quality import extract_vacancy_fields, render_vacancy_post
from app.enums import PostStatus
from app.logging import get_logger
from app.models import AppSetting, PostRevision, SyncState, ThreadsPost, Topic, Vacancy
from app.services import activity
from app.threads_client import ThreadsAPIError

log = get_logger("worker")


def _telegram_channel_id() -> str:
    return settings.telegram_channel.strip().lstrip("@").lower()


def _extract_vacancy_fields(text: str) -> dict[str, str]:
    """Compatibility wrapper kept in the worker for importer-focused tests."""
    return extract_vacancy_fields(text)


async def sync_telegram_vacancies(db) -> str:
    """Import new channel posts only; Telegram's Bot API cannot backfill history."""
    from . import main as _m

    if not settings.telegram_bot_token or not settings.telegram_channel:
        return "Telegram import is not configured."
    state = (
        await db.execute(select(SyncState).where(SyncState.resource == "telegram_channel_posts").with_for_update())
    ).scalar_one_or_none()
    params: dict[str, object] = {"timeout": 0, "allowed_updates": '["channel_post","edited_channel_post"]'}
    try:
        if state and state.cursor:
            params["offset"] = int(state.cursor)
        async with _m.httpx.AsyncClient(timeout=12) as client:
            response = await client.get(
                f"https://api.telegram.org/bot{settings.telegram_bot_token}/getUpdates", params=params
            )
        payload = response.json()
        if response.status_code >= 400 or not payload.get("ok"):
            raise RuntimeError("Telegram Bot API getUpdates failed")
    except (_m.httpx.HTTPError, ValueError) as exc:
        raise RuntimeError(f"Telegram polling failed ({type(exc).__name__})") from exc
    imported = 0
    next_offset = int(state.cursor) if state and state.cursor else 0
    for update in payload.get("result", []):
        next_offset = max(next_offset, int(update.get("update_id", 0)) + 1)
        message = update.get("channel_post") or update.get("edited_channel_post") or {}
        chat = message.get("chat") or {}
        username = str(chat.get("username") or "").lower()
        if username != _m._telegram_channel_id():
            continue
        message_id = str(message.get("message_id") or "")
        body = str(message.get("text") or message.get("caption") or "").strip()
        if not message_id or not body:
            continue
        if not re.search(r"ваканс|робот|робота|praca|oferta pracy|zatrudni|работ", body, re.IGNORECASE):
            continue
        lines = [line.strip() for line in body.splitlines() if line.strip()]
        title = (
            re.sub(r"^[^\wА-Яа-яІіЇїЄєŁłÓóĄąĘęŚśŹźŻż]+", "", lines[0])[:255]
            if lines
            else "Оголошення про роботу"
        )
        fields = _m._extract_vacancy_fields(body)
        location = fields.get("location") or "Не вказано в оголошенні"
        language = (
            "pl" if re.search(r"praca|wynagrodzenie|zatrudni", body, re.IGNORECASE) else settings.default_language
        )
        vacancy = (
            await db.execute(
                select(Vacancy).where(Vacancy.source == "telegram", Vacancy.source_message_id == message_id)
            )
        ).scalar_one_or_none()
        if vacancy:
            from app.vacancy_freshness import record_drift

            changes = []
            for key, value in fields.items():
                if not hasattr(vacancy, key):
                    continue
                before = getattr(vacancy, key)
                if value and before and value != before:
                    changes.append(f"{key}: {before} → {value}")
                setattr(vacancy, key, value)
            if changes:
                record_drift(vacancy, "; ".join(changes))
                affected = list(
                    (
                        await db.execute(
                            select(ThreadsPost.id).where(
                                ThreadsPost.vacancy_id == vacancy.id,
                                ThreadsPost.status.in_(
                                    [PostStatus.PUBLISHED, PostStatus.SCHEDULED, PostStatus.APPROVED]
                                ),
                            )
                        )
                    ).scalars()
                )
                await activity(
                    db,
                    "vacancy_source_drifted",
                    "vacancy",
                    vacancy.id,
                    {"changes": changes, "affected_posts": affected},
                )
            vacancy.title = title or vacancy.title
            vacancy.description = body
            vacancy.location = location
            vacancy.language = language
            continue
        vacancy = Vacancy(
            title=title or "Оголошення про роботу",
            description=body,
            location=location,
            salary=fields.get("salary"),
            student_salary=fields.get("student_salary"),
            schedule=fields.get("schedule"),
            contract_type=fields.get("contract_type"),
            housing=fields.get("housing"),
            transport=fields.get("transport"),
            requirements=fields.get("requirements"),
            contact=fields.get("contact"),
            language=language,
            source="telegram",
            source_message_id=message_id,
            source_url=f"https://t.me/{_m._telegram_channel_id()}/{message_id}",
        )
        db.add(vacancy)
        await db.flush()
        await _m._create_telegram_post_draft(db, vacancy, body)
        imported += 1
    if not state:
        state = SyncState(resource="telegram_channel_posts")
        db.add(state)
    state.cursor = str(next_offset)
    state.last_success_at = datetime.now(timezone.utc)
    state.last_error = None
    await db.commit()
    return f"Imported {imported} new Telegram vacancy post(s)."


async def _create_telegram_post_draft(db, vacancy: Vacancy, source_text: str) -> ThreadsPost:
    """Create the same safe, natural vacancy draft for short and long sources."""
    from . import main as _m

    phone_contact = (
        await db.execute(select(AppSetting.value).where(AppSetting.key == "phone_contact"))
    ).scalar_one_or_none()
    content = render_vacancy_post(vacancy, vacancy.language, str(phone_contact or ""))
    editor = "telegram-fact-condensor"
    summary = (
        f"Детерминированный текст вакансии: ставки и условия взяты из источника, "
        f"Telegram-контакты удалены. Источник: {vacancy.source_url}."
    )
    post = ThreadsPost(
        vacancy_id=vacancy.id,
        hook=vacancy.title[:500],
        content=content,
        content_type="VACANCY",
        language=vacancy.language,
        reasoning_summary=summary,
        status=PostStatus.AI_GENERATED,
    )
    db.add(post)
    await db.flush()
    db.add(PostRevision(post_id=post.id, version=1, content=content, editor=editor))
    await _m._auto_schedule_post(db, post)
    return post


def recent_source_timestamp(value: str | None, now: datetime) -> datetime | None:
    """Reject undated, malformed, future and stale public-source evidence."""
    if not value:
        return None
    try:
        try:
            stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            stamp = parsedate_to_datetime(value)
        if stamp.tzinfo is None:
            return None
        stamp = stamp.astimezone(timezone.utc)
        return stamp if now - timedelta(days=7) <= stamp <= now + timedelta(minutes=5) else None
    except (ValueError, TypeError, OverflowError):
        return None


async def scout_sources(db) -> str:
    """Collect public Threads discussions and Google News RSS; persist deduped topics."""
    from app.monitoring_status import public_search_unavailable_reason
    from . import main as _m

    account = await _m._connected_account(db) if not settings.threads_mock_mode else None
    search_state = (
        await db.execute(select(SyncState).where(SyncState.resource == "threads_trend_radar"))
    ).scalar_one_or_none()
    if search_state is None:
        search_state = SyncState(resource="threads_trend_radar")
        db.add(search_state)
    keywords = (
        await db.execute(select(AppSetting.value).where(AppSetting.key == "scout_keywords"))
    ).scalar_one_or_none()
    if not isinstance(keywords, list):
        keywords = ["робота Польща", "praca Polska"]
    found = 0
    threads_search_ok = False
    threads_search_failed = False
    now = datetime.now(timezone.utc)
    capability_error = public_search_unavailable_reason(account, now)
    if capability_error:
        search_state.last_error = capability_error
    else:
        client = _m.get_threads_client(account.profile_id, _m.decrypt_token(account.access_token_encrypted))
        try:
            for keyword in keywords[:3]:
                try:
                    results = await client.keyword_search(str(keyword), limit=10)
                    threads_search_ok = True
                except ThreadsAPIError as exc:
                    threads_search_failed = True
                    log.warning("threads_optional_search_unavailable", category=exc.category)
                    search_state.last_error = f"Threads keyword search: {exc.category}"
                    break
                for rank, item in enumerate(results):
                    text = str(item.get("text") or "").strip()
                    url = str(item.get("permalink") or "")
                    published = _m.recent_source_timestamp(item.get("timestamp"), now)
                    if not text or published is None:
                        continue
                    exists = (
                        (
                            await db.execute(
                                select(Topic.id).where(Topic.source_url == url, Topic.source_url.is_not(None))
                            )
                        ).scalar_one_or_none()
                        if url
                        else None
                    )
                    if exists:
                        continue
                    topic = Topic(
                        title=text[:250],
                        source="threads-keyword-search",
                        source_url=url or None,
                        language=settings.default_language,
                        priority=max(60, 90 - rank * 3),
                        explanation=(
                            f"Публичный результат Threads от {published.isoformat()} "
                            f"по запросу «{keyword}»; место в выдаче: {rank + 1}."
                        ),
                    )
                    db.add(topic)
                    await db.flush()
                    found += 1
        finally:
            await client.aclose()
    if threads_search_ok and not threads_search_failed:
        search_state.last_success_at = datetime.now(timezone.utc)
        search_state.last_error = None
    async with _m.httpx.AsyncClient(
        timeout=15, follow_redirects=True, headers={"User-Agent": "ThreadsRecruitmentManager/0.1"}
    ) as client:
        for keyword in keywords[:2]:
            try:
                response = await client.get(
                    "https://news.google.com/rss/search",
                    params={"q": str(keyword), "hl": "pl", "gl": "PL", "ceid": "PL:pl"},
                )
                response.raise_for_status()
                root = ET.fromstring(response.content)
            except (_m.httpx.HTTPError, ET.ParseError):
                continue
            for item in root.findall("./channel/item")[:5]:
                title = html.unescape((item.findtext("title") or "").strip())
                url = (item.findtext("link") or "").strip()
                published = _m.recent_source_timestamp(item.findtext("pubDate"), now)
                if not title or not url or published is None:
                    continue
                exists = (await db.execute(select(Topic.id).where(Topic.source_url == url))).scalar_one_or_none()
                if exists:
                    continue
                topic = Topic(
                    title=title[:250],
                    source="google-news-rss",
                    source_url=url[:500],
                    language="pl",
                    priority=45,
                    explanation=(
                        f"Новостной сигнал от {published.isoformat()} по запросу «{keyword}»; "
                        "требует проверки первоисточника."
                    ),
                )
                db.add(topic)
                await db.flush()
                found += 1
    await db.commit()
    return f"Разведчик сохранил {found} новых сигналов Threads/новостей для контент-плана."


async def _create_topic_draft(db, topic: Topic) -> ThreadsPost | None:
    """Public text is research evidence, never a copy-and-question draft."""
    return None


async def _backfill_telegram_vacancy_facts(db) -> int:
    """Repair older imports created before labelled Telegram fields were parsed."""
    vacancies = list((await db.execute(select(Vacancy).where(Vacancy.source == "telegram"))).scalars())
    changed = 0
    for vacancy in vacancies:
        fields = extract_vacancy_fields(vacancy.description or "")
        updated = False
        for key in (
            "salary",
            "student_salary",
            "schedule",
            "contract_type",
            "housing",
            "transport",
            "requirements",
        ):
            current = getattr(vacancy, key)
            candidate = fields.get(key)
            richer = candidate and (
                not current
                or len(re.findall(r"\d+(?:[,.]\d+)?", candidate)) > len(re.findall(r"\d+(?:[,.]\d+)?", current))
                or (len(candidate) > len(current or "") + 8)
            )
            polluted = current and re.search(
                r"дохід|доход|income|за\s+місяц|за\s+месяц|за\s+ставк|^(?:💰|🎓|•)\s*(?:ставка|студентам)?\s*:",
                current,
                re.I,
            )
            if candidate and (
                richer
                or (
                    current != candidate
                    and (polluted or key in {"salary", "student_salary", "schedule", "contract_type"})
                )
            ):
                setattr(vacancy, key, fields[key])
                updated = True
        if fields.get("phone") and vacancy.contact != fields["phone"]:
            vacancy.contact = fields["phone"]
            updated = True
        if fields.get("location") and vacancy.location != fields["location"]:
            vacancy.location = fields["location"]
            updated = True
        if updated:
            changed += 1
            posts = list(
                (
                    await db.execute(
                        select(ThreadsPost).where(
                            ThreadsPost.vacancy_id == vacancy.id, ThreadsPost.status == PostStatus.AI_GENERATED
                        )
                    )
                ).scalars()
            )
            phone_contact = (
                await db.execute(select(AppSetting.value).where(AppSetting.key == "phone_contact"))
            ).scalar_one_or_none()
            for post in posts:
                latest_editor = (
                    await db.execute(
                        select(PostRevision.editor)
                        .where(PostRevision.post_id == post.id)
                        .order_by(desc(PostRevision.version))
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if latest_editor not in {
                    "telegram-import",
                    "telegram-fact-condensor",
                    "writer-agent",
                    "planner-agent",
                    "vacancy-fact-repair",
                }:
                    continue
                replacement = render_vacancy_post(vacancy, post.language, str(phone_contact or ""))
                if replacement == post.content:
                    continue
                post.version += 1
                post.content = replacement
                post.hook = vacancy.title[:500]
                post.reasoning_summary = (
                    "Автоматически обновлено: восстановлены все ставки и удалены Telegram-контакты."
                )
                db.add(
                    PostRevision(
                        post_id=post.id, version=post.version, content=replacement, editor="vacancy-fact-repair"
                    )
                )
    if changed:
        await activity(db, "telegram_vacancies_enriched", "vacancy", None, {"count": changed})
        await db.commit()
    return changed
