"""Is this vacancy still true?

"Fourteen days without a check" is a signal to look, not a proof that the
vacancy is closed. The check therefore has two parts:

* how long ago the source was confirmed, and
* whether the source itself still says the same thing.

The second part matters more than the first. A channel post that was edited
after publication is a hard signal: the conditions changed, and every post
that quoted the old numbers is now wrong.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

FRESH = "FRESH"
STALE = "STALE"
DRIFTED = "DRIFTED"
UNVERIFIED = "UNVERIFIED"

# How long a confirmed source stays trusted. After that the vacancy needs a
# human check, not an automatic removal.
FRESHNESS_MAX_AGE_DAYS = 14


@dataclass
class Freshness:
    status: str
    age_days: int | None
    reason: str

    @property
    def trusted(self) -> bool:
        """True when the source was confirmed and did not drift."""
        return self.status == FRESH

    @property
    def needs_check(self) -> bool:
        return self.status in {STALE, DRIFTED, UNVERIFIED}


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def vacancy_freshness(vacancy, *, now: datetime | None = None) -> Freshness:
    """Read the freshness of one vacancy from its stored source state."""
    now = now or datetime.now(timezone.utc)
    drifted_at = _aware(getattr(vacancy, "source_drifted_at", None))
    if drifted_at is not None:
        note = getattr(vacancy, "source_drift_note", None) or "the source changed after publication"
        return Freshness(DRIFTED, 0, f"The source no longer matches the published conditions: {note}"[:300])
    confirmed_at = _aware(getattr(vacancy, "source_confirmed_at", None))
    if confirmed_at is None:
        return Freshness(
            UNVERIFIED, None,
            "The source of this vacancy has not been confirmed yet, so the conditions cannot be checked.",
        )
    age_days = max(0, (now - confirmed_at).days)
    if age_days > FRESHNESS_MAX_AGE_DAYS:
        return Freshness(
            STALE, age_days,
            f"The source was last confirmed {age_days} days ago; the limit is {FRESHNESS_MAX_AGE_DAYS}.",
        )
    return Freshness(FRESH, age_days, f"The source was confirmed {age_days} days ago.")


def vacancy_needs_check(vacancy, *, now: datetime | None = None) -> bool:
    return vacancy_freshness(vacancy, now=now).needs_check


def record_confirmation(vacancy, *, now: datetime | None = None) -> None:
    """Mark the source as confirmed by a human check."""
    vacancy.source_confirmed_at = now or datetime.now(timezone.utc)
    vacancy.source_drifted_at = None
    vacancy.source_drift_note = None


def record_drift(vacancy, note: str, *, now: datetime | None = None) -> None:
    """Mark that the source no longer supports the published conditions."""
    vacancy.source_drifted_at = now or datetime.now(timezone.utc)
    vacancy.source_drift_note = (note or "")[:500]
