"""Inspectable editorial preferences derived from explicit human feedback."""
import re
from sqlalchemy import select
from .models import ActivityEvent, AppSetting

RULES = {
    "shorter": "Пишіть коротше: прибирайте повтори та зайві вступи, зберігайте підтверджені факти.",
    "conversational": "Використовуйте живу розмовну мову рекрутера; уникайте корпоративних кліше.",
    "hook": "Починайте з конкретного питання або сильного короткого гачка без вигаданої терміновості.",
    "cta": "Завершуйте конкретним закликом до дії або доречним питанням для обговорення.",
    "format": "Розділяйте умови короткими рядками; не дублюйте ставки та позначення валюти.",
}

def redact(text):
    return re.sub(r"https?://\S+|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|@\w+|\+?\d[\d ()-]{7,}\d", "[redacted]", text or "")[:2000]

async def memory(db):
    events = list((await db.execute(select(ActivityEvent).where(ActivityEvent.action == "post_editorial_feedback").order_by(ActivityEvent.id.desc()).limit(1000))).scalars())
    decisions = (await db.execute(select(AppSetting.value).where(AppSetting.key == "editorial_memory_decisions"))).scalar_one_or_none() or {}
    groups = {}
    for event in events:
        item = event.detail or {}
        category = item.get("category")
        if category not in RULES:
            continue
        key = f"{item['language']}:{item['content_type']}:{category}"
        group = groups.setdefault(key, dict(id=key, language=item['language'], content_type=item['content_type'], category=category,
            rule=RULES[category], status=decisions.get(key, "PROPOSED"), examples=[], post_ids=set()))
        group['post_ids'].add(event.entity_id)
        if len(group['examples']) < 3:
            group['examples'].append(dict(post_id=event.entity_id, before=redact(item.get('before')), after=redact(item.get('after')),
                reason=redact(item.get('reason')), from_version=item.get('from_version'), to_version=item.get('to_version')))
    for group in groups.values():
        group['occurrences'] = len(group.pop('post_ids'))
    return list(groups.values())

async def approved_rules(db, language=None, content_type=None):
    kind = getattr(content_type, 'value', content_type)
    return [dict(language=r['language'], content_type=r['content_type'], rule=r['rule']) for r in await memory(db)
        if r['status'] == 'CONFIRMED' and (language is None or r['language'] == language) and (kind is None or r['content_type'] == kind)]
