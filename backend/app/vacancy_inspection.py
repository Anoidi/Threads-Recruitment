"""Source-grounded vacancy passport, repair preview and duplicate evidence."""
import hashlib
import re
from datetime import datetime, timezone
from .content_quality import structured_vacancy_facts, extract_vacancy_fields, _RATE_RE, _STUDENT_RE

FIELDS = ('location', 'salary', 'student_salary', 'schedule', 'contract_type', 'housing', 'transport', 'requirements', 'contact')

def rates(v):
    facts = structured_vacancy_facts(v)
    result = []
    for student, value in ((False, facts.salary), (True, facts.student_salary)):
        for clause in (value or '').split(';'):
            for match in _RATE_RE.finditer(clause):
                text = match.group()
                amounts = re.findall(r'\d+(?:[,.]\d+)?', text)
                result.append(dict(amount=amounts[0].replace(',', '.'), amount_max=amounts[1].replace(',', '.') if len(amounts)>1 else None,
                    currency='PLN' if re.search(r'zł|zl|зл|pln', text, re.I) else 'EUR' if re.search(r'€|eur', text, re.I) else 'UAH',
                    basis='netto' if re.search(r'netto|нетто', clause, re.I) else 'brutto' if re.search(r'brutto|брутто', clause, re.I) else None,
                    unit='hour', student=student, udt='WITHOUT' if re.search(r'без\s+udt', clause, re.I) else 'WITH' if re.search(r'udt|wjo', clause, re.I) else 'UNSPECIFIED',
                    condition=clause.strip(), evidence=match.group(), status='CONFIRMED'))
    return result

def inspection(v, peers=(), verified_at=None):
    facts = structured_vacancy_facts(v)
    extracted = extract_vacancy_fields(v.description or '')
    fields = {}
    for key in FIELDS:
        value = getattr(facts, key)
        fields[key] = dict(value=value, status='CONFIRMED' if value else 'MISSING', evidence=extracted.get(key),
            source='stored_or_source' if value else None)
    issues = []
    if not facts.location: issues.append('Нет подтверждённого города')
    if not facts.salary: issues.append('Нет обычной ставки; студенческая ставка не заменяет обычную')
    if not facts.schedule: issues.append('Не указан график')
    if not facts.contract_type: issues.append('Не указан договор')
    duplicates = []
    normalized = lambda text: ' '.join((text or '').casefold().split())
    for peer in peers:
        if peer.id == v.id: continue
        if normalized(v.description) and normalized(v.description) == normalized(peer.description):
            duplicates.append(dict(id=peer.id, reason='Идентичное исходное описание', active=peer.active))
    stamp = verified_at or v.created_at
    age = None
    if stamp:
        if stamp.tzinfo is None: stamp = stamp.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc)-stamp).days
    return dict(vacancy_id=v.id, fields=fields, rates=rates(v), issues=issues,
        status='NEEDS_REVIEW' if issues else 'READY', duplicates=duplicates,
        stale=age is not None and age >=14, source_age_days=age,
        source_hash=hashlib.sha256((v.description or '').encode()).hexdigest(), source_url=v.source_url)

def repair_preview(v):
    extracted = extract_vacancy_fields(v.description or '')
    return [dict(field=key, before=getattr(v, key), after=extracted[key]) for key in FIELDS if extracted.get(key) and extracted[key] != getattr(v, key)]
