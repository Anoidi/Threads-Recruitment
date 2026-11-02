"""Official OAuth/profile clients for Instagram Business Login and TikTok.

This module deliberately contains no publishing scheduler logic. It only
performs provider authentication/profile probes and returns sanitized data to
the API layer. Access tokens are encrypted before persistence and are never
included in returned dictionaries or exception messages.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from .config import settings
from .enums import SocialPlatform
from .logging import get_logger

log = get_logger("social")


SOCIAL_OAUTH_SCOPES: dict[SocialPlatform, tuple[str, ...]] = {
    SocialPlatform.INSTAGRAM: ("instagram_business_basic", "instagram_business_content_publish"),
    SocialPlatform.TIKTOK: ("user.info.basic", "video.upload", "video.publish"),
}


class SocialAPIError(RuntimeError):
    def __init__(self, message: str, *, category: str = "api", status_code: int | None = None, code: str | None = None, retryable: bool = False):
        super().__init__(message)
        self.category = category
        self.status_code = status_code
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class SocialToken:
    access_token: str
    refresh_token: str | None
    external_id: str
    username: str | None
    permissions: list[str]
    expires_at: datetime | None


# Key layout mirrors threads_client (audit D9 / phase 1.2): SOCIAL_TOKEN_KEY
# is preferred for writing, THREADS_TOKEN_KEY is an acceptable second choice,
# and the LITELLM_MASTER_KEY stand-in only ever decrypts stored tokens.
_social_key_warning_issued = False


def _derive_fernet(secret: str) -> Fernet:
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest()))


def _social_secrets() -> tuple[str, list[str]]:
    """Return (write-secret, read-chain) — newest first, legacy last.

    ``SOCIAL_TOKEN_KEY`` wins for writing, ``THREADS_TOKEN_KEY`` is an
    acceptable explicit alternative, the ``LITELLM_MASTER_KEY`` stand-in only
    ever joins the read chain so old tokens stay decryptable.
    """
    global _social_key_warning_issued
    social = (settings.social_token_key or "").strip()
    threads = (settings.threads_token_key or "").strip()
    legacy = (settings.litellm_master_key or "").strip()
    chain: list[str] = list(dict.fromkeys(secret for secret in (social, threads) if secret))
    if not chain:
        if not legacy:
            raise SocialAPIError("Set SOCIAL_TOKEN_KEY, THREADS_TOKEN_KEY or LITELLM_MASTER_KEY before storing social tokens", category="config")
        if not _social_key_warning_issued:
            log.warning("social_token_key_missing",
                        env=settings.app_env,
                        hint="Storage falls back to the LITELLM_MASTER_KEY stand-in; set SOCIAL_TOKEN_KEY or THREADS_TOKEN_KEY")
            _social_key_warning_issued = True
        chain = [legacy]
    elif legacy and legacy not in chain:
        chain.append(legacy)
    return chain[0], chain


def _read_token_fernet() -> MultiFernet:
    _, read_chain = _social_secrets()
    return MultiFernet([_derive_fernet(secret) for secret in dict.fromkeys(read_chain)])


def _write_token_fernet() -> Fernet:
    write, _ = _social_secrets()
    return _derive_fernet(write)


def _state_secret() -> str:
    _, read_chain = _social_secrets()
    # Distinct signing context: the state secret must never be the byte value
    # used for token encryption.
    return hashlib.sha256(b"social-oauth-state-v1:" + read_chain[0].encode()).hexdigest()


def encrypt_social_token(token: str) -> str:
    return _write_token_fernet().encrypt(token.encode()).decode()


def social_token_uses_primary_key(payload: str) -> bool:
    """True when the payload decrypts with the top (write) key alone."""
    write, _ = _social_secrets()
    try:
        _derive_fernet(write).decrypt(payload.encode())
        return True
    except InvalidToken:
        return False


def decrypt_social_token(payload: str) -> str:
    try:
        return _read_token_fernet().decrypt(payload.encode()).decode()
    except InvalidToken as exc:
        raise SocialAPIError("Stored social token cannot be decrypted", category="auth") from exc


def _oauth_secret(platform: SocialPlatform) -> str:
    return settings.instagram_app_secret if platform == SocialPlatform.INSTAGRAM else settings.tiktok_client_secret


def _client_id(platform: SocialPlatform) -> str:
    return settings.instagram_app_id if platform == SocialPlatform.INSTAGRAM else settings.tiktok_client_key


def _redirect_uri(platform: SocialPlatform) -> str:
    return settings.instagram_redirect_uri if platform == SocialPlatform.INSTAGRAM else settings.tiktok_redirect_uri


def is_configured(platform: SocialPlatform) -> bool:
    return bool(_client_id(platform) and _oauth_secret(platform) and _redirect_uri(platform))


def make_oauth_state(platform: SocialPlatform) -> str:
    """Create a short-lived signed state.

    The API layer stores the nonce as single-use server-side. The payload is
    still signed so a callback cannot switch platforms or alter its expiry.
    """

    nonce = secrets.token_urlsafe(24)
    issued = str(int(time.time()))
    payload = f"{platform.value}.{issued}.{nonce}"
    secret = _state_secret()
    signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{payload}.{signature}"


def validate_oauth_state(state: str | None, platform: SocialPlatform, *, max_age_seconds: int = 600) -> bool:
    if not state:
        return False
    parts = state.split(".")
    if len(parts) != 4 or parts[0] != platform.value:
        return False
    try:
        issued = int(parts[1])
    except ValueError:
        return False
    if issued < int(time.time()) - max_age_seconds or issued > int(time.time()) + 30:
        return False
    if len(parts[2]) < 16 or len(parts[3]) != 32:
        return False
    payload = ".".join(parts[:3])
    expected = hmac.new(_state_secret().encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]
    return hmac.compare_digest(parts[3], expected)


def authorize_url(platform: SocialPlatform, state: str) -> str:
    if not is_configured(platform):
        raise SocialAPIError(f"{platform.value} OAuth is not configured", category="config")
    if platform == SocialPlatform.INSTAGRAM:
        params = {
            "client_id": _client_id(platform),
            "redirect_uri": _redirect_uri(platform),
            "response_type": "code",
            "scope": ",".join(SOCIAL_OAUTH_SCOPES[platform]),
            "state": state,
        }
        return f"https://www.instagram.com/oauth/authorize?{urlencode(params)}"
    params = {
        "client_key": _client_id(platform),
        "redirect_uri": _redirect_uri(platform),
        "response_type": "code",
        "scope": ",".join(SOCIAL_OAUTH_SCOPES[platform]),
        "state": state,
    }
    return f"https://www.tiktok.com/v2/auth/authorize/?{urlencode(params)}"


def _versioned_url(base: str, version: str, path: str) -> str:
    return f"{base.rstrip('/')}/{version.strip('/')}/{path.lstrip('/')}"


async def _request_json(method: str, url: str, *, params: dict[str, Any] | None = None, data: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
            response = await client.request(method, url, params=params, data=data, headers=headers)
    except httpx.HTTPError as exc:
        raise SocialAPIError("Social provider is unavailable", category="unavailable", retryable=True) from exc
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if response.is_error:
        error = payload.get("error") if isinstance(payload, dict) else None
        message = error.get("message") if isinstance(error, dict) else None
        code = error.get("code") if isinstance(error, dict) else None
        raise SocialAPIError(str(message or "Social provider rejected the request"), category="auth" if response.status_code in {401, 403} else "api", status_code=response.status_code, code=str(code) if code else None, retryable=response.status_code in {408, 429} or response.status_code >= 500)
    if not isinstance(payload, dict):
        raise SocialAPIError("Social provider returned an invalid response", category="api")
    return payload


async def exchange_code(platform: SocialPlatform, code: str) -> SocialToken:
    if not is_configured(platform):
        raise SocialAPIError(f"{platform.value} OAuth is not configured", category="config")
    if platform == SocialPlatform.INSTAGRAM:
        short = await _request_json("POST", "https://api.instagram.com/oauth/access_token", data={
            "client_id": _client_id(platform), "client_secret": _oauth_secret(platform),
            "grant_type": "authorization_code", "redirect_uri": _redirect_uri(platform), "code": code,
        })
        access_token = str(short.get("access_token") or "")
        if not access_token:
            raise SocialAPIError("Instagram did not return an access token", category="auth")
        long_lived = await _request_json("GET", _versioned_url(settings.instagram_api_base_url, settings.instagram_api_version, "access_token"), params={
            "grant_type": "ig_exchange_token", "client_secret": _oauth_secret(platform), "access_token": access_token,
        })
        access_token = str(long_lived.get("access_token") or access_token)
        expires = _expiry(long_lived.get("expires_in"))
        profile = await instagram_profile(access_token)
        return SocialToken(access_token, None, str(profile["id"]), str(profile.get("username") or ""), list(SOCIAL_OAUTH_SCOPES[platform]), expires)

    token = await _request_json("POST", f"{settings.tiktok_api_base_url.rstrip('/')}/v2/oauth/token/", data={
        "client_key": _client_id(platform), "client_secret": _oauth_secret(platform),
        "grant_type": "authorization_code", "redirect_uri": _redirect_uri(platform), "code": code,
    })
    access_token = str(token.get("access_token") or "")
    if not access_token:
        raise SocialAPIError("TikTok did not return an access token", category="auth")
    profile = await tiktok_profile(access_token)
    return SocialToken(access_token, str(token.get("refresh_token") or "") or None, str(profile["open_id"]), str(profile.get("username") or profile.get("display_name") or ""), list(SOCIAL_OAUTH_SCOPES[platform]), _expiry(token.get("expires_in")))


def _expiry(value: Any) -> datetime | None:
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return None
    return datetime.now(timezone.utc) + timedelta(seconds=seconds) if seconds > 0 else None


async def instagram_profile(access_token: str) -> dict[str, Any]:
    return await _request_json("GET", _versioned_url(settings.instagram_api_base_url, settings.instagram_api_version, "me"), params={"fields": "id,username,user_id", "access_token": access_token})


async def tiktok_profile(access_token: str) -> dict[str, Any]:
    payload = await _request_json("GET", f"{settings.tiktok_api_base_url.rstrip('/')}/v2/user/info/", params={"fields": "open_id,display_name,username,avatar_url"}, headers={"Authorization": f"Bearer {access_token}"})
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
    if error.get("code") not in {None, "ok"} or not data.get("user"):
        raise SocialAPIError(str(error.get("message") or "TikTok profile lookup failed"), category="auth", code=str(error.get("code") or "profile"))
    return data["user"]
