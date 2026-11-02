"""One fact source for generation, repair and review."""
from .content_quality import vacancy_fact_lines


def confirmed_facts(vacancy=None, account_contact=None):
    facts = vacancy_fact_lines(vacancy) if vacancy is not None else [
        "No vacancy selected. Do not state specific pay, employers, places, benefits or legal guarantees."
    ]
    if account_contact:
        facts.append(f"Account recruiter contact: {account_contact}")
    return facts


async def post_context(db, post, *, lock=False):
    from sqlalchemy import select
    from .models import AppSetting, Vacancy
    vacancy = await db.get(Vacancy, post.vacancy_id, populate_existing=True, with_for_update=lock) if post.vacancy_id else None
    query = select(AppSetting.value).where(AppSetting.key == "phone_contact")
    if lock:
        query = query.with_for_update()
    contact = (await db.execute(query)).scalar_one_or_none()
    return vacancy, confirmed_facts(vacancy, contact)


def review_fingerprint(post, vacancy, facts):
    import hashlib
    import json
    from datetime import timezone
    def timestamp(value):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    values = {
        "review_rules": 2, "version": post.version, "content": post.content,
        "language": post.language, "content_type": str(getattr(post.content_type, "value", post.content_type)),
        "vacancy_id": post.vacancy_id, "facts": facts,
        "source_confirmed_at": timestamp(vacancy.source_confirmed_at) if vacancy else None,
        "source_drifted_at": timestamp(vacancy.source_drifted_at) if vacancy else None,
        "vacancy_active": vacancy.active if vacancy else None,
    }
    return hashlib.sha256(json.dumps(values, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


async def account_contact(db):
    from sqlalchemy import select
    from .models import AppSetting
    return (await db.execute(select(AppSetting.value).where(AppSetting.key == "phone_contact"))).scalar_one_or_none()
