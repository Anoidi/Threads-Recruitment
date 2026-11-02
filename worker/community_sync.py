from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import desc, select, update

from app.ai import is_generic_reply
from app.comment_engagement import (
    analyze_comment,
    analyze_comment_with_ai,
    generate_variants,
    normalize_username,
)
from app.config import settings
from app.content_quality import extract_vacancy_fields
from app.enums import ContentType, MetricsStatus, PostStatus
from app.logging import get_logger
from app.models import (
    AppSetting,
    CandidateSignal,
    CommentEngagementCampaign,
    CommentEngagementVariant,
    ReplyDraft,
    ThreadsAccount,
    ThreadsInteraction,
    ThreadsPost,
    Vacancy,
)
from app.services import activity
from app.threads_client import ThreadsAPIError
from app.threads_status import mark_threads_error

log = get_logger("worker")

# How many new comments one community sync may analyse with the model.
COMMENT_MODEL_ANALYSES_PER_CYCLE = 5

# How many real answers the replier may write per community sync, and per
# manual agent run.
REPLY_DRAFTS_PER_CYCLE = 5
REPLY_DRAFTS_PER_RUN = 10


def _parse_timestamp(value) -> datetime:
    try:
        stamp = datetime.fromisoformat(str(value))
        return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)


async def _reply_scan_interval_minutes(db) -> int:
    """How long a post's conversation stays trusted after it was last read."""
    value = (
        await db.execute(select(AppSetting.value).where(AppSetting.key == "reply_scan_interval_minutes"))
    ).scalar_one_or_none()
    try:
        return max(5, min(int(value or 30), 240))
    except (TypeError, ValueError):
        return 30


async def _reply_scan_skip_ids(db, own_posts: list[dict]) -> set[str]:
    """Media ids whose conversation was read recently enough to skip."""
    from . import main as _m

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=await _m._reply_scan_interval_minutes(db))
    remote_ids = [str(post.get("id")) for post in own_posts if post.get("id")]
    if not remote_ids:
        return set()
    rows = list(
        (
            await db.execute(
                select(ThreadsPost.remote_id, ThreadsPost.last_reply_checked_at).where(
                    ThreadsPost.remote_id.in_(remote_ids),
                    ThreadsPost.last_reply_checked_at.is_not(None),
                )
            )
        ).all()
    )
    skip: set[str] = set()
    for remote_id, checked_at in rows:
        if checked_at is None:
            continue
        if checked_at.tzinfo is None:
            checked_at = checked_at.replace(tzinfo=timezone.utc)
        if checked_at >= cutoff:
            skip.add(str(remote_id))
    return skip


def _adopted_content_type(text: str) -> ContentType:
    """Infer the content type of a post published outside this manager."""
    fields = extract_vacancy_fields(text)
    if any(fields.get(key) for key in ("salary", "student_salary", "contract_type", "schedule", "location")):
        return ContentType.VACANCY
    return ContentType.ENGAGEMENT


def _mask_handles(text: str) -> str:
    """Remove @handles from a message before it is sent to a model."""
    return re.sub(r"@[\w.]{2,32}", "someone", text or "")


def _reply_facts(vacancy) -> list[str]:
    """Confirmed conditions of a vacancy, in Ukrainian labels."""
    from app.content_quality import structured_vacancy_facts

    facts = structured_vacancy_facts(vacancy)
    labels = (
        ("location", "Місто"),
        ("salary", "Ставка"),
        ("student_salary", "Ставка для студентів"),
        ("schedule", "Графік"),
        ("contract_type", "Договір"),
        ("housing", "Житло"),
        ("transport", "Транспорт"),
        ("requirements", "Вимоги"),
    )
    result = []
    for key, label in labels:
        value = getattr(facts, key, None)
        if value:
            result.append(f"{label}: {value}")
    return result


async def _draft_reply_for_interaction(db, interaction, post) -> str | None:
    """Ask the model for a real answer to one question."""
    from app.ai import AIService
    from . import main as _m

    facts: list[str] = []
    if post is not None and post.vacancy_id is not None:
        vacancy = await db.get(Vacancy, post.vacancy_id)
        if vacancy is not None:
            facts = _m._reply_facts(vacancy)
    return await AIService().draft_reply(interaction.message, facts, "uk")


async def draft_replies(db, limit: int = 10) -> str:
    """Prepare answers to the candidate questions that have none yet."""
    from app.audience_profile import is_question
    from . import main as _m

    interactions = list(
        (
            await db.execute(select(ThreadsInteraction).order_by(desc(ThreadsInteraction.occurred_at)).limit(80))
        ).scalars()
    )
    if not interactions:
        return "No candidate questions to answer yet."
    drafted_ids = set((await db.execute(select(ReplyDraft.interaction_id))).scalars())
    existing_drafts = {row.interaction_id: row for row in (await db.execute(select(ReplyDraft))).scalars()}
    pending = [
        item
        for item in interactions
        if is_question(item.message, item.classification)
        and (item.id not in drafted_ids or is_generic_reply(existing_drafts[item.id].content))
    ]
    drafted = 0
    for interaction in pending[:limit]:
        post = await db.get(ThreadsPost, interaction.post_id) if interaction.post_id else None
        text = await _m._draft_reply_for_interaction(db, interaction, post)
        if not text:
            continue
        current = existing_drafts.get(interaction.id)
        if current is not None:
            if current.status not in {"DRAFT", "REJECTED"}:
                continue
            current.content = text
            current.last_error = None
        else:
            db.add(ReplyDraft(interaction_id=interaction.id, content=text))
        drafted += 1
    if drafted:
        await db.commit()
    return f"Prepared {drafted} answer(s) to candidate questions."


async def sync_community(db, force_full: bool = False) -> None:
    """Read replies and mentions from Threads into the local database."""
    from . import main as _m

    account = (
        await db.execute(
            select(ThreadsAccount).where(ThreadsAccount.connected.is_(True)).order_by(desc(ThreadsAccount.id)).limit(1)
        )
    ).scalar_one_or_none()
    since = None
    if account is not None and account.last_success_at:
        last_success = account.last_success_at
        if last_success.tzinfo is None:
            last_success = last_success.replace(tzinfo=timezone.utc)
        since = last_success - timedelta(hours=1)
    token = (
        _m.decrypt_token(account.access_token_encrypted)
        if account is not None and account.access_token_encrypted and not account.is_mock
        else None
    )
    client = _m.get_threads_client(account.profile_id if account is not None and not account.is_mock else None, token)
    incoming = []
    optional_errors: list[ThreadsAPIError] = []
    own_posts: list[dict] = []
    skipped_ids: set[str] = set()
    for kind, loader in (("REPLY", client.replies), ("MENTION", client.mentions)):
        try:
            if kind == "REPLY":
                own_posts = await client.own_posts()
                skipped_ids = set() if force_full else await _m._reply_scan_skip_ids(db, own_posts)
                items = await client.replies(since, skip_media_ids=skipped_ids, posts=own_posts)
            else:
                items = await loader(since)
            incoming.extend((item, kind) for item in items)
        except ThreadsAPIError as exc:
            optional_errors.append(exc)
            log.warning("threads_optional_sync_unavailable", feature=kind.lower(), category=exc.category)
    if len(optional_errors) == 2:
        if account is not None:
            account.last_sync_at = datetime.now(timezone.utc)
            if optional_errors[0].category == "app_blocked":
                mark_threads_error(account, optional_errors[0])
            else:
                account.last_error = str(optional_errors[0])[:1000]
            await db.commit()
        raise optional_errors[0]
    now = datetime.now(timezone.utc)
    ai = _m.AIService()
    mode_row = (
        await db.execute(select(AppSetting.value).where(AppSetting.key == "comment_engagement_mode"))
    ).scalar_one_or_none()
    engagement_mode = str(mode_row or "MONITOR").upper()
    if engagement_mode not in {"MONITOR", "DRAFT", "AUTO"}:
        engagement_mode = "MONITOR"
    own_username = normalize_username(account.username) if account is not None else ""
    remote_post_ids = {str(data.get("root_post_id") or data.get("parent_id") or "") for data, _ in incoming}
    remote_post_ids.discard("")
    local_posts = {}
    if remote_post_ids:
        local_posts = {
            post.remote_id: post
            for post in (await db.execute(select(ThreadsPost).where(ThreadsPost.remote_id.in_(remote_post_ids)))).scalars()
            if post.remote_id
        }
    own_by_id = {str(item.get("id")): item for item in own_posts if item.get("id")}
    adopted = 0
    for remote_id in (sorted(remote_post_ids - set(local_posts)) if not settings.threads_mock_mode else []):
        source = own_by_id.get(remote_id)
        if source is None:
            continue
        text = str(source.get("text") or "").strip()
        if not text:
            continue
        adopted_post = ThreadsPost(
            remote_id=remote_id,
            content=text[:10000],
            hook=(text.splitlines()[0][:500] or None),
            content_type=_adopted_content_type(text),
            language=settings.default_language,
            status=PostStatus.PUBLISHED,
            published_at=_parse_timestamp(source.get("timestamp")),
            metrics_status=MetricsStatus.PENDING,
            reasoning_summary=(
                "Adopted from Threads: published outside this manager, "
                "so the content type is inferred and the text needs manual verification."
            ),
        )
        db.add(adopted_post)
        await db.flush()
        local_posts[remote_id] = adopted_post
        adopted += 1
    created = 0
    existing_count = 0
    model_analyses_left = COMMENT_MODEL_ANALYSES_PER_CYCLE
    reply_budget_left = REPLY_DRAFTS_PER_CYCLE
    for data, kind in incoming:
        username = normalize_username(data.get("username"))
        if own_username and username == own_username:
            continue
        existing = (
            await db.execute(select(ThreadsInteraction).where(ThreadsInteraction.remote_id == data["id"]))
        ).scalar_one_or_none()
        if existing:
            existing_count += 1
            if not existing.remote_parent_id and data.get("parent_id"):
                existing.remote_parent_id = str(data["parent_id"])
            root_id = str(data.get("root_post_id") or data.get("parent_id") or "")
            if not existing.post_id and root_id in local_posts:
                existing.post_id = local_posts[root_id].id
            existing.fetched_at = now
            continue
        result = ai.classify(data["text"])
        root_id = str(data.get("root_post_id") or data.get("parent_id") or "")
        local_post = local_posts.get(root_id)
        interaction = ThreadsInteraction(
            remote_id=data["id"],
            post_id=local_post.id if local_post else None,
            kind=kind,
            username=data["username"],
            message=data["text"],
            classification=result["classification"],
            confidence=result["confidence"],
            risk=result["risk"],
            remote_parent_id=str(data["parent_id"]) if data.get("parent_id") else None,
            fetched_at=now,
            occurred_at=_parse_timestamp(data.get("timestamp")),
        )
        db.add(interaction)
        await db.flush()
        created += 1
        if result["suggested_reply"]:
            from app.audience_profile import is_question

            reply_text = result["suggested_reply"]
            if reply_budget_left > 0 and is_question(data["text"], result["classification"]):
                reply_budget_left -= 1
                drafted = await _m._draft_reply_for_interaction(db, interaction, local_post)
                if drafted:
                    reply_text = drafted
            db.add(ReplyDraft(interaction_id=interaction.id, content=reply_text))
        if result["candidate_signal"]:
            db.add(
                CandidateSignal(
                    interaction_id=interaction.id,
                    vacancy_id=local_post.vacancy_id if local_post else None,
                    username=interaction.username,
                    intent=result["classification"],
                    confidence=result["confidence"],
                )
            )
            await activity(db, "candidate_signal_created", "interaction", interaction.id)
        analysis_error = None
        if model_analyses_left > 0:
            model_analyses_left -= 1
            try:
                analysis = await analyze_comment_with_ai(data["text"])
            except Exception as exc:
                analysis = analyze_comment(data["text"])
                analysis_error = f"{type(exc).__name__}: {str(exc)[:400]}"
        else:
            analysis = analyze_comment(data["text"])
            analysis_error = "deterministic analysis: the model budget for this cycle was used up"
        campaign = CommentEngagementCampaign(
            source_post_id=local_post.id if local_post else None,
            source_remote_id=root_id or None,
            mode=engagement_mode,
            status=(
                "ANALYZED"
                if engagement_mode == "MONITOR"
                else ("DRAFT" if engagement_mode == "DRAFT" else "AUTO_PENDING")
            ),
            analysis={
                **analysis,
                "interaction_id": interaction.id,
                "username": data["username"],
                "remote_id": data["id"],
            },
            error=analysis_error,
        )
        db.add(campaign)
        await db.flush()
        if engagement_mode in {"DRAFT", "AUTO"}:
            context = local_post.content if local_post else None
            for variant in generate_variants(
                username=data["username"],
                comment_text=data["text"],
                topic=analysis["topic"],
                source_context=context,
            ):
                db.add(
                    CommentEngagementVariant(
                        campaign_id=campaign.id,
                        style=variant["style"],
                        content=variant["content"],
                        character_count=variant["character_count"],
                        content_hash=variant["content_hash"],
                    )
                )
    checked_ids = [
        str(item.get("id"))
        for item in own_posts
        if item.get("id") and item.get("has_replies") and str(item.get("id")) not in skipped_ids
    ]
    if checked_ids:
        await db.execute(
            update(ThreadsPost).where(ThreadsPost.remote_id.in_(checked_ids)).values(last_reply_checked_at=now)
        )
    if account is not None:
        account.last_sync_at = now
        account.last_success_at = now
        account.last_error = None
    await activity(
        db,
        "community_sync_completed",
        "interaction",
        None,
        {
            "cursor": since.isoformat() if since else None,
            "own_posts": len(own_posts),
            "posts_with_replies": sum(1 for item in own_posts if item.get("has_replies")),
            "posts_skipped_recently": len(skipped_ids),
            "replies_fetched": len(incoming),
            "replies_created": created,
            "replies_existing": existing_count,
            "dropped_by_cursor": int(getattr(client, "cursor_dropped", 0) or 0),
            "adopted_posts": adopted,
        },
    )
    await db.commit()
