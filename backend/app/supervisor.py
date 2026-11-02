"""Deterministic Supervisor/Guard for agent and worker safety.

The supervisor observes persisted state and applies only a small allowlist of
local, reversible repairs. It never calls Threads, sends replies, changes
secrets, runs migrations, or edits code. Model-based diagnosis can be layered
on top later, but operational decisions stay in these typed rules.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import hashlib
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .enums import JobStatus, PostStatus
from .models import AppSetting, BackgroundJob, SyncState, ThreadsAccount, ThreadsPost, Vacancy
from .post_validation import validate_post_for_publication
from .content_context import account_contact
from .services import activity


SUPERVISOR_RULE_VERSION = "1"
STALE_JOB_AFTER = timedelta(minutes=10)
STALE_SCHEDULE_AFTER = timedelta(minutes=5)


@dataclass(slots=True)
class SupervisorFinding:
    severity: str
    rule: str
    title: str
    detail: str
    action: str
    auto_fixable: bool
    entity_type: str | None = None
    entity_id: int | None = None
    fixed: bool = False
    fingerprint: str = ""

    def __post_init__(self) -> None:
        raw = "|".join(
            str(value)
            for value in (
                SUPERVISOR_RULE_VERSION,
                self.rule,
                self.entity_type or "",
                self.entity_id or "",
                self.detail,
            )
        )
        self.fingerprint = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _setting_bool(values: dict[str, Any], key: str, default: bool = False) -> bool:
    value = values.get(key, default)
    if isinstance(value, bool):
        return value
    return str(value).casefold() in {"1", "true", "yes", "on"}


async def _settings(db: AsyncSession) -> dict[str, Any]:
    rows = (await db.execute(select(AppSetting).where(AppSetting.key.in_(
        ("auto_post", "auto_reply", "auto_external_comment", "auto_community_sync", "auto_content_plan")
    )))).scalars()
    return {row.key: row.value for row in rows}


def _finding(
    *, severity: str, rule: str, title: str, detail: str, action: str,
    auto_fixable: bool, entity_type: str | None = None, entity_id: int | None = None,
) -> SupervisorFinding:
    return SupervisorFinding(
        severity=severity,
        rule=rule,
        title=title,
        detail=detail,
        action=action,
        auto_fixable=auto_fixable,
        entity_type=entity_type,
        entity_id=entity_id,
    )


async def scan_supervisor(db: AsyncSession, *, apply_fixes: bool = False) -> dict[str, Any]:
    """Inspect the durable system state and optionally apply safe repairs."""
    now = datetime.now(timezone.utc)
    settings = await _settings(db)
    findings: list[SupervisorFinding] = []

    account = (await db.execute(
        select(ThreadsAccount)
        .where(ThreadsAccount.connected.is_(True))
        .order_by(ThreadsAccount.id.desc())
        .limit(1)
    )).scalar_one_or_none()
    if account is not None:
        permissions = set(account.permissions or [])
        if "threads_manage_mentions" not in permissions:
            findings.append(_finding(
                severity="medium",
                rule="meta_mentions_scope_missing",
                title="Упоминания Threads недоступны",
                detail="Аккаунт не имеет threads_manage_mentions; синхронизация внешних упоминаний может отвечать 403.",
                action="manual_review",
                auto_fixable=False,
                entity_type="threads_account",
                entity_id=account.id,
            ))
        if account.api_status not in {"READY", "UNKNOWN"}:
            findings.append(_finding(
                severity="high",
                rule="threads_account_not_ready",
                title="Threads account не готов для внешних операций",
                detail=f"Текущий api_status={account.api_status}; автоматическую публикацию следует приостановить.",
                action="pause_auto_post",
                auto_fixable=_setting_bool(settings, "auto_post"),
                entity_type="threads_account",
                entity_id=account.id,
            ))

    # This setting is exposed in the UI but has no implemented outbound path.
    # Do not silently switch it off: report the mismatch for an operator.
    if _setting_bool(settings, "auto_external_comment"):
        findings.append(_finding(
            severity="high",
            rule="unsupported_external_comment_automation",
            title="Включена неподдерживаемая автоматизация внешних комментариев",
            detail="Настройка auto_external_comment=true, но безопасного реализованного пути отправки внешних комментариев нет.",
            action="manual_review",
            auto_fixable=False,
        ))

    jobs = list((await db.execute(
        select(BackgroundJob).where(BackgroundJob.status == JobStatus.RUNNING).with_for_update()
    )).scalars())
    for job in jobs:
        locked_at = job.locked_at
        if locked_at is None:
            continue
        if locked_at.tzinfo is None:
            locked_at = locked_at.replace(tzinfo=timezone.utc)
        if locked_at > now - STALE_JOB_AFTER:
            continue
        external = job.kind in {"publish_post", "publish_reply"}
        findings.append(_finding(
            severity="critical" if external else "high",
            rule="stale_running_job",
            title="Зависшая background job",
            detail=f"Job {job.kind} выполняется дольше {int(STALE_JOB_AFTER.total_seconds() // 60)} минут.",
            action="recover_job",
            auto_fixable=True,
            entity_type="background_job",
            entity_id=job.id,
        ))

    scheduled = list((await db.execute(
        select(ThreadsPost).where(
            ThreadsPost.status == PostStatus.SCHEDULED,
            ThreadsPost.scheduled_at.is_not(None),
        ).with_for_update()
    )).scalars())
    for post in scheduled:
        scheduled_at = post.scheduled_at
        if scheduled_at is None:
            continue
        if scheduled_at.tzinfo is None:
            scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)
        if scheduled_at > now - STALE_SCHEDULE_AFTER:
            continue
        findings.append(_finding(
            severity="high",
            rule="overdue_schedule",
            title="Просроченное расписание публикации",
            detail=f"Пост был запланирован на {scheduled_at.isoformat()}, но не был опубликован.",
            action="quarantine_schedule",
            auto_fixable=True,
            entity_type="post",
            entity_id=post.id,
        ))

    candidates = list((await db.execute(
        select(ThreadsPost).where(ThreadsPost.status.in_([PostStatus.APPROVED, PostStatus.SCHEDULED])).with_for_update()
    )).scalars())
    vacancies: dict[int, Vacancy | None] = {}
    for post in candidates:
        vacancy = None
        if post.vacancy_id is not None:
            if post.vacancy_id not in vacancies:
                vacancies[post.vacancy_id] = await db.get(Vacancy, post.vacancy_id)
            vacancy = vacancies[post.vacancy_id]
        try:
            validate_post_for_publication(
                content=post.content,
                language=post.language,
                vacancy=vacancy,
                content_type=post.content_type,
                account_contact=await account_contact(db),
            )
        except HTTPException as exc:
            findings.append(_finding(
                severity="critical",
                rule="invalid_publish_candidate",
                title="Пост не проходит финальную проверку",
                detail=str(exc.detail),
                action="quarantine_post",
                auto_fixable=True,
                entity_type="post",
                entity_id=post.id,
            ))

    if apply_fixes:
        await _apply_safe_repairs(db, findings, now)
        await db.commit()

    await activity(db, "supervisor_scan", "supervisor", None, {
        "apply_fixes": apply_fixes,
        "finding_count": len(findings),
        "auto_fixable": sum(1 for item in findings if item.auto_fixable),
        "fixed": sum(1 for item in findings if item.fixed),
        "rules": sorted({item.rule for item in findings}),
    })
    await db.commit()
    return {
        "checked_at": now,
        "apply_fixes": apply_fixes,
        "findings": [item.as_dict() for item in findings],
        "summary": {
            "total": len(findings),
            "critical": sum(1 for item in findings if item.severity == "critical"),
            "high": sum(1 for item in findings if item.severity == "high"),
            "medium": sum(1 for item in findings if item.severity == "medium"),
            "fixed": sum(1 for item in findings if item.fixed),
        },
    }


async def _apply_safe_repairs(db: AsyncSession, findings: list[SupervisorFinding], now: datetime) -> None:
    """Apply only local, idempotent repairs from the explicit allowlist."""
    for finding in findings:
        if not finding.auto_fixable:
            continue
        if finding.action == "pause_auto_post":
            setting = (await db.execute(select(AppSetting).where(AppSetting.key == "auto_post").with_for_update())).scalar_one_or_none()
            if setting is not None and setting.value is not False:
                setting.value = False
                finding.fixed = True
                await activity(db, "supervisor_auto_post_paused", "threads_account", finding.entity_id, {"rule": finding.rule})
            continue
        if finding.action == "recover_job" and finding.entity_id is not None:
            job = await db.get(BackgroundJob, finding.entity_id, with_for_update=True)
            if job is None or job.status != JobStatus.RUNNING:
                finding.fixed = True
                continue
            external = job.kind in {"publish_post", "publish_reply"}
            job.status = JobStatus.FAILED if external or job.attempts >= 3 else JobStatus.PENDING
            job.locked_at = None
            job.run_at = now if job.status == JobStatus.PENDING else job.run_at
            job.last_error = (
                "Supervisor detected an interrupted external delivery; verify Threads before retrying."
                if external else "Supervisor recovered a stale worker lock."
            )
            finding.fixed = True
            await activity(db, "supervisor_job_recovered", "job", job.id, {"kind": job.kind, "status": job.status.value, "external": external})
            continue
        if finding.action == "quarantine_schedule" and finding.entity_id is not None:
            post = await db.get(ThreadsPost, finding.entity_id, with_for_update=True)
            if post is None or post.status != PostStatus.SCHEDULED:
                finding.fixed = True
                continue
            post.status = PostStatus.APPROVED
            post.scheduled_at = None
            post.failure_reason = "Supervisor снял просроченное расписание; требуется ручная проверка."
            jobs = list((await db.execute(select(BackgroundJob).where(
                BackgroundJob.kind == "publish_post",
                BackgroundJob.status.in_([JobStatus.PENDING, JobStatus.RUNNING]),
            ).with_for_update())).scalars())
            for job in jobs:
                if int(job.payload.get("post_id", -1)) == post.id and job.status == JobStatus.PENDING:
                    job.status = JobStatus.FAILED
                    job.last_error = "Supervisor cancelled an overdue schedule"
            finding.fixed = True
            await activity(db, "supervisor_schedule_quarantined", "post", post.id, {"rule": finding.rule})
            continue
        if finding.action == "quarantine_post" and finding.entity_id is not None:
            post = await db.get(ThreadsPost, finding.entity_id, with_for_update=True)
            if post is None or post.status not in {PostStatus.APPROVED, PostStatus.SCHEDULED}:
                finding.fixed = True
                continue
            post.status = PostStatus.FAILED
            post.failure_reason = f"Supervisor blocked publication: {finding.detail}"[:1000]
            post.scheduled_at = None
            finding.fixed = True
            await activity(db, "supervisor_post_quarantined", "post", post.id, {"rule": finding.rule, "reason": finding.detail})


async def run_supervisor_scan(db: AsyncSession) -> dict[str, Any]:
    """Worker entrypoint: scan and apply only safe repairs."""
    return await scan_supervisor(db, apply_fixes=True)
