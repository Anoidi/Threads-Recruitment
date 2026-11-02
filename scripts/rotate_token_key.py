"""Re-encrypt stored platform tokens with the dedicated THREADS_TOKEN_KEY.

The audit (D9/1.2) split token reading and writing: after you set
``THREADS_TOKEN_KEY`` in ``.env`` (required for writes outside development),
tokens stored under the old LITELLM_MASTER_KEY stand-in still decrypt fine,
but every row should be re-encrypted once with the new key so the legacy
secret can be retired.

Usage (inside the backend container so dependencies and env exist):

    # Default: dry run, nothing is written
    python scripts/rotate_token_key.py

    # Write the re-encrypted values back
    python scripts/rotate_token_key.py --apply --i-have-a-backup

Run a DB backup first (scripts/backup-db.ps1). Without ``--apply`` the script
only prints what it would change. Rows that fail to decrypt are reported and
left untouched.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import SocialAccount, ThreadsAccount  # noqa: E402
from app.social_clients import decrypt_social_token, encrypt_social_token, social_token_uses_primary_key  # noqa: E402
from app.threads_client import decrypt_token, encrypt_token, token_uses_primary_key  # noqa: E402
from sqlalchemy import select  # noqa: E402

ENCRYPTED_COLUMNS = {
    ThreadsAccount: ("access_token_encrypted",),
    SocialAccount: ("access_token_encrypted", "refresh_token_encrypted"),
}


def _explicit_key() -> str:
    key = (settings.threads_token_key or "").strip()
    if not key:
        raise SystemExit(
            "THREADS_TOKEN_KEY is not set. Add it to .env first (generate with: "
            "python -c \"import secrets; print(secrets.token_urlsafe(32))\"), "
            "then recreate the backend container."
        )
    return key


async def rotate_tokens(*, apply: bool = False) -> dict:
    _explicit_key()
    plan: dict = {
        "dry_run": not apply,
        "checked": 0,
        "needs_rotation": 0,
        "rotated": 0,
        "rows": [],
    }
    async with SessionLocal() as db:
        for model in (ThreadsAccount, SocialAccount):
            table = model.__tablename__
            rows = list((await db.execute(select(model))).scalars())
            for row in rows:
                for column in ENCRYPTED_COLUMNS[model]:
                    payload = getattr(row, column)
                    if not payload:
                        continue
                    key = f"{table}:{row.id}:{column}"
                    plan["checked"] += 1
                    try:
                        already_current = (
                            social_token_uses_primary_key(payload)
                            if model is SocialAccount
                            else token_uses_primary_key(payload)
                        )
                        token = decrypt_social_token(payload) if model is SocialAccount else decrypt_token(payload)
                    except Exception as exc:  # noqa: BLE001 - report every row
                        plan["rows"].append({"row": key, "status": "unreadable", "error": str(exc)[:200]})
                        continue
                    current = encrypt_social_token(token) if model is SocialAccount else encrypt_token(token)
                    if already_current:
                        plan["rows"].append({"row": key, "status": "already-current"})
                        continue
                    plan["needs_rotation"] += 1
                    if apply:
                        setattr(row, column, current)
                        plan["rotated"] += 1
                        plan["rows"].append({"row": key, "status": "rotated"})
                    else:
                        plan["rows"].append({"row": key, "status": "would-rotate"})
        if apply:
            await db.commit()
    return plan


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write re-encrypted tokens to the database")
    parser.add_argument("--i-have-a-backup", action="store_true", help="confirm that scripts/backup-db.ps1 was run")
    args = parser.parse_args()
    if args.apply and not args.i_have_a_backup:
        raise SystemExit("--apply requires --i-have-a-backup: run scripts/backup-db.ps1 first.")
    result = asyncio.run(rotate_tokens(apply=args.apply))
    for row in result["rows"]:
        print(row)
    print({key: value for key, value in result.items() if key != "rows"})


if __name__ == "__main__":
    main()
