from datetime import datetime, timedelta, timezone

from .threads_status import threads_token_expired


def public_search_unavailable_reason(account, now=None):
    """Check current capability before using historical search evidence."""
    if account is None or not account.connected:
        return 'Подключите аккаунт Threads для поиска публичных обсуждений.'
    if account.is_mock:
        return 'Тестовый аккаунт не собирает реальные обсуждения Threads.'
    if not account.access_token_encrypted:
        return 'Токен Threads отсутствует. Подключите аккаунт повторно.'
    if threads_token_expired(account, now):
        return 'Токен Threads истёк. Подключите аккаунт повторно.'
    if account.api_status in ('BLOCKED', 'AUTH_ERROR'):
        return 'Доступ Threads ограничен. Проверьте подключение аккаунта.'
    if 'threads_keyword_search' not in (account.permissions or []):
        return 'Нет разрешения threads_keyword_search. Разрешите поиск в Meta и подключите аккаунт повторно; публикация не требует этого разрешения.'
    return None


def radar_status(state, now=None):
    """Saved topics are not proof that the current integration is healthy."""
    now = now or datetime.now(timezone.utc)
    if state and state.last_error:
        return 'unavailable'
    if not state or not state.last_success_at:
        return 'no_recent_data'
    last = state.last_success_at
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return 'available' if last >= now - timedelta(days=1) else 'no_recent_data'
