"""Separate token encryption key + rotation (audit D9 / phase 1.2).

Covers: legacy stand-in tokens stay readable after the dedicated key is
enabled, new tokens are written with the key, outside development the write
path fails closed, OAuth state uses a distinct derived key, and the rotation
script re-encrypts rows on a test database.
"""

import base64
import hashlib

import pytest
from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from sqlalchemy import select

from app.config import settings
from app.enums import SocialPlatform
from app.models import SocialAccount, ThreadsAccount
from app.social_clients import (
    decrypt_social_token,
    encrypt_social_token,
    make_oauth_state,
    social_token_uses_primary_key,
    validate_oauth_state,
)
from app.threads_client import (
    ThreadsAPIError,
    decrypt_token,
    encrypt_token,
    make_state,
    token_uses_primary_key,
    validate_state,
)


def _legacy_fernet(secret: str) -> Fernet:
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest()))


@pytest.mark.asyncio
async def test_legacy_token_reads_after_dedicated_key_is_set(monkeypatch):
    monkeypatch.setattr(settings, "litellm_master_key", "legacy-master")
    monkeypatch.setattr(settings, "threads_token_key", "")
    stored = encrypt_token("live-threads-token")

    monkeypatch.setattr(settings, "threads_token_key", "new-dedicated-key")
    assert decrypt_token(stored) == "live-threads-token"
    assert token_uses_primary_key(stored) is False


@pytest.mark.asyncio
async def test_new_tokens_are_written_with_dedicated_key(monkeypatch):
    monkeypatch.setattr(settings, "litellm_master_key", "legacy-master")
    monkeypatch.setattr(settings, "threads_token_key", "new-dedicated-key")
    stored = encrypt_token("fresh-token")

    assert token_uses_primary_key(stored) is True
    with pytest.raises(InvalidToken):
        # The old stand-in alone must NOT decrypt what was written after the
        # dedicated key was enabled.
        _legacy_fernet("legacy-master").decrypt(stored.encode())


@pytest.mark.asyncio
async def test_write_without_dedicated_key_fails_closed_outside_development(monkeypatch):
    monkeypatch.setattr(settings, "threads_token_key", "")
    monkeypatch.setattr(settings, "litellm_master_key", "legacy-master")
    monkeypatch.setattr(settings, "app_env", "production")
    with pytest.raises(ThreadsAPIError) as error:
        encrypt_token("token")
    assert error.value.category == "config"
    assert "THREADS_TOKEN_KEY" in str(error.value)


@pytest.mark.asyncio
async def test_wrong_key_reports_clear_error(monkeypatch):
    monkeypatch.setattr(settings, "litellm_master_key", "unused-legacy")
    monkeypatch.setattr(settings, "threads_token_key", "writer-key")
    stored = encrypt_token("secret-token")

    monkeypatch.setattr(settings, "litellm_master_key", "")
    monkeypatch.setattr(settings, "threads_token_key", "a-different-key")
    with pytest.raises(ThreadsAPIError) as error:
        decrypt_token(stored)
    assert "cannot be decrypted" in str(error.value)


@pytest.mark.asyncio
async def test_oauth_state_uses_separate_key_from_tokens(monkeypatch):
    monkeypatch.setattr(settings, "litellm_master_key", "")
    monkeypatch.setattr(settings, "threads_token_key", "key-for-both-roles")
    state = make_state()
    assert validate_state(state) is True
    # The state blob must not open with the token read keys (one secret may
    # not serve both roles).
    with pytest.raises(ThreadsAPIError):
        decrypt_token(state)


@pytest.mark.asyncio
async def test_social_tokens_follow_the_same_key_layout(monkeypatch):
    monkeypatch.setattr(settings, "litellm_master_key", "legacy-master")
    monkeypatch.setattr(settings, "threads_token_key", "")
    monkeypatch.setattr(settings, "social_token_key", "")
    legacy = encrypt_social_token("social-token")

    monkeypatch.setattr(settings, "threads_token_key", "dedicated")
    assert decrypt_social_token(legacy) == "social-token"
    assert social_token_uses_primary_key(legacy) is False
    fresh = encrypt_social_token("social-token")
    assert social_token_uses_primary_key(fresh) is True

    state = make_oauth_state(SocialPlatform.INSTAGRAM)
    assert validate_oauth_state(state, SocialPlatform.INSTAGRAM) is True
    # A tampered signature must fail (signature is checked, not just format).
    assert validate_oauth_state(state[:-3] + ("aaa" if not state.endswith("aaa") else "bbb"), SocialPlatform.INSTAGRAM) is False


@pytest.mark.asyncio
async def test_rotation_script_dry_run_and_apply(db, monkeypatch):
    import scripts.rotate_token_key as rotate

    monkeypatch.setattr(settings, "litellm_master_key", "legacy-master")
    monkeypatch.setattr(settings, "threads_token_key", "")
    monkeypatch.setattr(settings, "social_token_key", "")

    account = ThreadsAccount(
        profile_id="rotate-profile", username="recruiter", connected=True, is_mock=False,
        access_token_encrypted=encrypt_token("live-threads-token"),
    )
    social = SocialAccount(
        platform=SocialPlatform.INSTAGRAM, external_id="ig-ex-1", username="recruiter.social",
        access_token_encrypted=encrypt_social_token("ig-access"),
        refresh_token_encrypted=encrypt_social_token("ig-refresh"),
    )
    db.add_all([account, social])
    await db.commit()
    await db.refresh(account)
    await db.refresh(social)

    monkeypatch.setattr(settings, "threads_token_key", "new-dedicated-key")

    class SameSession:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *_):
            return False

    monkeypatch.setattr(rotate, "SessionLocal", lambda: SameSession())

    plan = await rotate.rotate_tokens(apply=False)
    assert plan["dry_run"] is True
    assert plan["checked"] == 3
    assert plan["needs_rotation"] == 3
    assert {row["status"] for row in plan["rows"]} == {"would-rotate"}
    # Dry run wrote nothing: legacy store-close still answers.
    stored_access = (await db.execute(select(ThreadsAccount).where(
        ThreadsAccount.id == account.id))).scalar_one().access_token_encrypted
    assert stored_access == account.access_token_encrypted

    applied = await rotate.rotate_tokens(apply=True)
    assert applied["rotated"] == 3
    await db.refresh(account)
    await db.refresh(social)
    assert token_uses_primary_key(account.access_token_encrypted) is True
    assert social_token_uses_primary_key(social.access_token_encrypted) is True
    assert social_token_uses_primary_key(social.refresh_token_encrypted) is True
    assert decrypt_token(account.access_token_encrypted) == "live-threads-token"
    assert decrypt_social_token(social.access_token_encrypted) == "ig-access"
