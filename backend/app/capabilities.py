"""Capability report: which Threads permissions the account actually has.

The application can only do what Meta allows it to do. Missing permissions
fail silently: replies outside our own posts never arrive, public discussions
stay invisible, and the audience profile stays one-sided. This module turns
that into an explicit, ordered list with the concrete cost of every gap and
the exact place to fix it.

The report is read-only and never contacts Meta. It compares the scopes
stored on the connected account with what the product needs.
"""
from __future__ import annotations

from sqlalchemy import select

from .models import ThreadsAccount

# scope, what it is for, what it unlocks, what stays broken without it
REQUIRED_SCOPES = (
    {
        "scope": "threads_basic",
        "title": "Базовый доступ к профилю",
        "unlocks": "Чтение профиля аккаунта, квоты публикаций и проверка подключения.",
        "cost": "Без него аккаунт не подключается.",
        "required": True,
    },
    {
        "scope": "threads_content_publish",
        "title": "Публикация постов",
        "unlocks": "Создание и публикация текстовых постов и ответов.",
        "cost": "Без него нельзя опубликовать ни пост, ни ответ.",
        "required": True,
    },
    {
        "scope": "threads_read_replies",
        "title": "Чтение ответов",
        "unlocks": "Чтение дерева ответов под нашими постами.",
        "cost": "Без него мы не видим, как нам отвечают под нашими постами.",
        "required": True,
    },
    {
        "scope": "threads_manage_replies",
        "title": "Управление ответами",
        "unlocks": "Отправка ответов и упоминаний в обсуждения.",
        "cost": "Без него мы читаем вопросы, но не можем на них ответить в треде.",
        "required": False,
    },
    {
        "scope": "threads_manage_insights",
        "title": "Статистика публикаций",
        "unlocks": "Просмотры, ответы, репосты и цитаты по своим постам.",
        "cost": "Без него часть метрик недоступна и сравнение форматов неполно.",
        "required": False,
    },
    {
        "scope": "threads_manage_mentions",
        "title": "Упоминания аккаунта",
        "unlocks": "Публичные посты, в которых упомянут наш аккаунт.",
        "cost": "Без него мы не видим спрос, который возникает вне наших постов.",
        "required": False,
    },
    {
        "scope": "threads_keyword_search",
        "title": "Поиск публичных обсуждений",
        "unlocks": "Поиск чужих обсуждений по ключевым словам.",
        "cost": "Без него разведчик не находит публичные треды и идёт только по новостям.",
        "required": False,
    },
)


async def capability_report(db) -> dict:
    """Compare the granted scopes with what the product needs."""
    account = (await db.execute(
        select(ThreadsAccount)
        .where(ThreadsAccount.connected.is_(True))
        .order_by(ThreadsAccount.id.desc())
        .limit(1)
    )).scalar_one_or_none()
    granted = set(account.permissions or []) if account is not None else set()
    rows = []
    for item in REQUIRED_SCOPES:
        rows.append({
            "scope": item["scope"],
            "title": item["title"],
            "granted": item["scope"] in granted,
            "required": item["required"],
            "unlocks": item["unlocks"],
            "cost": item["cost"],
        })
    missing = [row for row in rows if not row["granted"]]
    missing_required = [row for row in missing if row["required"]]
    connected = account is not None
    return {
        "connected": connected,
        "username": account.username if account is not None else None,
        "granted": sorted(granted),
        "scopes": rows,
        "missing": [row["scope"] for row in missing],
        "missing_required": [row["scope"] for row in missing_required],
        "complete": not missing,
        "reconnect_required": connected and bool(missing),
        "summary": (
            "Все нужные разрешения выданы."
            if not missing else
            "Не хватает разрешений: " + ", ".join(row["scope"] for row in missing)
            + ". Проверьте выбранные права токена и доступ приложения в Meta Developer Portal."
        ),
        "audience_impact": _audience_impact(missing),
        "where": "Meta Developer Portal → Threads API → Permissions and Features",
        "how_to_get": _how_to_get(missing, connected),
    }


def _how_to_get(missing: list[dict], connected: bool) -> list[str]:
    """The concrete steps for the operator, in the order they must happen."""
    if not missing:
        return []
    names = ", ".join(row["scope"] for row in missing)
    steps = [
        "1. Проверить доступ приложения к разрешениям: " + names + ", роль Threads Tester и принятие приглашения.",
        "2. Для пользователей вне ролей приложения проверить необходимость App Review; отсутствие scope у токена само по себе не доказывает отказ Meta. Справка: docs/meta-scopes-request.md.",
    ]
    if connected:
        steps.append(
            "3. Переподключить аккаунт: обновление токена не добавляет новые разрешения, "
            "нужен новый токен с выбранными разрешениями."
        )
        steps.append(
            "4. Нажать «Проверить подключение» — приложение перечитает выданные разрешения и обновит этот отчёт."
        )
    else:
        steps.append("3. Подключить аккаунт — при подключении запрашиваются все разрешения сразу.")
    return steps


def _audience_impact(missing: list[dict]) -> list[str]:
    """What the audience profile cannot see because of a missing scope."""
    impact: list[str] = []
    scopes = {row["scope"] for row in missing}
    if "threads_manage_mentions" in scopes:
        impact.append("Упоминания аккаунта в чужих постах не видны — профиль аудитории собирается только из ответов под нашими публикациями.")
    if "threads_manage_replies" in scopes:
        impact.append("Ответить в чужом обсуждении нельзя, даже если там спрашивают о вакансиях.")
    if "threads_read_replies" in scopes:
        impact.append("Ответы под нашими постами не читаются — спрос аудитории остаётся невидимым.")
    if "threads_keyword_search" in scopes:
        impact.append("Публичные обсуждения не ищутся: разведчик видит только новости, а не живой спрос.")
    if "threads_manage_insights" in scopes:
        impact.append("Метрики по охвату неполны, поэтому выбор формата опирается на неполные данные.")
    return impact
