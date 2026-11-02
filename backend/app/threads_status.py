from __future__ import annotations

from datetime import datetime, timezone

from .models import ThreadsAccount
from .threads_client import ThreadsAPIError


def mark_threads_error(account: ThreadsAccount, error: ThreadsAPIError) -> None:
    """Persist a safe, actionable API state without storing credentials."""
    account.last_error = str(error)[:1000]
    account.last_error_code = error.graph_code or error.status_code
    account.last_error_subcode = error.graph_subcode
    account.last_error_trace_id = error.fbtrace_id
    if error.category == "app_blocked":
        account.api_status = "BLOCKED"
        account.blocked_at = datetime.now(timezone.utc)
    elif error.category == "auth":
        account.api_status = "AUTH_ERROR"
        account.blocked_at = None
    else:
        account.api_status = "DEGRADED"


def mark_threads_ready(account: ThreadsAccount) -> None:
    account.api_status = "READY"
    account.last_error = None
    account.last_error_code = None
    account.last_error_subcode = None
    account.last_error_trace_id = None
    account.blocked_at = None
    account.last_success_at = datetime.now(timezone.utc)


def threads_token_expired(account: ThreadsAccount, now: datetime | None = None) -> bool:
    expiry = account.token_expires_at
    if expiry is None:
        return False
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    return expiry <= (now or datetime.now(timezone.utc))


def threads_publishing_ready(account: ThreadsAccount | None) -> bool:
    if account is None or not account.connected:
        return False
    if account.is_mock:
        return True
    if threads_token_expired(account):
        return False
    return account.api_status == "READY" and "threads_content_publish" in (account.permissions or [])
