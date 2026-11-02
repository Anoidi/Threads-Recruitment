"""Format experiments: one confirmed fact, several formats, one comparison.

Why this exists
---------------
The account published and counted views, but it never learned which way of
saying things works. Every earlier comparison mixed the condition with the
wording, so a better post could not be separated from a luckier subject.

An experiment holds one confirmed fact constant and changes only the format.
The fact comes from the vacancy source and is never invented, so every variant
says the same true thing in a different shape. The result is measured the way
the business is measured: candidates first, then signals, and reach only as a
tiebreaker.

Two rules keep the comparison honest:

* Membership is verified. A post that does not carry the confirmed fact, or
  that does not look like the format it was assigned to, is excluded from the
  comparison instead of counting as a losing variant.
* Missing data is missing. With one post per variant the verdict stays
  "insufficient data" until more posts exist, and the report always says how
  many posts each variant really has.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any

from sqlalchemy import desc, select

from .content_quality import structured_vacancy_facts
from .editorial_angles import THREADS_LIMIT, angle_parts
from .enums import PostStatus
from .models import (
    PostMetric,
    ThreadExperiment,
    ThreadExperimentAssignment,
    ThreadsPost,
    Vacancy,
)
from .recruitment_intelligence import post_attribution

# The formats an experiment compares. They are structural, not topical: the
# same fact in a statement, in a question and in a list.
FORMATS = ("FACT", "QUESTION", "TIPS")

FORMAT_LABELS = {
    "FACT": "Факт и что уточнить",
    "QUESTION": "Вопрос аудитории",
    "TIPS": "Список: что проверить",
}

# The planner prompt is in English like the rest of the planner; the labels
# are Russian because the operator reads them.
FORMAT_INSTRUCTIONS = {
    "FACT": (
        "FACT: state the given fact plainly in one or two sentences and add what the reader "
        "should confirm before travelling. No question mark anywhere, no numbered list. "
        "Set content_type to FAQ."
    ),
    "QUESTION": (
        "QUESTION: the same fact, and the post ends with exactly one direct question to the "
        "reader about this fact. No numbered list. Set content_type to QUESTION."
    ),
    "TIPS": (
        "TIPS: the same fact as a short numbered list with 2 or 3 items, every item about this "
        "fact. No question mark anywhere. Set content_type to EDUCATIONAL."
    ),
}

# The topic a fact belongs to, and the editorial angle that renders it.
SUBJECT_ANGLE = {
    "PAY": "RATE",
    "HOUSING": "HOUSING",
    "SCHEDULE": "SCHEDULE",
    "CONTRACT": "CONTRACT",
    "CITY": "CITY",
    "TRANSPORT": "TRANSPORT",
    "REQUIREMENTS": "REQUIREMENTS",
}

SUBJECT_LABELS = {
    "PAY": "Ставка",
    "HOUSING": "Жильё",
    "SCHEDULE": "График",
    "CONTRACT": "Договор",
    "CITY": "Город",
    "TRANSPORT": "Транспорт",
    "REQUIREMENTS": "Требования",
}

# The order the subjects are tried in when the experiment picks its own fact.
SUBJECT_ORDER = ("PAY", "SCHEDULE", "HOUSING", "CITY", "TRANSPORT", "CONTRACT", "REQUIREMENTS")

# The question a QUESTION variant adds. It asks about the fact and names no
# other condition, so a variant cannot smuggle in a second subject.
_FORMAT_QUESTION = {
    "uk": "Що ще варто уточнити перед виїздом?",
    "ru": "Что ещё стоит уточнить перед выездом?",
    "pl": "Co jeszcze warto sprawdzić przed wyjazdem?",
    "en": "What else is worth checking before travelling?",
}

# A fact longer than this cannot fit into a post together with the advice.
MAX_FACT_LENGTH = 120

# How many posts every variant may get. The experiment borrows a few days of
# the content plan and then gives it back: an account that posts the same
# confirmed fact three times a day stops being read.
EXPERIMENT_ROUNDS = 2

# Every comparison is made inside an observation window measured from the
# publication, not from "now". A post published an hour ago and a post
# published three days ago have not had the same chance to collect replies,
# and comparing their raw counters reads that difference as a format effect.
WINDOWS = (24, 48, 72)

WINDOW_LABELS = {24: "24 ч", 48: "48 ч", 72: "72 ч"}

# The collector takes snapshots at 1, 24, 48 and 72 hours. A window is closed
# by a snapshot at or shortly after its end; the tolerance only absorbs the
# normal job delay, it never lets a 72-hour snapshot stand in for a 24-hour one.
WINDOW_TOLERANCE_HOURS = {24: 6, 48: 12, 72: 18}

# A format is not "better" on one lucky post.
MIN_POSTS_PER_VARIANT = 2

# A difference counts as a lead only when it is large and repeated. With tiny
# samples anything smaller is reported as "not separated" instead of a winner.
CLEAR_LEAD_RATIO = 2.0
CLEAR_LEAD_MIN_CANDIDATES = 2

_LIST_ITEM_RE = re.compile(r"(?m)^\s*(?:\d+[.)]|[-•*])\s+\S")


def _norm(value: str) -> str:
    return " ".join((value or "").split()).casefold()


def detect_format(text: str) -> str | None:
    """Which format the text really is, or None when it cannot be attributed.

    A post that is both a list and a question cannot be counted for any
    format, so it returns None instead of guessing.
    """
    value = (text or "").strip()
    if not value:
        return None
    items = len(_LIST_ITEM_RE.findall(value))
    question = "?" in value
    if items >= 2 and question:
        return None
    if items >= 2:
        return "TIPS"
    if question:
        return "QUESTION"
    return "FACT"


def fact_present(content: str, fact: str) -> bool:
    """Does the post carry the confirmed fact unchanged?"""
    needle = _norm(fact)
    if not needle:
        return False
    return needle in _norm(content)


def verify_membership(content: str, fmt: str, fact: str) -> tuple[bool, str | None]:
    """Check that the post really belongs to the variant it was assigned to."""
    detected = detect_format(content)
    format_ok = detected == fmt
    fact_ok = fact_present(content, fact)
    if format_ok and fact_ok:
        return True, None
    if not format_ok and not fact_ok:
        return False, "both"
    return False, "format" if not format_ok else "fact"


def supported_fact(vacancy, subject: str) -> str | None:
    """The confirmed value of this subject for this vacancy, or None."""
    facts = structured_vacancy_facts(vacancy)
    value = {
        "PAY": facts.salary or facts.student_salary,
        "HOUSING": facts.housing,
        "SCHEDULE": facts.schedule,
        "CONTRACT": facts.contract_type,
        "CITY": facts.location,
        "TRANSPORT": facts.transport,
        "REQUIREMENTS": facts.requirements,
    }.get(subject)
    if not value:
        return None
    value = " ".join(str(value).split())
    if len(value) > MAX_FACT_LENGTH:
        return None
    return value


def render_format(fmt: str, sentences: list[str], cta: str, language: str = "uk") -> str | None:
    """The deterministic version of one format, built from confirmed sentences.

    The sentences are never reworded, only re-arranged and cut. The fact is
    kept even when the rest has to go, because a post without the fact is not
    the same post any more.
    """
    if fmt not in FORMATS or not sentences:
        return None
    optional = list(sentences[1:])
    required = [sentences[0]]
    if fmt == "QUESTION":
        required.append(_FORMAT_QUESTION.get(language, _FORMAT_QUESTION["uk"]))
    while True:
        items = required + optional
        if fmt == "TIPS":
            # The line breaks are the format. They must survive.
            text = "\n".join(f"{index}. {item}" for index, item in enumerate(items, 1)) + "\n" + cta
            text = "\n".join(" ".join(line.split()) for line in text.splitlines())
        else:
            text = " ".join(" ".join(items + [cta]).split())
        if len(text) <= THREADS_LIMIT:
            return text
        if not optional:
            return None
        optional.pop()


def base_fact_text(vacancy, subject: str, language: str = "uk", phone_contact: str | None = None, variant: int = 0) -> tuple[list[str], str] | None:
    """The sentences and the call to action that carry the confirmed fact.

    ``variant`` follows the round index, so a later round re-shapes the same
    confirmed fact with different wording instead of repeating the exact
    post the audience has already seen in round one.
    """
    angle = SUBJECT_ANGLE.get(subject)
    if not angle:
        return None
    return angle_parts(vacancy, angle, language, phone_contact, variant=variant)


async def _gap_themes(db) -> list[str]:
    """The themes the audience asks about and we have not answered."""
    from .audience_profile import audience_profile

    try:
        profile = await audience_profile(db)
    except Exception:
        return []
    themes = []
    for gap in profile.get("gaps") or []:
        theme = str(gap.get("theme") or "").upper()
        if theme and theme not in themes:
            themes.append(theme)
    return themes


async def pick_pair(db) -> tuple[Vacancy, str, str] | None:
    """Choose the vacancy, the subject and the fact the experiment holds constant.

    A vacancy nobody asks about comes first: the experiment should help the
    job that produces no candidates, not the one that already works.
    """
    from .recruitment_intelligence import vacancy_demand

    vacancies = list((await db.execute(
        select(Vacancy).where(Vacancy.active.is_(True)).order_by(desc(Vacancy.created_at)).limit(12))
    ).scalars())
    if not vacancies:
        return None
    try:
        demand = await vacancy_demand(db)
        deficits = [row["vacancy_id"] for row in demand.get("deficits") or []]
    except Exception:
        deficits = []
    gaps = await _gap_themes(db)
    ordered = sorted(
        vacancies,
        key=lambda item: (deficits.index(item.id) if item.id in deficits else len(deficits), -item.id),
    )
    subjects = [theme for theme in gaps if theme in SUBJECT_ANGLE] + list(SUBJECT_ORDER)
    for vacancy in ordered:
        for subject in subjects:
            fact = supported_fact(vacancy, subject)
            if fact:
                return vacancy, subject, fact
    return None


async def start_experiment(
    db,
    *,
    subject: str | None = None,
    formats: list[str] | None = None,
    name: str | None = None,
    hypothesis: str | None = None,
    created_by: str = "operator",
) -> ThreadExperiment:
    """Open an experiment. Only a draft is created later, nothing is published."""
    chosen: tuple[Vacancy, str, str] | None = None
    if subject:
        vacancies = list((await db.execute(
            select(Vacancy).where(Vacancy.active.is_(True)).order_by(desc(Vacancy.created_at)).limit(12))
        ).scalars())
        for vacancy in vacancies:
            fact = supported_fact(vacancy, subject)
            if fact:
                chosen = (vacancy, subject, fact)
                break
        if chosen is None:
            raise ValueError(
                f"Ни одна активная вакансия не подтверждает факт по теме «{subject}»."
            )
    else:
        chosen = await pick_pair(db)
        if chosen is None:
            raise ValueError("Нет активной вакансии с подтверждённым фактом для эксперимента.")
    vacancy, subject_key, fact = chosen
    variant_formats = [item for item in (formats or list(FORMATS)) if item in FORMATS] or list(FORMATS)
    label = SUBJECT_LABELS.get(subject_key, subject_key)
    experiment = ThreadExperiment(
        name=(name or f"Форматы: {label}")[:160],
        hypothesis=hypothesis or (
            f"Один и тот же подтверждённый факт («{fact}») в разных форматах даст разное число "
            "откликов кандидатов. Побеждает формат с кандидатами, а не с просмотрами."
        ),
        subject=subject_key,
        fact=fact,
        vacancy_id=vacancy.id,
        metric="CANDIDATES",
        status="RUNNING",
        variants=[
            {"key": item, "format": item, "label": FORMAT_LABELS.get(item, item)}
            for item in variant_formats
        ],
        started_at=datetime.now(timezone.utc),
        created_by=str(created_by or "operator")[:100],
    )
    db.add(experiment)
    await db.flush()
    return experiment


async def active_experiment(db) -> ThreadExperiment | None:
    """The experiment that is currently running, if any."""
    return (await db.execute(
        select(ThreadExperiment)
        .where(ThreadExperiment.status == "RUNNING")
        .order_by(desc(ThreadExperiment.id))
    )).scalars().first()


async def record_assignment(
    db,
    experiment: ThreadExperiment,
    post: ThreadsPost,
    variant: str,
    fmt: str,
) -> ThreadExperimentAssignment:
    """Attach a post to a variant and record whether it really belongs there."""
    verified, reason = verify_membership(post.content or "", fmt, experiment.fact)
    assignment = ThreadExperimentAssignment(
        experiment_id=experiment.id,
        variant=variant,
        format=fmt,
        post_id=post.id,
        fact=experiment.fact,
        verified=verified,
        mismatch_reason=reason,
    )
    db.add(assignment)
    await db.flush()
    return assignment


def slot_rotation(experiment_id: int, round_index: int, count: int) -> dict[int, int]:
    """Which slot each variant gets in this round.

    The earliest free slot is the best chance to be seen. Giving it to the
    same format in every round would turn the format comparison into a
    time-of-day comparison. The rotation is deterministic, so a retried job
    does not reshuffle the schedule.
    """
    if count <= 0:
        return {}
    shift = (int(experiment_id) + max(0, int(round_index))) % count
    return {index: (index + shift) % count for index in range(count)}


async def experiment_needs_round(db, experiment: ThreadExperiment) -> bool:
    """Does this experiment still need another round of posts?

    The content plan only lends itself to the experiment while there is
    something to compare. After the last round the account goes back to its
    normal content.

    A new round is planned only when the previous round has actually reached
    the audience: a second round drafted while the first one is still sitting
    in the queue would flood the account with the same fact day after day,
    which was one of the causes of the "posts look alike" complaint.
    """
    counts: dict[str, int] = {}
    published_counts: dict[str, int] = {}
    rows = list((await db.execute(
        select(ThreadExperimentAssignment, ThreadsPost.status)
        .join(ThreadsPost, ThreadsPost.id == ThreadExperimentAssignment.post_id)
        .where(
            ThreadExperimentAssignment.experiment_id == experiment.id,
            # An excluded post never tested its format. Consuming a round for
            # it would end the experiment with no comparable data at all.
            ThreadExperimentAssignment.verified.is_(True),
        )
    )).all())
    for row, status in rows:
        counts[row.variant] = counts.get(row.variant, 0) + 1
        if status == PostStatus.PUBLISHED:
            published_counts[row.variant] = published_counts.get(row.variant, 0) + 1
    for variant in experiment.variants or []:
        key = str(variant.get("key") or variant.get("format") or "")
        total = counts.get(key, 0)
        if total >= EXPERIMENT_ROUNDS:
            continue
        # A variant with nothing yet still needs its first round no matter
        # what is published. A variant with drafted-but-unpublished posts
        # waits for them to go out before another round is borrowed.
        if total == 0 or published_counts.get(key, 0) >= 1:
            return True
    return False


async def _metrics_by_post(db, post_ids: list[int]) -> dict[int, list[PostMetric]]:
    """Every snapshot per post, oldest first. The age is what makes it usable."""
    if not post_ids:
        return {}
    rows = list((await db.execute(
        select(PostMetric)
        .where(PostMetric.post_id.in_(post_ids))
        .order_by(PostMetric.post_id, PostMetric.captured_at, PostMetric.id)
    )).scalars())
    snapshots: dict[int, list[PostMetric]] = {}
    for row in rows:
        snapshots.setdefault(row.post_id, []).append(row)
    return snapshots


def _as_utc(value):
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _window_metric(snapshots: list[PostMetric], published_at, hours: int) -> PostMetric | None:
    """The snapshot that closes this window, or None while the window is open.

    A 24-hour window is closed only by a snapshot taken at 24 hours (plus the
    normal job delay). A snapshot taken after three days measures three days,
    not one, so it never stands in for a window it does not belong to.
    """
    published = _as_utc(published_at)
    if published is None:
        return None
    tolerance = WINDOW_TOLERANCE_HOURS[hours]
    closed = None
    for metric in snapshots:
        captured = _as_utc(metric.captured_at)
        if captured is None:
            continue
        age = (captured - published).total_seconds() / 3600
        if hours <= age <= hours + tolerance:
            closed = metric  # snapshots are sorted, so this keeps the latest in range
    return closed


def _views(metric: PostMetric | None) -> int | None:
    if metric is None or metric.views is None or metric.views < 0:
        return None
    return int(metric.views)


def _rate(value: int, posts: int) -> float | None:
    return round(value / posts, 2) if posts else None


def _median(values: list[int]) -> int | None:
    return int(median(values)) if values else None


def _window_note(hours: int | None, span_hours: float | None) -> str:
    parts = []
    if hours:
        parts.append(f"Окно наблюдения {int(hours)} ч.")
    if span_hours and span_hours > 24:
        parts.append(f"Разброс публикаций {int(span_hours)} ч.")
    return (" " + " ".join(parts)) if parts else ""


async def experiment_report(db, experiment: ThreadExperiment) -> dict:
    """The measured result of one experiment, with the sample sizes.

    Results are compared inside equal observation windows: 24, 48 and 72 hours
    from publication. A post that has not reached a window is not part of that
    window's comparison, so a fresh post can never lose to an old one simply
    because the old one had more time.
    """
    assignments = list((await db.execute(
        select(ThreadExperimentAssignment).where(
            ThreadExperimentAssignment.experiment_id == experiment.id
        ).order_by(ThreadExperimentAssignment.id)
    )).scalars())
    post_ids = [row.post_id for row in assignments if row.post_id]
    posts: dict[int, ThreadsPost] = {}
    if post_ids:
        posts = {
            post.id: post for post in (await db.execute(
                select(ThreadsPost).where(ThreadsPost.id.in_(post_ids))
            )).scalars()
        }
    snapshots = await _metrics_by_post(db, list(posts))
    lifetime = await post_attribution(db, list(posts))
    published_by_post = {
        post.id: _as_utc(post.published_at)
        for post in posts.values()
        if post.status == PostStatus.PUBLISHED and post.published_at is not None
    }
    # One query per window, shared by all variants: the same reply must not be
    # counted with a different rule in two places.
    window_attribution = {
        hours: await post_attribution(
            db, list(published_by_post),
            published_by_post=published_by_post, window_hours=hours,
        )
        for hours in WINDOWS
    }

    variants: list[dict] = []
    excluded: list[dict] = []
    closed_by_variant: dict[str, dict[int, list[int]]] = {}
    candidate_keys_by_variant: dict[str, set[str]] = {}
    for variant in experiment.variants or []:
        key = str(variant.get("key") or variant.get("format") or "")
        rows = [row for row in assignments if row.variant == key]
        verified = [row for row in rows if row.verified and row.post_id in posts]
        published = [row for row in verified if posts[row.post_id].status == PostStatus.PUBLISHED]
        published_ids = [row.post_id for row in published]
        closed: dict[int, list[int]] = {}
        for hours in WINDOWS:
            closed[hours] = [
                row.post_id for row in published
                if _window_metric(snapshots.get(row.post_id, []), posts[row.post_id].published_at, hours) is not None
            ]
        closed_by_variant[key] = closed
        views: list[int] = []
        for row in published:
            post_snapshots = snapshots.get(row.post_id) or []
            value = _views(post_snapshots[-1]) if post_snapshots else None
            if value is not None:
                views.append(value)
        unique_candidates: set[str] = set()
        for row in published:
            unique_candidates.update((lifetime.get(row.post_id) or {}).get("candidate_keys", []))
        candidate_keys_by_variant[key] = set(unique_candidates)

        post_rows = []
        for row in rows:
            post = posts.get(row.post_id) if row.post_id else None
            result = lifetime.get(row.post_id) if row.post_id else None
            post_snapshots = snapshots.get(row.post_id, []) if row.post_id else []
            latest = post_snapshots[-1] if post_snapshots else None
            age_hours = None
            published_at = _as_utc(post.published_at) if post is not None else None
            if published_at is not None:
                age_hours = round((datetime.now(timezone.utc) - published_at).total_seconds() / 3600, 1)
            post_rows.append({
                "post_id": row.post_id,
                "status": str(getattr(post.status, "value", post.status)) if post else None,
                "verified": bool(row.verified),
                "mismatch_reason": row.mismatch_reason,
                "published_at": (
                    post.published_at.isoformat()
                    if post is not None and post.published_at is not None else None
                ),
                "age_hours": age_hours,
                "views": _views(latest),
                "signals": int(result["signals"]) if result else 0,
                "candidates": int(result["candidates"]) if result else 0,
                "closed_windows": [hours for hours in WINDOWS if row.post_id in closed[hours]],
                "content": (post.content[:200] if post else None),
            })
            if not row.verified:
                excluded.append({
                    "post_id": row.post_id,
                    "variant": key,
                    "reason": row.mismatch_reason or "unknown",
                })
        variants.append({
            "key": key,
            "format": str(variant.get("format") or key),
            "label": str(variant.get("label") or FORMAT_LABELS.get(key, key)),
            "assigned": len(rows),
            "verified": len(verified),
            "excluded": len(rows) - len(verified),
            "published": len(published),
            "measured": sum(1 for row in published if snapshots.get(row.post_id)),
            "signals": sum(int((lifetime.get(row.post_id) or {}).get("signals", 0)) for row in published),
            "candidates": sum(int((lifetime.get(row.post_id) or {}).get("candidates", 0)) for row in published),
            "unique_candidates": len(unique_candidates),
            "median_views": _median(views),
            "completed_windows": [
                hours for hours in WINDOWS
                if len(closed[hours]) >= MIN_POSTS_PER_VARIANT
            ],
            "posts": post_rows,
        })

    windows: list[dict] = []
    for hours in WINDOWS:
        rows: list[dict] = []
        for variant in variants:
            key = variant["key"]
            ids = closed_by_variant[key][hours]
            observations = [
                _window_metric(snapshots.get(post_id, []), posts[post_id].published_at, hours)
                for post_id in ids
            ]
            views = [value for value in (_views(metric) for metric in observations) if value is not None]
            people: set[str] = set()
            candidate_keys: set[str] = set()
            signals = 0
            candidates = 0
            for post_id in ids:
                attributed = window_attribution[hours].get(post_id) or {}
                signals += int(attributed.get("signals", 0))
                candidates += int(attributed.get("candidates", 0))
                people.update(attributed.get("people_keys", []))
                candidate_keys.update(attributed.get("candidate_keys", []))
            rows.append({
                "key": key,
                "label": variant["label"],
                "published": variant["published"],
                "posts": len(ids),
                "views_median": _median(views),
                "views_total": sum(views) if views else None,
                "signals": signals,
                "candidates": candidates,
                "unique_people": len(people),
                "candidates_per_post": _rate(candidates, len(ids)),
                "signals_per_post": _rate(signals, len(ids)),
            })
        complete = bool(rows) and all(row["posts"] >= MIN_POSTS_PER_VARIANT for row in rows)
        windows.append({
            "hours": hours,
            "label": WINDOW_LABELS[hours],
            "complete": complete,
            "variants": rows,
        })

    complete_windows = [item["hours"] for item in windows if item["complete"]]
    with_data = [item["hours"] for item in windows if any(row["posts"] for row in item["variants"])]
    if complete_windows:
        primary_window = max(complete_windows)
    elif with_data:
        primary_window = min(with_data)
    else:
        primary_window = None
    primary_entry = next((item for item in windows if item["hours"] == primary_window), None)
    for variant in variants:
        variant["window_posts"] = len(
            closed_by_variant[variant["key"]][primary_window]
        ) if primary_window is not None else 0
        # The headline views figure follows the same window as the verdict.
        # A lifetime median next to a windowed verdict compares two different
        # observation periods and invites the exact mistake this module fixes.
        variant["lifetime_views_median"] = variant["median_views"]
        window_row = next(
            (row for row in (primary_entry or {}).get("variants", []) if row["key"] == variant["key"]),
            None,
        )
        variant["median_views"] = (
            window_row["views_median"] if window_row is not None else None
        ) if primary_window is not None else None

    # The window the variants were published in. A post that appeared two days
    # earlier has had two days more to collect replies, and that difference
    # would be read as a difference between the formats.
    published_times = []
    for row in assignments:
        if not row.verified or not row.post_id:
            continue
        post = posts.get(row.post_id)
        if post is None or post.published_at is None:
            continue
        if post.status != PostStatus.PUBLISHED:
            continue
        published_times.append(_as_utc(post.published_at))
    window: dict[str, Any] = {"published_posts": len(published_times)}
    if published_times:
        first = min(published_times)
        last = max(published_times)
        window["first_published_at"] = first.isoformat()
        window["last_published_at"] = last.isoformat()
        window["span_hours"] = round((last - first).total_seconds() / 3600, 1)
    else:
        window["first_published_at"] = None
        window["last_published_at"] = None
        window["span_hours"] = None

    # One person can reply under several variants. That is a confound, so it is
    # counted and reported instead of silently adding two candidates.
    variants_by_key: dict[str, set[str]] = {}
    for variant in variants:
        for identity in candidate_keys_by_variant.get(variant["key"], set()):
            variants_by_key.setdefault(identity, set()).add(variant["key"])
    shared_candidates = sum(1 for keys in variants_by_key.values() if len(keys) > 1)

    verdict, verdict_text, winner = _verdict(variants, primary_entry, window.get("span_hours"))
    caveats = [
        "Отклики кандидатов не равны подтверждённому найму.",
        "Посты, не прошедшие проверку формата и факта, исключены из сравнения, а не зачтены как проигрыш.",
        "Сравнение идёт только по закрытым окнам наблюдения: пост без замера после 24/48/72 ч в это окно не входит.",
        "Один человек считается один раз на пост, даже если написал несколько комментариев.",
        "Просмотры — дополнительный показатель; победитель по просмотрам не объявляется.",
    ]
    span = window.get("span_hours")
    if span is not None and span > 24:
        caveats.append(
            f"Варианты опубликованы с разбросом {int(span)} ч: у более раннего поста больше времени "
            "на отклики. Равные окна это смягчают, но не отменяют разницу условий."
        )
    if shared_candidates:
        caveats.append(
            f"{shared_candidates} кандидатов появились более чем в одном варианте: "
            "человек мог увидеть несколько постов, поэтому сравнение форматов смещено."
        )
    if primary_window is not None and primary_window < max(WINDOWS):
        caveats.append(
            f"Окна {', '.join(str(item) + ' ч' for item in WINDOWS if item > primary_window)} "
            f"ещё не закрыты у всех вариантов: вывод по {primary_window} ч предварительный."
        )
    smallest_sample = min((row["published"] for row in variants if row["published"]), default=0)
    if 0 < smallest_sample < 5:
        caveats.append(
            f"В самом маленьком варианте {smallest_sample} постов: это направление, "
            "а не статистически значимый результат."
        )
    statistics = {
        "min_posts_per_variant": MIN_POSTS_PER_VARIANT,
        "clear_lead_ratio": CLEAR_LEAD_RATIO,
        "primary_window": primary_window,
        "closed_windows": complete_windows,
        "posts_per_variant": {row["key"]: row["published"] for row in variants},
        "shared_candidates": shared_candidates,
        "sample_note": (
            f"Сравнение по {primary_window} ч." if primary_window
            else "Ни одно окно наблюдения ещё не закрыто."
        ),
    }
    return {
        "experiment_id": experiment.id,
        "name": experiment.name,
        "status": experiment.status,
        "subject": experiment.subject,
        "subject_label": SUBJECT_LABELS.get(experiment.subject, experiment.subject),
        "fact": experiment.fact,
        "vacancy_id": experiment.vacancy_id,
        "metric": experiment.metric,
        "hypothesis": experiment.hypothesis,
        "started_at": experiment.started_at.isoformat() if experiment.started_at else None,
        "concluded_at": experiment.concluded_at.isoformat() if experiment.concluded_at else None,
        "variants": variants,
        "excluded": excluded,
        "window": window,
        "windows": windows,
        "primary_window": primary_window,
        "statistics": statistics,
        "verdict": verdict,
        "verdict_text": verdict_text,
        "winner": winner,
        "caveats": caveats,
    }


def _verdict(
    variants: list[dict],
    window_entry: dict | None = None,
    span_hours: float | None = None,
) -> tuple[str, str, dict | None]:
    """The honest conclusion of one experiment.

    A winner is named only when one variant produced at least two candidates
    and at least twice the candidate rate of the next variant. Everything
    else is reported as a direction or as "not separated".
    """
    if not variants or not any(row["assigned"] for row in variants):
        return "NO_ASSIGNMENTS", "Посты для вариантов ещё не созданы. Сравнивать нечего.", None
    published = [row for row in variants if row["published"]]
    if not published:
        excluded = sum(row["excluded"] for row in variants)
        if excluded:
            return (
                "NO_VERIFIED_POSTS",
                "Ни один пост не прошёл проверку формата и факта, поэтому сравнивать нечего.",
                None,
            )
        return "NO_PUBLISHED", "Ни один проверенный пост ещё не опубликован.", None
    if len(published) < 2 or any(row["published"] < MIN_POSTS_PER_VARIANT for row in published):
        return (
            "INSUFFICIENT_DATA",
            f"Данных недостаточно: в части групп меньше {MIN_POSTS_PER_VARIANT} опубликованных постов. "
            "Один пост не отделяет форматы друг от друга.",
            None,
        )
    rows = [row for row in (window_entry or {}).get("variants", []) if row["published"]]
    hours = (window_entry or {}).get("hours")
    if not rows or not any(row["posts"] for row in rows):
        return (
            "INSUFFICIENT_DATA",
            "Посты опубликованы, но ни одно окно наблюдения ещё не закрыто: нет замера после 24 часов. "
            "Сравнивать разный возраст нельзя.",
            None,
        )
    if not (window_entry or {}).get("complete"):
        return (
            "INSUFFICIENT_DATA",
            f"Окно {hours} ч закрыто не у всех вариантов: у части групп меньше "
            f"{MIN_POSTS_PER_VARIANT} постов с полным наблюдением.",
            None,
        )
    total_signals = sum(row["signals"] for row in rows)
    total_candidates = sum(row["candidates"] for row in rows)
    if not total_signals and not total_candidates:
        return (
            "NO_CANDIDATE_SIGNAL",
            f"В окне {hours} ч откликов кандидатов не было ни в одной группе. Сравнение по кандидатам "
            "невозможно; в отчёте только охват, и он не является целью."
            + _window_note(hours, span_hours),
            None,
        )
    if not total_candidates:
        winner = max(
            rows,
            key=lambda row: (row["signals_per_post"] or 0, row["views_median"] or 0),
        )
        return (
            "SIGNAL_ONLY",
            f"Подтверждённых кандидатов пока нет ни в одной группе. Лучше других по сигналам "
            f"формат «{winner['label']}» ({winner['signals']} сигналов на {winner['posts']} постах). "
            "Это ориентир, а не вывод."
            + _window_note(hours, span_hours),
            winner,
        )
    ranked = sorted(
        rows,
        key=lambda row: (row["candidates_per_post"] or 0, row["signals_per_post"] or 0, row["views_median"] or 0),
        reverse=True,
    )
    leader, runner = ranked[0], ranked[1]
    leader_rate = leader["candidates_per_post"] or 0
    runner_rate = runner["candidates_per_post"] or 0
    clear = leader["candidates"] >= CLEAR_LEAD_MIN_CANDIDATES and (
        runner_rate == 0 or leader_rate >= CLEAR_LEAD_RATIO * runner_rate
    )
    if not clear:
        return (
            "DIFFERENCE_NOT_CLEAR",
            f"Кандидаты есть у нескольких форматов, но разница в пределах малой выборки: "
            f"«{leader['label']}» — {leader['candidates']} на {leader['posts']} постах, "
            f"«{runner['label']}» — {runner['candidates']} на {runner['posts']}. "
            "Победитель не объявляется."
            + _window_note(hours, span_hours),
            None,
        )
    return (
        "RESULT",
        f"В окне {hours} ч больше подтверждённых кандидатов принёс формат «{leader['label']}»: "
        f"{leader['candidates']} кандидатов и {leader['signals']} сигналов на {leader['posts']} постах "
        f"({leader_rate} на пост против {runner_rate} у следующего). "
        "Разница кратная, но выборка мала: это ориентир, а не доказанная закономерность."
        + _window_note(hours, span_hours),
        leader,
    )


async def conclude_experiment(db, experiment: ThreadExperiment) -> dict:
    """Freeze the result of an experiment."""
    # The status is set first, so the frozen report says what it is.
    experiment.status = "CONCLUDED"
    experiment.concluded_at = datetime.now(timezone.utc)
    report = await experiment_report(db, experiment)
    experiment.report = report
    await db.flush()
    return report


async def current_report(db) -> dict | None:
    """The report of the running experiment, or of the last concluded one."""
    experiment = await active_experiment(db)
    if experiment is None:
        experiment = (await db.execute(
            select(ThreadExperiment).order_by(desc(ThreadExperiment.id)).limit(1)
        )).scalars().first()
    if experiment is None:
        return None
    if experiment.status == "CONCLUDED" and experiment.report:
        return experiment.report
    return await experiment_report(db, experiment)


def experiment_brief(report: dict | None, limit: int = 4) -> str:
    """Compact form of the last experiment for the planner prompt.

    The brief carries the window and the sample size with every number. A
    planner that reads "3 candidates" without "out of 2 posts in a 24-hour
    window" will treat a hint as a rule.
    """
    if not report:
        return ""
    rows = report.get("variants") or []
    if not rows:
        return ""
    window = report.get("primary_window")
    lines = []
    for row in rows[:limit]:
        lines.append(
            f"- {row.get('label')}: опубликовано {row.get('published', 0)}, "
            f"в окне {row.get('window_posts', 0)}, кандидатов {row.get('candidates', 0)}, "
            f"сигналов {row.get('signals', 0)}, медиана просмотров {row.get('median_views')}"
        )
    verdict = str(report.get("verdict_text") or "")
    window_note = (
        f"Окно наблюдения: {int(window)} ч, одинаковое для всех вариантов."
        if window else "Ни одно окно наблюдения ещё не закрыто."
    )
    return (
        "Последний эксперимент форматов (одна и та же подтверждённая фактура, разные форматы). "
        f"{window_note} Это ориентир, а не доказанное правило:\n"
        + "\n".join(lines) + f"\n{verdict}"
    )
