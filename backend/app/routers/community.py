"""Community inbox, comment engagement campaigns, and candidate signals routes."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..comment_engagement import (
    MentionManager,
    analyze_comment,
    analyze_comment_with_ai,
    content_hash,
    generate_variants,
    normalize_username,
    validate_engagement_text,
)
from ..database import get_db
from ..enums import CandidateSignalStatus, ContentType, InteractionKind, PostStatus, RiskLevel
from ..models import (
    CandidateSignal,
    CommentEngagementCampaign,
    CommentEngagementVariant,
    CommentMentionHistory,
    CommentMentionPreference,
    ReplyDraft,
    ThreadsInteraction,
    ThreadsPost,
)
from ..queue import enqueue
from ..schemas import (
    CandidateRead,
    CandidateSignalRead,
    CommentEngagementApproveRequest,
    CommentEngagementCampaignRead,
    CommentEngagementGenerateRequest,
    CommunityAction,
    InteractionRead,
    MentionPreferenceUpdate,
    SignalStatusUpdate,
)
from ..services import activity, convert_signal
from .recruitment import SignalConvertRequest

Db = Annotated[AsyncSession, Depends(get_db)]
router = APIRouter()


@router.get("/threads/community", response_model=list[InteractionRead])
async def community(db: Db, tab: str = "all"):
    query = select(ThreadsInteraction)
    mapping = {
        "replies": ThreadsInteraction.kind == InteractionKind.REPLY,
        "mentions": ThreadsInteraction.kind == InteractionKind.MENTION,
        "needs_review": ThreadsInteraction.needs_review.is_(True),
        "answered": ThreadsInteraction.answered.is_(True),
        "ignored": ThreadsInteraction.ignored.is_(True),
        "high_risk": ThreadsInteraction.risk == RiskLevel.HIGH,
    }
    if tab in mapping:
        query = query.where(mapping[tab])
    if tab == "candidate_signals":
        query = query.join(CandidateSignal, CandidateSignal.interaction_id == ThreadsInteraction.id)
    items = list((await db.execute(query.order_by(desc(ThreadsInteraction.occurred_at)))).scalars())
    if items:
        ids = [item.id for item in items]
        drafts = {row.interaction_id: row for row in (await db.execute(select(ReplyDraft).where(ReplyDraft.interaction_id.in_(ids)))).scalars()}
        signal_ids = set((await db.execute(select(CandidateSignal.interaction_id).where(CandidateSignal.interaction_id.in_(ids)))).scalars())
        for item in items:
            draft = drafts.get(item.id)
            item.reply_status = draft.status if draft else None
            item.suggested_reply = draft.content if draft else None
            item.candidate_signal = item.id in signal_ids
    return items


@router.post("/threads/community/sync", status_code=202)
async def sync_community(db: Db):
    job = await enqueue(db, "sync_community", {}, idempotency_key=f"sync-community:{datetime.now(timezone.utc).strftime('%Y%m%d%H%M')}")
    return {"job_id": job.id, "status": job.status}


@router.post("/threads/community/{interaction_id}/action", response_model=InteractionRead)
async def community_action(interaction_id: int, data: CommunityAction, db: Db):
    item = await db.get(ThreadsInteraction, interaction_id)
    if not item:
        raise HTTPException(404, "Interaction not found")
    if data.action == "ignore":
        item.ignored = True
        item.needs_review = False
    elif data.action == "escalate":
        item.risk = RiskLevel.HIGH
        item.needs_review = True
    elif data.action == "mark_candidate":
        existing = (await db.execute(select(CandidateSignal).where(CandidateSignal.interaction_id == item.id))).scalar_one_or_none()
        if not existing:
            db.add(
                CandidateSignal(
                    interaction_id=item.id,
                    vacancy_id=((await db.get(ThreadsPost, item.post_id)).vacancy_id if item.post_id else None),
                    username=item.username,
                    intent=item.classification or "MANUAL",
                    confidence=item.confidence or 1.0,
                )
            )
    elif data.action == "approve_reply":
        if item.risk == RiskLevel.HIGH:
            raise HTTPException(409, "High-risk interactions cannot be auto-approved")
        draft = (await db.execute(select(ReplyDraft).where(ReplyDraft.interaction_id == item.id))).scalar_one_or_none()
        if not draft:
            draft = ReplyDraft(interaction_id=item.id, content=data.content or "Дякуємо за повідомлення.", status="APPROVED")
            db.add(draft)
        else:
            if draft.remote_id:
                raise HTTPException(409, "This reply was already sent to Threads")
            draft.content = data.content or draft.content
            draft.status = "APPROVED"
            draft.last_error = None
        draft.updated_at = datetime.now(timezone.utc)
        item.needs_review = False
    elif data.action == "reject_reply":
        draft = (await db.execute(select(ReplyDraft).where(ReplyDraft.interaction_id == item.id))).scalar_one_or_none()
        if draft and not draft.remote_id:
            draft.status = "REJECTED"
        item.needs_review = False
    await activity(db, f"community_{data.action}", "interaction", item.id)
    await db.commit()
    await db.refresh(item)
    return item


@router.post("/threads/community/{interaction_id}/reply", status_code=202)
async def send_reply(interaction_id: int, db: Db):
    """Queue an approved reply for manual one-click delivery to Threads."""
    item = await db.get(ThreadsInteraction, interaction_id)
    if not item:
        raise HTTPException(404, "Interaction not found")
    if item.risk == RiskLevel.HIGH:
        raise HTTPException(409, "High-risk interactions must be escalated, not replied to")
    if not item.remote_parent_id:
        raise HTTPException(409, "This interaction has no Threads reply target; synchronize the inbox again")
    draft = (await db.execute(select(ReplyDraft).where(ReplyDraft.interaction_id == item.id))).scalar_one_or_none()
    if not draft:
        raise HTTPException(409, "Approve a suggested reply first")
    if draft.remote_id:
        raise HTTPException(409, "This reply was already sent to Threads")
    if draft.status != "APPROVED":
        raise HTTPException(409, f"Reply must be approved before sending (current: {draft.status})")
    job = await enqueue(
        db,
        "publish_reply",
        {"draft_id": draft.id, "interaction_id": item.id},
        idempotency_key=f"publish-reply:{draft.id}:{draft.updated_at.isoformat()}",
    )
    if job.status == "FAILED":
        raise HTTPException(409, "Previous delivery attempt failed; edit and approve the reply again before retrying")
    return {"job_id": job.id, "status": job.status}


@router.get("/threads/comment-engagement", response_model=list[CommentEngagementCampaignRead])
async def comment_engagement_campaigns(db: Db, status_filter: str | None = Query(None, alias="status")):
    """Operator inbox for real-comment analysis and source-grounded variants."""
    query = select(CommentEngagementCampaign)
    if status_filter:
        query = query.where(CommentEngagementCampaign.status == status_filter.upper())
    campaigns = list((await db.execute(query.order_by(desc(CommentEngagementCampaign.created_at)).limit(200))).scalars())
    if not campaigns:
        return []
    ids = [item.id for item in campaigns]
    variants = list(
        (
            await db.execute(
                select(CommentEngagementVariant)
                .where(CommentEngagementVariant.campaign_id.in_(ids))
                .order_by(CommentEngagementVariant.id)
            )
        ).scalars()
    )
    by_campaign: dict[int, list[CommentEngagementVariant]] = {}
    for variant in variants:
        by_campaign.setdefault(variant.campaign_id, []).append(variant)
    for campaign in campaigns:
        campaign.variants = by_campaign.get(campaign.id, [])
    return campaigns


@router.post("/threads/comment-engagement/generate", response_model=CommentEngagementCampaignRead)
async def generate_comment_engagement(data: CommentEngagementGenerateRequest, db: Db):
    interaction = await db.get(ThreadsInteraction, data.interaction_id)
    if not interaction:
        raise HTTPException(404, "Interaction not found")
    existing = (
        await db.execute(
            select(CommentEngagementCampaign)
            .where(CommentEngagementCampaign.analysis["interaction_id"].as_integer() == interaction.id)
            .order_by(desc(CommentEngagementCampaign.id))
            .limit(1)
        )
    ).scalar_one_or_none()
    source_context = None
    if interaction.post_id:
        source = await db.get(ThreadsPost, interaction.post_id)
        source_context = source.content if source else None
    try:
        analysis = await analyze_comment_with_ai(interaction.message)
    except Exception as exc:
        analysis = analyze_comment(interaction.message)
        campaign_error = f"{type(exc).__name__}: {str(exc)[:400]}"
    else:
        campaign_error = None
    campaign = existing or CommentEngagementCampaign(source_post_id=interaction.post_id, source_remote_id=interaction.remote_parent_id)
    campaign.mode = data.mode
    campaign.status = "DRAFT" if data.mode == "DRAFT" else ("AUTO_PENDING" if data.mode == "AUTO" else "ANALYZED")
    campaign.analysis = {**analysis, "interaction_id": interaction.id, "username": interaction.username, "remote_id": interaction.remote_id}
    campaign.error = campaign_error
    if not existing:
        db.add(campaign)
        await db.flush()
    old = list((await db.execute(select(CommentEngagementVariant).where(CommentEngagementVariant.campaign_id == campaign.id))).scalars())
    for variant in old:
        await db.delete(variant)
    for variant in generate_variants(username=interaction.username, comment_text=interaction.message, topic=analysis["topic"], source_context=source_context):
        db.add(
            CommentEngagementVariant(
                campaign_id=campaign.id,
                style=variant["style"],
                content=variant["content"],
                character_count=variant["character_count"],
                content_hash=variant["content_hash"],
            )
        )
    await activity(db, "comment_engagement_generated", "campaign", campaign.id, {"interaction_id": interaction.id, "mode": data.mode})
    await db.commit()
    await db.refresh(campaign)
    campaign.variants = list(
        (await db.execute(select(CommentEngagementVariant).where(CommentEngagementVariant.campaign_id == campaign.id).order_by(CommentEngagementVariant.id))).scalars()
    )
    return campaign


@router.post("/threads/comment-engagement/{campaign_id}/approve", response_model=CommentEngagementCampaignRead)
async def approve_comment_engagement(campaign_id: int, data: CommentEngagementApproveRequest, db: Db):
    campaign = await db.get(CommentEngagementCampaign, campaign_id)
    if not campaign:
        raise HTTPException(404, "Comment engagement campaign not found")
    variant = await db.get(CommentEngagementVariant, data.variant_id)
    if not variant or variant.campaign_id != campaign.id:
        raise HTTPException(404, "Variant not found for this campaign")
    text = validate_engagement_text(data.content or variant.content, expected_usernames=[campaign.analysis.get("username", "")])
    variant.content = text
    variant.character_count = len(text)
    variant.content_hash = content_hash(text)
    variant.status = "APPROVED"
    variant.selected = True
    campaign.status = "APPROVED"
    campaign.approved_at = datetime.now(timezone.utc)
    post = ThreadsPost(
        content=text,
        content_type=ContentType.ENGAGEMENT,
        language="uk",
        status=PostStatus.REVIEW,
        account_id=None,
        reasoning_summary=f"Comment engagement campaign #{campaign.id}; source comment {campaign.analysis.get('remote_id', '')}",
        idempotency_key=f"comment-engagement:{campaign.id}:{variant.id}",
    )
    db.add(post)
    await db.flush()
    campaign.analysis = {**campaign.analysis, "draft_post_id": post.id, "selected_variant_id": variant.id}
    await activity(db, "comment_engagement_approved", "campaign", campaign.id, {"draft_post_id": post.id, "variant_id": variant.id})
    await db.commit()
    await db.refresh(campaign)
    campaign.variants = [variant]
    return campaign


@router.put("/threads/comment-engagement/mention-preferences", status_code=200)
async def update_mention_preference(data: MentionPreferenceUpdate, db: Db):
    normalized = normalize_username(data.username)
    if not normalized:
        raise HTTPException(422, "Username is empty or invalid")
    row = (await db.execute(select(CommentMentionPreference).where(CommentMentionPreference.normalized_username == normalized))).scalar_one_or_none()
    if row is None:
        row = CommentMentionPreference(normalized_username=normalized)
        db.add(row)
    row.opted_out = data.opted_out
    row.note = data.note
    await db.commit()
    return {"username": "@" + normalized, "opted_out": row.opted_out}


@router.get("/threads/comment-engagement/{campaign_id}/mention-plan")
async def comment_engagement_mention_plan(campaign_id: int, db: Db):
    """Return only observed, real commenters eligible for a mention."""
    campaign = await db.get(CommentEngagementCampaign, campaign_id)
    if not campaign:
        raise HTTPException(404, "Comment engagement campaign not found")
    interaction_id = campaign.analysis.get("interaction_id")
    interaction = await db.get(ThreadsInteraction, int(interaction_id)) if interaction_id else None
    if not interaction:
        return {"verified_usernames": [], "eligible_usernames": [], "reason": "source interaction is unavailable"}
    participants = [interaction.username]
    if campaign.source_post_id:
        participants.extend((await db.execute(select(ThreadsInteraction.username).where(ThreadsInteraction.post_id == campaign.source_post_id))).scalars())
    normalized = list(dict.fromkeys(normalize_username(item) for item in participants if normalize_username(item)))
    preferences = list((await db.execute(select(CommentMentionPreference).where(CommentMentionPreference.normalized_username.in_(normalized)))).scalars()) if normalized else []
    opted_out = [row.normalized_username for row in preferences if row.opted_out]
    history = (
        list(
            (
                await db.execute(
                    select(CommentMentionHistory)
                    .where(CommentMentionHistory.normalized_username.in_(normalized))
                    .order_by(desc(CommentMentionHistory.created_at))
                )
            ).mappings()
        )
        if normalized
        else []
    )
    eligible = MentionManager(cooldown_hours=24, max_group_size=3).select(normalized, opted_out=opted_out, history=history)
    return {
        "verified_usernames": ["@" + item for item in normalized],
        "eligible_usernames": ["@" + item for item in eligible],
        "reason": "verified from real captured Threads comments; cooldown and opt-out rules applied",
    }


@router.get("/threads/candidate-signals", response_model=list[CandidateSignalRead])
async def list_signals(db: Db, status_filter: CandidateSignalStatus | None = Query(None, alias="status")):
    query = select(CandidateSignal)
    if status_filter:
        query = query.where(CandidateSignal.status == status_filter)
    return list((await db.execute(query.order_by(desc(CandidateSignal.created_at)))).scalars())


@router.patch("/threads/candidate-signals/{signal_id}", response_model=CandidateSignalRead)
async def update_signal(signal_id: int, data: SignalStatusUpdate, db: Db):
    signal = await db.get(CandidateSignal, signal_id)
    if not signal:
        raise HTTPException(404, "Candidate signal not found")
    signal.status = data.status
    await db.commit()
    await db.refresh(signal)
    return signal


@router.post("/threads/candidate-signals/{signal_id}/convert", response_model=CandidateRead)
async def convert_candidate_signal(signal_id: int, data: SignalConvertRequest, db: Db):
    """Create a candidate record from a signal, with a confirmed contact."""
    signal = await db.get(CandidateSignal, signal_id)
    if not signal:
        raise HTTPException(404, "Candidate signal not found")
    return await convert_signal(
        db,
        signal,
        channel=data.channel.value,
        contact_value=data.contact_value.strip(),
        confirmed_by=data.confirmed_by,
        consent_given=data.consent_given,
        notes=data.notes,
    )
