"""Autonomous deterministic content/data audit, separate from operations Supervisor."""
from __future__ import annotations
from dataclasses import asdict, dataclass
from datetime import datetime, timezone, timedelta
import hashlib
from sqlalchemy import select, func
from fastapi import HTTPException
from .models import ThreadsPost, Vacancy, AppSetting, ActivityEvent, BackgroundJob
from .enums import PostStatus, JobStatus
from .post_validation import validate_post_for_publication
from .content_context import account_contact
from .vacancy_freshness import DRIFTED, STALE, UNVERIFIED, vacancy_freshness
from .vacancy_inspection import inspection

@dataclass(slots=True)
class QualityFinding:
    severity: str
    rule: str
    title: str
    detail: str
    entity_type: str
    entity_id: int
    action: str = 'manual_review'
    fingerprint: str = ''
    def __post_init__(self):
        self.fingerprint = hashlib.sha256('|'.join(map(str, (self.rule, self.entity_type, self.entity_id, self.detail))).encode()).hexdigest()[:20]
    def as_dict(self): return asdict(self)

from .vacancy_freshness import DRIFTED, STALE, UNVERIFIED, vacancy_freshness


async def scan_quality(db):
    findings: list[QualityFinding] = []
    vacancies = list((await db.execute(select(Vacancy).where(Vacancy.active.is_(True)))).scalars())
    for vacancy in vacancies:
        verified_at = (await db.execute(select(func.max(ActivityEvent.created_at)).where(ActivityEvent.entity_type == 'vacancy', ActivityEvent.entity_id == vacancy.id, ActivityEvent.action == 'vacancy_source_verified'))).scalar_one_or_none()
        report = inspection(vacancy, vacancies, verified_at=verified_at)
        for issue in report['issues']:
            rule = 'vacancy_missing_fact' if 'ставк' not in issue else 'vacancy_missing_salary'
            findings.append(QualityFinding('high' if rule == 'vacancy_missing_salary' else 'medium', rule, 'Вакансия требует проверки', issue, 'vacancy', vacancy.id))
        for duplicate in report['duplicates']:
            findings.append(QualityFinding('medium', 'vacancy_possible_duplicate', 'Возможный дубль вакансии', f"Совпадает с вакансией #{duplicate['id']}.", 'vacancy', vacancy.id))
        if report['stale']:
            findings.append(QualityFinding('low', 'vacancy_source_stale', 'Источник вакансии устарел', f"Источник не проверялся {report['source_age_days']} дней.", 'vacancy', vacancy.id))
        # The source freshness is a state, not a derived guess: a source that
        # changed after publication means the published conditions are wrong.
        freshness = vacancy_freshness(vacancy)
        if freshness.status == DRIFTED:
            findings.append(QualityFinding('high', 'vacancy_source_drifted', 'Условия в источнике изменились', freshness.reason, 'vacancy', vacancy.id, action='verify_before_publish'))
        elif freshness.status == STALE:
            findings.append(QualityFinding('medium', 'vacancy_source_stale', 'Источник вакансии устарел', freshness.reason, 'vacancy', vacancy.id, action='verify_source'))
        elif freshness.status == UNVERIFIED:
            findings.append(QualityFinding('low', 'vacancy_source_unverified', 'Источник вакансии не подтверждён', freshness.reason, 'vacancy', vacancy.id, action='verify_source'))
    posts = list((await db.execute(select(ThreadsPost).where(ThreadsPost.status.in_([PostStatus.AI_GENERATED, PostStatus.DRAFT, PostStatus.REVIEW])))).scalars())
    for post in posts:
        vacancy = await db.get(Vacancy, post.vacancy_id) if post.vacancy_id else None
        try:
            validate_post_for_publication(content=post.content, language=post.language, vacancy=vacancy, content_type=post.content_type, account_contact=await account_contact(db))
        except HTTPException as exc:
            findings.append(QualityFinding('high', 'ai_draft_quality_blocked', 'AI-черновик не готов', str(exc.detail), 'post', post.id, action='edit_before_review'))
    failures = (await db.execute(select(BackgroundJob.kind, func.count(BackgroundJob.id)).where(BackgroundJob.status == JobStatus.FAILED, BackgroundJob.updated_at >= datetime.now(timezone.utc) - timedelta(days=1)).group_by(BackgroundJob.kind))).all()
    for kind, count in failures:
        if count >= 10:
            findings.append(QualityFinding('high', 'repeated_job_failure', 'Повторяющиеся ошибки фоновых задач', f'{kind}: {count} ошибок за 24 часа. Требуется диагностика интеграции.', 'system', 0))
    return {'checked_at': datetime.now(timezone.utc).isoformat(), 'findings': [f.as_dict() for f in findings], 'summary': {
        'total': len(findings), 'critical': sum(f.severity == 'critical' for f in findings),
        'high': sum(f.severity == 'high' for f in findings), 'medium': sum(f.severity == 'medium' for f in findings),
        'low': sum(f.severity == 'low' for f in findings), 'mutations': 0,
    }}

async def run_quality_guard(db):
    """Persist an autonomous scan; never mutate vacancy facts or post states."""
    report = await scan_quality(db)
    row = (await db.execute(select(AppSetting).where(AppSetting.key == 'quality_guard_report'))).scalar_one_or_none()
    if row is None:
        db.add(AppSetting(key='quality_guard_report', value=report))
    else:
        row.value = report
    db.add(ActivityEvent(category='threads', action='quality_guard_scan', entity_type='system', detail=report['summary']))
    await db.commit()
    return report
