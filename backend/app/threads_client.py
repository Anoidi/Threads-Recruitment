from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import hmac
import time
from datetime import datetime, timezone
from typing import Any, Protocol
from urllib.parse import parse_qs, urlparse
from urllib.parse import urlencode
from uuid import uuid4

import httpx
from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from .config import settings
from .logging import get_logger

log = get_logger("threads")


def normalize_remote_username(value: Any) -> str:
    return str(value or "").strip().lstrip("@").casefold()

# Official Threads API scopes. threads_basic is mandatory; threads_manage_mentions
# is required by the mentions endpoint (Meta documentation).
OAUTH_SCOPES = [
    "threads_basic",
    "threads_content_publish",
    "threads_read_replies",
    "threads_manage_replies",
    "threads_manage_insights",
    "threads_manage_mentions",
    "threads_keyword_search",
]

# Graph error codes that mean "slow down" rather than "broken".
_RATE_LIMIT_CODES = {4, 17, 32, 613, 80000, 80004}


class ThreadsAPIError(RuntimeError):
    """Normalized Threads API failure.

    category: config | auth | app_blocked | account_not_ready | rate_limit |
    unavailable | unknown_outcome | api
    retryable marks failures that are safe to retry automatically — the worker
    only ever retries attempts where no media container was created yet.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retryable: bool = False,
        category: str = "api",
        graph_code: int | None = None,
        graph_subcode: int | None = None,
        graph_type: str | None = None,
        fbtrace_id: str | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable
        self.category = category
        self.graph_code = graph_code
        self.graph_subcode = graph_subcode
        self.graph_type = graph_type
        self.fbtrace_id = fbtrace_id
        # An application-level block must wait for operator action in Meta.
        # It is deliberately distinct from a retryable transport failure.
        self.deferred = category in {"app_blocked", "account_not_ready"}
        # True when a publish call may have reached Threads but the outcome is
        # unknown — the worker must then stop instead of risking a duplicate.
        self.duplicate_risk = False


class ThreadsAdapter(Protocol):
    async def profile(self) -> dict[str, Any]: ...
    async def publish_text(self, text: str, idempotency_key: str, reply_to_id: str | None = None) -> dict[str, Any]: ...
    async def replies(self, since: datetime | None = None) -> list[dict[str, Any]]: ...
    async def mentions(self, since: datetime | None = None) -> list[dict[str, Any]]: ...
    async def metrics(self, remote_post_id: str) -> dict[str, int | None]: ...
    async def keyword_search(self, query: str, limit: int = 10) -> list[dict[str, Any]]: ...
    async def own_posts(self) -> list[dict[str, Any]]: ...


# ---------------------------------------------------------------------------
# Token protection (secret never leaves the backend; no token is ever logged)
#
# Key layout (audit D9 / phase 1.2):
#   * ``THREADS_TOKEN_KEY`` is the only key allowed to *encrypt* new tokens.
#   * When it is set, the previously used stand-in (SHA-256 of
#     ``LITELLM_MASTER_KEY``) stays in the read chain only, so tokens stored
#     before the rotation remain readable.
#   * With no dedicated key configured, a running development deployment
#     keeps using the legacy stand-in (with a warning), while any other
#     environment refuses to store new tokens instead of silently
#     re-encrypting with the LLM gateway key.
#   * OAuth state uses a separate derived key so one secret never plays both
#     the token-encryption and the state-signing role.
# ---------------------------------------------------------------------------

_token_key_warning_issued = False


def _derive_fernet(secret: str) -> Fernet:
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest()))


def _legacy_secrets() -> list[str]:
    looked = settings.litellm_master_key or ""
    return [looked] if looked else []


def _read_fernet() -> MultiFernet:
    global _token_key_warning_issued
    keys: list[Fernet] = []
    explicit = (settings.threads_token_key or "").strip()
    if explicit:
        keys.append(_derive_fernet(explicit))
    if not explicit and not settings.litellm_master_key:
        raise ThreadsAPIError("Set THREADS_TOKEN_KEY or LITELLM_MASTER_KEY before storing tokens", category="config")
    if explicit:
        for legacy in _legacy_secrets():
            keys.append(_derive_fernet(legacy))
    else:
        if not _token_key_warning_issued:
            log.warning("threads_token_key_missing",
                        env=settings.app_env,
                        hint="Storage falls back to the LITELLM_MASTER_KEY stand-in; set THREADS_TOKEN_KEY")
            _token_key_warning_issued = True
        keys.append(_derive_fernet(settings.litellm_master_key))
    return MultiFernet(keys)


def _write_fernet() -> Fernet:
    explicit = (settings.threads_token_key or "").strip()
    if explicit:
        return _derive_fernet(explicit)
    if settings.app_env != "development":
        raise ThreadsAPIError(
            "THREADS_TOKEN_KEY is required to store tokens outside development (audit 1.2)",
            category="config",
        )
    if not settings.litellm_master_key:
        raise ThreadsAPIError("Set THREADS_TOKEN_KEY or LITELLM_MASTER_KEY before storing tokens", category="config")
    return _derive_fernet(settings.litellm_master_key)


def _state_fernet() -> Fernet:
    secret = settings.threads_token_key or settings.litellm_master_key
    if not secret:
        raise ThreadsAPIError("Set THREADS_TOKEN_KEY or LITELLM_MASTER_KEY before storing tokens", category="config")
    derived = hashlib.sha256(b"threads-oauth-state-v1:" + secret.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(derived))


def encrypt_token(token: str) -> str:
    return _write_fernet().encrypt(token.encode()).decode()


def token_uses_primary_key(payload: str) -> bool:
    """True when the payload already decrypts with the dedicated key alone.

    Fernet output is non-deterministic, so equality with a re-encryption never
    decides rotation; the key that can open the payload does.
    """
    explicit = (settings.threads_token_key or "").strip()
    if not explicit:
        # No dedicated key: legacy stand-in is also the write key, nothing to rotate.
        return True
    try:
        _derive_fernet(explicit).decrypt(payload.encode())
        return True
    except InvalidToken:
        return False


def decrypt_token(payload: str) -> str:
    try:
        return _read_fernet().decrypt(payload.encode()).decode()
    except InvalidToken as exc:
        raise ThreadsAPIError("Stored Threads token cannot be decrypted (encryption key changed?)", category="auth") from exc


# ---------------------------------------------------------------------------
# OAuth helpers (server-side only; the app secret never reaches the frontend)
# ---------------------------------------------------------------------------

def make_state() -> str:
    """Short-lived, tamper-evident OAuth state (10 minutes)."""
    return _state_fernet().encrypt(f"{uuid4().hex}:{int(time.time())}".encode()).decode()


def validate_state(state: str | None) -> bool:
    if not state:
        return False
    try:
        payload = _state_fernet().decrypt(state.encode()).decode()
        _, created = payload.rsplit(":", 1)
        return 0 <= (int(time.time()) - int(created)) <= 600
    except (InvalidToken, ThreadsAPIError, ValueError):
        return False


def authorize_url(redirect_uri: str, state: str) -> str:
    if not settings.threads_app_id:
        raise ThreadsAPIError("THREADS_APP_ID is not configured", category="config")
    query = urlencode({
        "client_id": settings.threads_app_id,
        "redirect_uri": redirect_uri,
        "scope": ",".join(OAUTH_SCOPES),
        "response_type": "code",
        "state": state,
    })
    return f"https://threads.com/oauth/authorize?{query}"


async def _oauth_request(method: str, path: str, *, params: dict | None = None, data: dict | None = None, headers: dict | None = None) -> dict[str, Any]:
    """Unauthenticated (or app-secret) OAuth call; never logs secrets."""
    base = settings.threads_api_base_url.rstrip("/")
    url = path if path.startswith("http") else f"{base}/{path.lstrip('/')}"
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.request(method, url, params=params, data=data, headers=headers)
    except httpx.HTTPError as exc:
        raise ThreadsAPIError(f"Meta OAuth endpoint unreachable: {type(exc).__name__}", retryable=True, category="unavailable") from exc
    payload = _error_payload(response)
    if payload is not None:
        raise _classify_error(response.status_code, payload)
    try:
        result = response.json()
        if not isinstance(result, dict):
            raise ValueError("Expected a JSON object")
        return result
    except ValueError as exc:
        raise ThreadsAPIError("Meta OAuth returned an invalid JSON object", status_code=response.status_code, category="api") from exc


def _error_payload(response: httpx.Response) -> dict[str, Any] | str | None:
    """Return Meta's complete error object so diagnostic fields are not lost."""
    payload = _safe_json(response)
    if response.status_code < 400 and "error" not in payload:
        return None
    if payload:
        error = payload.get("error", payload)
        return error if isinstance(error, (dict, str)) else str(error)[:300]
    return response.text[:300] or response.reason_phrase


def _error_body(response: httpx.Response) -> str | None:
    """Backward-compatible human-readable view used by older callers/tests."""
    payload = _error_payload(response)
    if payload is None:
        return None
    if isinstance(payload, dict):
        return str(payload.get("message") or payload)[:300]
    return payload[:300]


def _classify_error(status: int, payload: dict[str, Any] | str) -> ThreadsAPIError:
    error = payload if isinstance(payload, dict) else {}
    message = str(error.get("message") or payload)
    graph_code = error.get("code") if isinstance(error.get("code"), int) else None
    graph_subcode = error.get("error_subcode") if isinstance(error.get("error_subcode"), int) else None
    graph_type = str(error.get("type")) if error.get("type") else None
    fbtrace_id = str(error.get("fbtrace_id")) if error.get("fbtrace_id") else None
    is_transient = error.get("is_transient") is True
    lowered = message.lower()
    common = {
        "status_code": status,
        "graph_code": graph_code,
        "graph_subcode": graph_subcode,
        "graph_type": graph_type,
        "fbtrace_id": fbtrace_id,
    }
    if "api access blocked" in lowered or "application access blocked" in lowered:
        return ThreadsAPIError(
            f"Meta blocked API access for this app: {message[:200]}",
            category="app_blocked",
            **common,
        )
    if status in (401, 403) or graph_code == 190 or "expired" in lowered or "invalid access token" in lowered:
        return ThreadsAPIError(f"Threads authentication failed: {message[:200]}", category="auth", **common)
    if status == 429 or graph_code in _RATE_LIMIT_CODES or "rate limit" in lowered or "request limit" in lowered or "too many" in lowered:
        return ThreadsAPIError(f"Threads rate limit reached: {message[:200]}", retryable=True, category="rate_limit", **common)
    if "unsupported get request" in lowered or "does not support this operation" in lowered:
        # The media id cannot be loaded with the current account: it belongs to
        # another user or app, was deleted, or left the insights window. This
        # is a permanent condition, never a transient one.
        return ThreadsAPIError(f"Threads cannot load this object with the current account: {message[:200]}", category="not_owned", **common)
    if status >= 500 or is_transient:
        return ThreadsAPIError(f"Threads API unavailable: {message[:200]}", retryable=True, category="unavailable", **common)
    return ThreadsAPIError(f"Threads API error: {message[:200]}", category="api", **common)


async def exchange_code(code: str, redirect_uri: str) -> dict[str, Any]:
    """Authorization code -> short-lived user token + Threads user id."""
    if not settings.threads_app_id or not settings.threads_app_secret:
        raise ThreadsAPIError("THREADS_APP_ID / THREADS_APP_SECRET are not configured", category="config")
    return await _oauth_request(
        "POST",
        "/oauth/access_token",
        data={
            "client_id": settings.threads_app_id,
            "client_secret": settings.threads_app_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        },
    )


async def exchange_long_lived(short_token: str) -> dict[str, Any]:
    """Short-lived -> long-lived token (60 days, server-side only)."""
    if not settings.threads_app_secret:
        raise ThreadsAPIError("THREADS_APP_SECRET is not configured", category="config")
    return await _oauth_request(
        "GET",
        "/access_token",
        params={"grant_type": "th_exchange_token", "client_secret": settings.threads_app_secret, "access_token": short_token},
    )


async def refresh_long_lived(long_token: str) -> dict[str, Any]:
    """Refresh a long-lived token (allowed once it is 24h old and unexpired)."""
    return await _oauth_request("GET", "/refresh_access_token", params={"grant_type": "th_refresh_token", "access_token": long_token})


async def debug_token(token: str) -> dict[str, Any] | None:
    """Best-effort scope inspection; None when the app id/endpoint is unavailable."""
    if not settings.threads_app_id:
        return None
    try:
        return await _oauth_request(
            "GET", "/debug_token",
            params={"input_token": token, "app_id": settings.threads_app_id},
            headers={"Authorization": f"Bearer {token}"},
        )
    except ThreadsAPIError:
        return None


async def granted_scopes(token: str) -> list[str]:
    """Read the scopes Meta actually granted to this token.

    A token refresh does not add new scopes, and neither does a connection
    probe. The permission set therefore has to be re-read explicitly, which
    is what the "check connection" action does.
    """
    return scopes_from_debug(await debug_token(token))


def scopes_from_debug(debug: Any) -> list[str]:
    """Meta returns scopes; retain compatibility with older granted_scopes fixtures."""
    if not isinstance(debug, dict):
        return []
    data = debug.get("data") if isinstance(debug.get("data"), dict) else debug
    if data.get("is_valid") is False:
        return []
    granted = data.get("scopes", data.get("granted_scopes")) if isinstance(data, dict) else None
    if not isinstance(granted, list):
        return []
    return [str(item) for item in granted if item]


# ---------------------------------------------------------------------------
# Mock adapter (THREADS_MOCK_MODE=true) — no request can reach Meta
# ---------------------------------------------------------------------------

class MockThreadsClient:
    """Deterministic isolated adapter; no request can reach Meta from mock mode."""

    def __init__(self) -> None:
        self.cursor_dropped = 0

    async def profile(self) -> dict[str, Any]:
        return {"id": "mock-profile-1", "username": "demo_recruiter", "permissions": ["mock_content_publish", "mock_manage_replies"]}

    async def own_posts(self) -> list[dict[str, Any]]:
        return [
            {"id": "mock-remote-post-1", "timestamp": datetime.now(timezone.utc).isoformat(), "has_replies": True, "text": "Mock vacancy post for the demo recruiter."},
            {"id": "mock-mention-1", "timestamp": datetime.now(timezone.utc).isoformat(), "has_replies": False, "text": "Mock mention target."},
        ]

    async def publish_text(self, text: str, idempotency_key: str, reply_to_id: str | None = None) -> dict[str, Any]:
        if not text.strip():
            raise ThreadsAPIError("Cannot publish an empty post", category="api")
        prefix = f"mock-reply-{idempotency_key}" if reply_to_id else f"mock-{idempotency_key}"
        return {"id": prefix, "published_at": datetime.now(timezone.utc).isoformat(), "reply_to_id": reply_to_id}

    async def replies(self, since: datetime | None = None, *, skip_media_ids: set[str] | None = None, posts: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        items = [
            {"id": "mock-reply-1", "username": "candidate_demo", "text": "Чи є житло і яка ставка?", "timestamp": datetime.now(timezone.utc).isoformat(), "parent_id": "mock-remote-post-1"},
            {"id": "mock-reply-2", "username": "jobseeker_demo", "text": "Можу вийти завтра, шукаю роботу", "timestamp": datetime.now(timezone.utc).isoformat(), "parent_id": "mock-remote-post-1"},
        ]
        kept, dropped = _split_since(items, since)
        self.cursor_dropped = dropped
        return kept

    async def mentions(self, since: datetime | None = None) -> list[dict[str, Any]]:
        items = [{"id": "mock-mention-1", "username": "worker_demo", "text": "@demo_recruiter є вакансії у Кракові?", "timestamp": datetime.now(timezone.utc).isoformat(), "parent_id": "mock-mention-1"}]
        return _filter_since(items, since)

    async def metrics(self, remote_post_id: str) -> dict[str, int | None]:
        seed = sum(ord(c) for c in remote_post_id) % 20
        return {"views": 100 + seed * 5, "likes": 4 + seed, "replies": 2, "reposts": 1, "quotes": 0}

    async def publishing_limit(self) -> dict[str, Any]:
        return {"quota_usage": 0, "quota_total": 250}

    async def keyword_search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        return [{"id": "mock-topic-1", "text": f"Дискуссия по запросу: {query}", "permalink": "https://www.threads.net/", "timestamp": datetime.now(timezone.utc).isoformat()}]


def _split_since(items: list[dict[str, Any]], since: datetime | None) -> tuple[list[dict[str, Any]], int]:
    """Split items into (kept, dropped_by_cursor).

    The dropped count is returned so the worker can report how much of the
    inbox was older than the sync cursor. Silent drops made a broken cursor
    look like "no engagement" for eleven days.
    """
    if not since:
        return list(items), 0
    kept: list[dict[str, Any]] = []
    dropped = 0
    for item in items:
        try:
            stamp = datetime.fromisoformat(item["timestamp"])
        except (KeyError, ValueError):
            kept.append(item)
            continue
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        if stamp >= since:
            kept.append(item)
        else:
            dropped += 1
    return kept, dropped


def _filter_since(items: list[dict[str, Any]], since: datetime | None) -> list[dict[str, Any]]:
    kept, _ = _split_since(items, since)
    return kept


# ---------------------------------------------------------------------------
# Live adapter (THREADS_MOCK_MODE=false) — official graph.threads.net only
# ---------------------------------------------------------------------------

class LiveThreadsClient:
    """Official Threads API adapter with bounded retries and safe logging."""

    # Keep a high safety cap while cursor paging remains bounded.  This avoids
    # silently dropping older owned posts from the hourly comment monitor.
    RECENT_POSTS_SCAN_LIMIT = 1000
    PAGE_LIMIT = 25

    def __init__(self, access_token: str, profile_id: str | None = None, *, transport: httpx.AsyncBaseTransport | None = None):
        if not access_token:
            raise ThreadsAPIError("Threads access token is not configured", category="config")
        self.access_token = access_token
        self.profile_id = profile_id
        self.api_base = f"{settings.threads_api_base_url.rstrip('/')}/v1.0"
        self.timeout = settings.threads_request_timeout
        self.max_retries = settings.threads_max_retries
        self._client = httpx.AsyncClient(timeout=self.timeout, transport=transport)
        self._profile_username: str | None = None
        # How many replies the sync cursor hid during the last read. The
        # worker reports it so a broken cursor is visible instead of silent.
        self.cursor_dropped = 0

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, path: str, *, params: dict | None = None, data: dict | None = None, retry: bool = True) -> dict[str, Any]:
        url = f"{self.api_base}/{path.lstrip('/')}"
        headers = {"Authorization": f"Bearer {self.access_token}"}
        attempts = self.max_retries + 1 if retry else 1
        attempt = 0
        while True:
            attempt += 1
            try:
                response = await self._client.request(method, url, params=params, data=data, headers=headers)
            except httpx.HTTPError as exc:
                if attempt >= attempts:
                    error = ThreadsAPIError(f"Threads API unreachable: {type(exc).__name__}", retryable=True, category="unavailable")
                    if method.upper() == "POST":
                        error.duplicate_risk = True  # outcome unknown after a POST
                    raise error from exc
                await asyncio.sleep(min(8.0, 0.5 * (2 ** attempt)))
                continue
            # Structured log without query params or tokens.
            log.info("threads_api_request", method=method, path=path, status=response.status_code, attempt=attempt)
            error_payload = _error_payload(response)
            if error_payload is not None:
                error = _classify_error(response.status_code, error_payload)
                if response.status_code >= 500 and method.upper() == "POST":
                    error.duplicate_risk = True
                if error.retryable and attempt < attempts:
                    retry_after = response.headers.get("Retry-After")
                    delay = float(retry_after) if (retry_after or "").isdigit() else min(16.0, 1.0 * (2 ** attempt))
                    await asyncio.sleep(min(delay, 30.0))
                    continue
                raise error
            try:
                result = response.json()
                if not isinstance(result, dict):
                    raise ValueError("Expected a JSON object")
                return result
            except ValueError as exc:
                error = ThreadsAPIError("Threads API returned a non-JSON response", status_code=response.status_code, category="api")
                if method.upper() == "POST":
                    error.duplicate_risk = True
                raise error from exc

    async def _require_profile_id(self) -> str:
        if not self.profile_id:
            profile = await self.profile()
            self.profile_id = str(profile["id"])
        return self.profile_id

    async def _paged_request(self, method: str, path: str, *, params: dict[str, Any], max_pages: int = 20) -> list[dict[str, Any]]:
        """Follow Meta cursor paging without ever copying a token from paging.next."""
        collected: list[dict[str, Any]] = []
        request_params = dict(params)
        for _ in range(max_pages):
            page = await self._request(method, path, params=request_params)
            data = page.get("data") or []
            if isinstance(data, list):
                collected.extend(item for item in data if isinstance(item, dict))
            paging = page.get("paging") or {}
            cursors = paging.get("cursors") or {}
            after = cursors.get("after")
            if not after:
                next_url = paging.get("next")
                if next_url:
                    after_values = parse_qs(urlparse(str(next_url)).query).get("after")
                    after = after_values[0] if after_values else None
            if not after or not data:
                break
            request_params["after"] = after
        return collected

    async def profile(self) -> dict[str, Any]:
        result = await self._request("GET", "me", params={"fields": "id,username"})
        self.profile_id = str(result.get("id") or self.profile_id or "") or self.profile_id
        self._profile_username = normalize_remote_username(result.get("username"))
        return result

    async def publishing_limit(self) -> dict[str, Any]:
        uid = await self._require_profile_id()
        payload = await self._request("GET", f"{uid}/threads_publishing_limit", params={"fields": "quota_usage,config,reply_quota_usage,reply_config"})
        # The live endpoint answers with {"data": [ {...} ]}. Returning the
        # wrapper made every quota lookup miss, so the publishing limit was
        # never enforced.
        data = payload.get("data") if isinstance(payload, dict) else None
        if isinstance(data, list) and data and isinstance(data[0], dict):
            return data[0]
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _quota_total(limit: dict[str, Any], total_key: str = "quota_total") -> int | None:
        """Read the quota ceiling from any shape Meta returns.

        The live endpoint answers with ``config`` as an object, while other
        docs and the mock adapter use a list or a flat field. All three are
        accepted, otherwise the ceiling is never resolved and the publishing
        quota guard silently does nothing.
        """
        config = limit.get("config")
        if isinstance(config, dict):
            value = config.get(total_key)
            return int(value) if isinstance(value, (int, float)) else None
        if isinstance(config, list) and config and isinstance(config[0], dict):
            value = config[0].get(total_key)
            return int(value) if isinstance(value, (int, float)) else None
        value = limit.get(total_key)
        return int(value) if isinstance(value, (int, float)) else None

    async def _check_quota(self, kind: str = "quota_usage", total_key: str = "quota_total") -> None:
        try:
            limit = await self.publishing_limit()
        except ThreadsAPIError as exc:
            # A quota endpoint outage is advisory. Authentication/app blocks and
            # rate limits are authoritative and must not be hidden by a second
            # publish request.
            if exc.category in {"app_blocked", "auth", "config", "rate_limit"}:
                raise
            return
        usage = limit.get(kind)
        total = self._quota_total(limit, total_key)
        if isinstance(usage, int) and isinstance(total, int) and total > 0 and usage >= total:
            raise ThreadsAPIError(
                f"Threads publishing quota exhausted ({usage}/{total}) — retry after the quota window resets",
                status_code=429, retryable=True, category="rate_limit",
            )

    async def publish_text(self, text: str, idempotency_key: str, reply_to_id: str | None = None) -> dict[str, Any]:
        """Official two-step publish: container -> status check -> publish."""
        if not text.strip():
            raise ThreadsAPIError("Cannot publish an empty post", category="api")
        if len(text) > 500:
            raise ThreadsAPIError("Post exceeds the 500-character Threads limit", category="api")
        uid = await self._require_profile_id()
        await self._check_quota("quota_usage", "quota_total")
        container_params: dict[str, Any] = {"media_type": "TEXT", "text": text}
        if reply_to_id:
            container_params["reply_to_id"] = reply_to_id
        try:
            container = await self._request("POST", f"{uid}/threads", data=container_params)
        except ThreadsAPIError as exc:
            exc.duplicate_risk = False  # creating a container never publishes
            raise
        creation_id = str(container.get("id") or "")
        if not creation_id:
            raise ThreadsAPIError("Threads did not return a media container id", category="api")
        # Text containers normally finish immediately; check before publishing so
        # failures surface as a readable error while retries are still duplicate-safe.
        for _ in range(3):
            status = await self._request("GET", creation_id, params={"fields": "status,error_message"})
            state = str(status.get("status") or "").upper()
            if state in ("", "FINISHED", "PUBLISHED"):
                break
            if state == "ERROR":
                raise ThreadsAPIError(f"Threads rejected the media container: {status.get('error_message') or 'unknown error'}", category="api")
            await asyncio.sleep(1.0)
        try:
            # The publish POST is never retried automatically: an ambiguous
            # outcome must be surfaced to the operator instead of duplicated.
            result = await self._request("POST", f"{uid}/threads_publish", data={"creation_id": creation_id}, retry=False)
        except ThreadsAPIError as exc:
            if exc.category == "unavailable":
                exc.duplicate_risk = True
            raise
        remote_id = str(result.get("id") or "")
        if not remote_id:
            error = ThreadsAPIError(
                "Threads accepted the publish request but returned no media id; operator reconciliation is required",
                retryable=False,
                category="unknown_outcome",
            )
            error.duplicate_risk = True
            raise error
        return {"id": remote_id, "reply_to_id": reply_to_id, "published_at": datetime.now(timezone.utc).isoformat()}

    async def own_posts(self) -> list[dict[str, Any]]:
        """List the account's own posts.

        The list request must never carry the sync cursor: Meta applies
        `since` to the publication time of the POST, not to the replies, so a
        cursor there hides every reply that arrived more than an hour after
        its post was published.
        """
        uid = await self._require_profile_id()
        params = {"fields": "id,timestamp,has_replies,text", "limit": self.PAGE_LIMIT}
        return await self._paged_request("GET", f"{uid}/threads", params=params)

    async def replies(
        self,
        since: datetime | None = None,
        *,
        skip_media_ids: set[str] | None = None,
        posts: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Read the complete reply tree to owned posts with cursor paging.

        Meta exposes ``conversation`` for nested replies and ``replies`` for
        top-level compatibility.  We prefer the former and fall back only when
        the account/app version does not expose it.
        """
        uid = await self._require_profile_id()
        skip = {str(item) for item in (skip_media_ids or set())}
        # No `since` here on purpose: it filters the post list by publication
        # time and would hide every reply to an older post. The cursor is
        # applied to the replies below, where it belongs.
        post_list = posts if posts is not None else await self.own_posts()
        if self._profile_username is None:
            with contextlib.suppress(ThreadsAPIError):
                await self.profile()
        collected: list[dict[str, Any]] = []
        for post in post_list[: self.RECENT_POSTS_SCAN_LIMIT]:
            media_id = str(post.get("id") or "")
            if not media_id or not post.get("has_replies"):
                continue
            if media_id in skip:
                continue
            thread_params = {"fields": "id,username,text,timestamp,replied_to,root_post,has_replies,is_reply_owned_by_me", "limit": self.PAGE_LIMIT, "reverse": "false"}
            try:
                items = await self._paged_request("GET", f"{media_id}/conversation", params=thread_params)
            except ThreadsAPIError as exc:
                if exc.status_code not in {400, 404}:
                    raise
                items = await self._paged_request("GET", f"{media_id}/replies", params=thread_params)
            for item in items:
                username = normalize_remote_username(item.get("username"))
                if item.get("is_reply_owned_by_me") or (self._profile_username and username == self._profile_username):
                    continue
                replied_to = item.get("replied_to") or {}
                item["parent_id"] = str(replied_to.get("id") or media_id)
                root_post = item.get("root_post") or {}
                item["root_post_id"] = str(root_post.get("id") or media_id)
                collected.append(item)
        kept, dropped = _split_since(collected, since)
        self.cursor_dropped = dropped
        return kept

    async def mentions(self, since: datetime | None = None) -> list[dict[str, Any]]:
        """Public posts mentioning the account (GET /{user-id}/mentions)."""
        uid = await self._require_profile_id()
        params: dict[str, Any] = {"fields": "id,username,text,timestamp", "limit": self.PAGE_LIMIT}
        if since:
            params["since"] = int(since.timestamp())
        data = await self._paged_request("GET", f"{uid}/mentions", params=params)
        items = []
        for item in data:
            # Reply target for a mention is the mentioning post itself.
            item["parent_id"] = str(item.get("id") or "")
            items.append(item)
        return _filter_since(items, since)

    async def metrics(self, remote_post_id: str) -> dict[str, int | None]:
        data = await self._request("GET", f"{remote_post_id}/insights", params={"metric": "views,likes,replies,reposts,quotes"})
        metrics: dict[str, int | None] = {}
        for entry in data.get("data", []):
            values = entry.get("values") or [{}]
            metrics[entry["name"]] = values[-1].get("value")
        return metrics

    async def keyword_search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        data = await self._request("GET", "keyword_search", params={
            "q": query[:100], "search_type": "RECENT",
            "fields": "id,text,username,timestamp,permalink", "limit": max(1, min(limit, 25)),
        })
        return data.get("data") or []


def _safe_json(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
        return payload if isinstance(payload, dict) else {}
    except ValueError:
        return {}


def verify_webhook_signature(raw_body: bytes, signature_header: str | None) -> bool:
    """Validate Meta's X-Hub-Signature-256 (sha256 of body keyed by app secret)."""
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    if not settings.threads_app_secret:
        return False
    expected = hmac_sha256_hex(settings.threads_app_secret.encode(), raw_body)
    return hmac_compare(expected, signature_header.split("=", 1)[1])


def hmac_sha256_hex(key: bytes, body: bytes) -> str:
    return hmac.new(key, body, hashlib.sha256).hexdigest()


def hmac_compare(a: str, b: str) -> bool:
    return hmac.compare_digest(a, b)


def get_threads_client(profile_id: str | None = None, access_token: str | None = None) -> ThreadsAdapter:
    if settings.threads_mock_mode:
        return MockThreadsClient()
    return LiveThreadsClient(access_token or settings.threads_access_token, profile_id)
