"""Backfill community replies that the reply collector silently dropped.

Why this exists
---------------
The reply collector used to pass its sync cursor into the post list. Meta
applies that parameter to the *publication time of the post*, not to the
replies, so every reply that arrived more than an hour after its post was
published was never stored. The inbox looked empty while Threads held dozens
of real candidate comments.

This utility reads the conversations again for a chosen period and stores
everything it finds, deduplicated by the remote id. It never publishes,
never replies and never changes settings.

Usage
-----
    # report only, nothing is written
    python -m scripts.backfill_interactions --days 30

    # store what is found
    python -m scripts.backfill_interactions --days 30 --apply

Run it inside the worker container so the application modules are importable:
    docker compose exec worker python -m scripts.backfill_interactions --days 30 --apply
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.database import SessionLocal
from app.models import ActivityEvent, ThreadsAccount, ThreadsInteraction
from app.threads_client import LiveThreadsClient, decrypt_token, get_threads_client
from app.config import settings
from worker.main import sync_community


async def _connected_account(db):
    return (await db.execute(
        select(ThreadsAccount)
        .where(ThreadsAccount.connected.is_(True), ThreadsAccount.is_mock.is_(False))
        .order_by(ThreadsAccount.id.desc())
        .limit(1)
    )).scalar_one_or_none()


async def _dry_run(db, account, days: int) -> tuple[int, int]:
    """Count the replies that are missing without writing anything."""
    known = set((await db.execute(select(ThreadsInteraction.remote_id))).scalars())
    since = datetime.now(timezone.utc) - timedelta(days=days)
    client = get_threads_client(account.profile_id, decrypt_token(account.access_token_encrypted))
    if isinstance(client, LiveThreadsClient):
        posts = await client.own_posts()
        replies = await client.replies(since, posts=posts)
    else:
        replies = await client.replies(since)
    fresh = [item for item in replies if item.get("id") and item["id"] not in known]
    return len(replies), len(fresh)


async def _apply(db, account, days: int) -> dict:
    """Widen the cursor and replay the normal collector."""
    original = account.last_success_at
    account.last_success_at = datetime.now(timezone.utc) - timedelta(days=days)
    await db.commit()
    try:
        await sync_community(db, force_full=True)
        event = (await db.execute(
            select(ActivityEvent)
            .where(ActivityEvent.action == "community_sync_completed")
            .order_by(ActivityEvent.id.desc())
            .limit(1)
        )).scalar_one_or_none()
        return dict(event.detail) if event is not None else {}
    finally:
        # The collector already advanced last_success_at to the present.
        account = await _connected_account(db)
        if account is not None and original is not None and account.last_success_at is not None:
            def _aware(value):
                return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
            account.last_success_at = max(_aware(account.last_success_at), _aware(original))
            await db.commit()


async def main(days: int, apply: bool) -> int:
    async with SessionLocal() as db:
        account = await _connected_account(db)
        if account is None:
            print("No connected live Threads account. Nothing to back up.")
            return 1
        if settings.threads_mock_mode:
            print("Mock mode is active. Nothing to back up.")
            return 1
        if not apply:
            total, fresh = await _dry_run(db, account, days)
            print(f"Replies in the last {days} day(s): {total}")
            print(f"Not stored yet: {fresh}")
            print("Nothing was written. Re-run with --apply to store them.")
            return 0
        summary = await _apply(db, account, days)
        for key in ("replies_fetched", "replies_created", "replies_existing", "adopted_posts", "dropped_by_cursor"):
            if key in summary:
                print(f"{key}: {summary[key]}")
        print("Backfill finished.")
        return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=30, help="how far back to read conversations")
    parser.add_argument("--apply", action="store_true", help="store the replies instead of only reporting")
    args = parser.parse_args()

    async def _run() -> int:
        try:
            return await main(max(1, min(args.days, 365)), args.apply)
        finally:
            # Dispose the pool explicitly: the aiomysql teardown otherwise
            # complains about a closed event loop and masks the exit code.
            from app.database import engine

            await engine.dispose()

    raise SystemExit(asyncio.run(_run()))
