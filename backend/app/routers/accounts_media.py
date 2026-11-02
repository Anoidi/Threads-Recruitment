"""Threads OAuth/webhook/account, Instagram/TikTok social accounts, and ComfyUI media routes."""
from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import FileResponse, RedirectResponse

from ..comfyui import ComfyUIError, comfyui_health, detect_media, resolve_asset
from ..config import settings
from ..database import get_db
from ..enums import JobStatus, MediaGenerationStatus, MediaKind, PostStatus, SocialPlatform
from ..logging import get_logger
from ..models import BackgroundJob, MediaGeneration, SocialAccount, ThreadsAccount, ThreadsPost
from ..queue import enqueue
from ..schemas import (
    AccountRead,
    MediaGenerationRead,
    MediaGenerationRequest,
    MediaStatusRead,
    SocialAccountRead,
    SocialTokenConnectRequest,
    TokenConnectRequest,
)
from ..security import require_local_operator, require_manual_token_import
from ..services import activity
from ..social_clients import (
    SOCIAL_OAUTH_SCOPES,
    SocialAPIError,
    authorize_url as social_authorize_url,
    decrypt_social_token,
    encrypt_social_token,
    instagram_profile,
    is_configured as social_provider_configured,
    make_oauth_state as make_social_state,
    tiktok_profile,
    validate_oauth_state as validate_social_state,
)
from ..threads_client import (
    OAUTH_SCOPES,
    ThreadsAPIError,
    authorize_url,
    decrypt_token,
    encrypt_token,
    granted_scopes,
    make_state,
    scopes_from_debug,
    validate_state,
    verify_webhook_signature,
)
from ..threads_status import mark_threads_error, mark_threads_ready, threads_publishing_ready

Db = Annotated[AsyncSession, Depends(get_db)]
router = APIRouter()
log = get_logger("api")

_SOCIAL_OAUTH_STATES: dict[str, tuple[SocialPlatform, datetime]] = {}


def _bind_threads_state(response: Response, state: str) -> None:
    response.set_cookie(
        "threads_oauth_state", state, max_age=600, httponly=True,
        secure=settings.threads_redirect_uri.startswith("https://"),
        samesite="lax", path="/api/threads/",
    )
    response.headers["Cache-Control"] = "no-store"


def _remember_social_state(platform: SocialPlatform) -> str:
    now = datetime.now(timezone.utc)
    for key, (_, expires) in list(_SOCIAL_OAUTH_STATES.items()):
        if expires <= now:
            _SOCIAL_OAUTH_STATES.pop(key, None)
    state = make_social_state(platform)
    _SOCIAL_OAUTH_STATES[state] = (platform, now + timedelta(minutes=10))
    return state


def _consume_social_state(platform: SocialPlatform, state: str | None) -> bool:
    if not state or not validate_social_state(state, platform):
        return False
    stored = _SOCIAL_OAUTH_STATES.pop(state, None)
    return bool(stored and stored[0] == platform and stored[1] > datetime.now(timezone.utc))


def _social_token_status(row: SocialAccount | None) -> str:
    if row is None or not row.connected:
        return "MISSING"
    if row.token_expires_at is None:
        return "VALID"
    expiry = row.token_expires_at.replace(tzinfo=timezone.utc) if row.token_expires_at.tzinfo is None else row.token_expires_at
    now = datetime.now(timezone.utc)
    if expiry <= now:
        return "EXPIRED"
    if expiry <= now + timedelta(days=7):
        return "EXPIRING"
    return "VALID"


def _social_account_read(platform: SocialPlatform, row: SocialAccount | None) -> SocialAccountRead:
    configured = social_provider_configured(platform)
    return SocialAccountRead(
        platform=platform,
        connected=bool(row and row.connected),
        provider_configured=configured,
        external_id=row.external_id if row else None,
        username=row.username if row else None,
        permissions=list(row.permissions or []) if row else [],
        token_status="CONFIG_ERROR" if not configured and not row else _social_token_status(row),
        token_expires_at=row.token_expires_at if row else None,
        last_success_at=row.last_success_at if row else None,
        last_error=row.last_error if row else None,
    )


def _media_generation_read(row: MediaGeneration) -> MediaGenerationRead:
    value = MediaGenerationRead.model_validate(row)
    return value.model_copy(
        update={"asset_url": f"/api/media/assets/{row.id}" if row.status == MediaGenerationStatus.READY and row.output_filename else None}
    )


@router.get("/threads/oauth/start")
async def oauth_start():
    """Redirect the browser to Meta's official Authorization Window."""
    if settings.threads_mock_mode:
        raise HTTPException(409, "Mock mode is active: set THREADS_MOCK_MODE=false to connect a real account.")
    if not settings.threads_app_id or not settings.threads_app_secret:
        raise HTTPException(501, "THREADS_APP_ID / THREADS_APP_SECRET are not configured. See docs/threads-api-readiness.md.")
    state = make_state()
    response = RedirectResponse(authorize_url(settings.threads_redirect_uri, state), status_code=302)
    _bind_threads_state(response, state)
    return response


@router.get("/threads/oauth/callback")
async def oauth_callback(request: Request, db: Db, code: str | None = None, state: str | None = None, error: str | None = None):
    """Server-side code exchange: the app secret and token never leave the backend."""
    from .. import api as _api

    target = f"{settings.frontend_url.rstrip('/')}/account"

    def finish(query: str):
        response = RedirectResponse(f"{target}?{query}", status_code=302)
        response.delete_cookie("threads_oauth_state", path="/api/threads/")
        response.headers["Cache-Control"] = "no-store"
        return response

    browser_state = request.cookies.get("threads_oauth_state", "")
    if not validate_state(state) or not browser_state or not hmac.compare_digest(browser_state.encode(), (state or "").encode()):
        return finish("threads_error=state")
    if error or not code:
        return finish("threads_error=denied")
    try:
        short = await _api.exchange_code(code, settings.threads_redirect_uri)
        long_lived = await _api.exchange_long_lived(str(short["access_token"]))
    except ThreadsAPIError as exc:
        return finish(f"threads_error={exc.category}")
    except (KeyError, TypeError, ValueError):
        return finish("threads_error=token_exchange")
    token = str(long_lived.get("access_token") or "")
    if not token:
        return finish("threads_error=token_exchange")
    profile_id = str(short.get("user_id") or "")
    client = _api.LiveThreadsClient(token, profile_id or None)
    publishing_probe_error: ThreadsAPIError | None = None
    try:
        profile = await client.profile()
        profile_id = str(profile["id"])
        username = str(profile.get("username") or "unknown")
        publishing_probe = getattr(client, "publishing_limit", None)
        if publishing_probe is not None:
            try:
                await publishing_probe()
            except ThreadsAPIError as exc:
                publishing_probe_error = exc
    except ThreadsAPIError as exc:
        return finish(f"threads_error={exc.category}")
    finally:
        with contextlib.suppress(Exception):
            await client.aclose()
    debug = await _api.debug_token(token)
    permissions = scopes_from_debug(debug) or ["threads_basic"]
    expires_at = None
    expires_in = str(long_lived.get("expires_in") or "")
    if expires_in.isdigit():
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))
    row = (await db.execute(select(ThreadsAccount).where(ThreadsAccount.profile_id == profile_id))).scalar_one_or_none()
    if row is None:
        row = ThreadsAccount(profile_id=profile_id, username=username)
        db.add(row)
    # The product selects one active Threads account. Match manual import:
    # reconnecting an older profile must not leave a newer profile active.
    others = list((await db.execute(select(ThreadsAccount).where(
        ThreadsAccount.connected.is_(True), ThreadsAccount.profile_id != profile_id,
    ))).scalars())
    for other in others:
        other.connected = False
    row.username = username
    row.access_token_encrypted = encrypt_token(token)
    row.permissions = permissions
    row.token_expires_at = expires_at
    row.connected = True
    row.is_mock = False
    if publishing_probe_error is None:
        mark_threads_ready(row)
    else:
        mark_threads_error(row, publishing_probe_error)
    await activity(db, "threads_account_connected", "threads_account", row.id, {"username": username})
    await db.commit()
    return finish("threads_connected=1")


@router.get("/threads/webhook")
async def webhook_verify(
    hub_mode: Annotated[str | None, Query(alias="hub.mode")] = None,
    hub_verify_token: Annotated[str | None, Query(alias="hub.verify_token")] = None,
    hub_challenge: Annotated[str | None, Query(alias="hub.challenge")] = None,
) -> Response:
    """Meta webhook subscription verification (GET challenge)."""
    if hub_mode != "subscribe" or not settings.threads_webhook_verify_token or hub_verify_token != settings.threads_webhook_verify_token:
        raise HTTPException(403, "Webhook verification failed")
    return Response(content=hub_challenge or "", media_type="text/plain")


@router.post("/threads/webhook")
async def webhook_notify(request: Request, db: Db):
    """Real-time Threads notifications."""
    raw = await request.body()
    if settings.threads_app_secret and not verify_webhook_signature(raw, request.headers.get("X-Hub-Signature-256")):
        raise HTTPException(403, "Webhook signature verification failed")
    if not settings.threads_app_secret and not settings.threads_mock_mode:
        raise HTTPException(403, "Webhook signature is required when not in mock mode")
    try:
        payload = json.loads(raw or b"{}")
    except ValueError:
        payload = {}
    entries = payload if isinstance(payload, list) else payload.get("entry") or payload.get("values") or []
    topic = payload.get("topic") if isinstance(payload, dict) else None
    if not topic and isinstance(entries, list) and entries and isinstance(entries[0], dict):
        topic = entries[0].get("field") or entries[0].get("topic")
    if topic in {"replies", "mentions", "publish", "delete", "moderate", "interaction"}:
        await enqueue(db, "sync_community", {}, idempotency_key=f"sync-community:webhook:{datetime.now(timezone.utc).strftime('%Y%m%d%H%M')}")
    log.info("threads_webhook_received", topic=topic or "unknown")
    return {"status": "received"}


@router.get("/social/accounts", response_model=list[SocialAccountRead])
async def social_accounts(request: Request, db: Db):
    require_local_operator(request)
    rows = list((await db.execute(select(SocialAccount).where(SocialAccount.connected.is_(True)).order_by(desc(SocialAccount.id)))).scalars())
    latest = {row.platform: row for row in rows}
    return [_social_account_read(platform, latest.get(platform)) for platform in SocialPlatform]


@router.post("/social/{platform}/oauth/start")
async def social_oauth_start(platform: SocialPlatform, request: Request):
    require_local_operator(request)
    if not social_provider_configured(platform):
        raise HTTPException(501, f"{platform.value} OAuth is not configured")
    state = _remember_social_state(platform)
    try:
        url = social_authorize_url(platform, state)
    except SocialAPIError as exc:
        raise HTTPException(501, str(exc)) from exc
    return {"status": "redirect_required", "platform": platform, "authorize_url": url}


@router.get("/social/oauth/{platform}/callback")
async def social_oauth_callback(
    platform: SocialPlatform,
    request: Request,
    db: Db,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
):
    from .. import api as _api

    require_local_operator(request)
    target = f"{settings.frontend_url.rstrip('/')}/account"
    platform_key = platform.value.lower()
    if error or not code:
        return RedirectResponse(f"{target}?social_error={platform_key}:denied", status_code=302)
    if not _consume_social_state(platform, state):
        return RedirectResponse(f"{target}?social_error={platform_key}:state", status_code=302)
    try:
        token = await _api.exchange_social_code(platform, code)
        row = (
            await db.execute(select(SocialAccount).where(SocialAccount.platform == platform, SocialAccount.external_id == token.external_id))
        ).scalar_one_or_none()
        if row is None:
            row = SocialAccount(platform=platform, external_id=token.external_id, access_token_encrypted=encrypt_social_token(token.access_token))
            db.add(row)
        row.username = token.username
        row.access_token_encrypted = encrypt_social_token(token.access_token)
        row.refresh_token_encrypted = encrypt_social_token(token.refresh_token) if token.refresh_token else None
        row.permissions = token.permissions
        row.token_expires_at = token.expires_at
        row.connected = True
        row.last_success_at = datetime.now(timezone.utc)
        row.last_error = None
        await activity(db, "social_account_connected", "social_account", row.id, {"platform": platform.value, "username": token.username or ""})
        await db.commit()
    except SocialAPIError as exc:
        return RedirectResponse(f"{target}?social_error={platform_key}:{exc.category}", status_code=302)
    except (KeyError, TypeError, ValueError):
        return RedirectResponse(f"{target}?social_error={platform_key}:token_exchange", status_code=302)
    return RedirectResponse(f"{target}?social_connected={platform_key}", status_code=302)


@router.post("/social/{platform}/token", response_model=SocialAccountRead)
async def social_token_connect(platform: SocialPlatform, payload: SocialTokenConnectRequest, request: Request, db: Db):
    require_local_operator(request)
    require_manual_token_import()
    token = payload.access_token.strip()
    try:
        profile = await (instagram_profile(token) if platform == SocialPlatform.INSTAGRAM else tiktok_profile(token))
    except SocialAPIError as exc:
        raise HTTPException(502, {"message": str(exc), "category": exc.category, "code": exc.code}) from exc
    external_id = payload.external_id or str(profile.get("id") or profile.get("open_id") or "")
    username = payload.username or str(profile.get("username") or profile.get("display_name") or "")
    if not external_id:
        raise HTTPException(502, "Provider did not return an account id")
    row = (await db.execute(select(SocialAccount).where(SocialAccount.platform == platform, SocialAccount.external_id == external_id))).scalar_one_or_none()
    if row is None:
        row = SocialAccount(platform=platform, external_id=external_id, access_token_encrypted=encrypt_social_token(token))
        db.add(row)
    row.username = username or None
    row.access_token_encrypted = encrypt_social_token(token)
    row.refresh_token_encrypted = encrypt_social_token(payload.refresh_token) if payload.refresh_token else None
    row.permissions = list(SOCIAL_OAUTH_SCOPES.get(platform, ()))
    row.token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=payload.expires_in) if payload.expires_in else None
    row.connected = True
    row.last_success_at = datetime.now(timezone.utc)
    row.last_error = None
    await activity(db, "social_account_connected", "social_account", row.id, {"platform": platform.value, "username": username, "method": "manual_token"})
    await db.commit()
    await db.refresh(row)
    return _social_account_read(platform, row)


@router.post("/social/{platform}/test", response_model=SocialAccountRead)
async def social_account_test(platform: SocialPlatform, request: Request, db: Db):
    require_local_operator(request)
    row = (
        await db.execute(select(SocialAccount).where(SocialAccount.platform == platform, SocialAccount.connected.is_(True)).order_by(desc(SocialAccount.id)).limit(1))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "Social account is not connected")
    try:
        token = decrypt_social_token(row.access_token_encrypted)
        await (instagram_profile(token) if platform == SocialPlatform.INSTAGRAM else tiktok_profile(token))
    except SocialAPIError as exc:
        row.last_error = str(exc)[:1000]
        await activity(db, "social_account_probe_failed", "social_account", row.id, {"platform": platform.value, "category": exc.category, "code": exc.code})
        await db.commit()
        raise HTTPException(502, {"message": str(exc), "category": exc.category, "code": exc.code}) from exc
    row.last_success_at = datetime.now(timezone.utc)
    row.last_error = None
    await activity(db, "social_account_probe_succeeded", "social_account", row.id, {"platform": platform.value})
    await db.commit()
    await db.refresh(row)
    return _social_account_read(platform, row)


@router.post("/social/{platform}/disconnect", response_model=SocialAccountRead)
async def social_account_disconnect(platform: SocialPlatform, request: Request, db: Db):
    require_local_operator(request)
    row = (
        await db.execute(select(SocialAccount).where(SocialAccount.platform == platform, SocialAccount.connected.is_(True)).order_by(desc(SocialAccount.id)).limit(1))
    ).scalar_one_or_none()
    if row is None:
        return _social_account_read(platform, None)
    row.connected = False
    row.access_token_encrypted = "revoked"
    row.refresh_token_encrypted = None
    await activity(db, "social_account_disconnected", "social_account", row.id, {"platform": platform.value})
    await db.commit()
    await db.refresh(row)
    return _social_account_read(platform, row)


@router.get("/media/status", response_model=MediaStatusRead)
async def media_status(request: Request):
    require_local_operator(request)
    healthy, health_detail = await comfyui_health()
    image_ready = bool(settings.comfyui_image_workflow)
    video_ready = bool(settings.comfyui_video_workflow)
    video_engine = (
        "comfyui"
        if (not settings.comfyui_mock_mode and video_ready)
        else "local_ffmpeg"
        if settings.local_video_fallback
        else "unavailable"
    )
    video_available = video_engine != "unavailable"
    configured = settings.comfyui_mock_mode or image_ready or video_ready or video_available
    if settings.comfyui_mock_mode:
        component_status = "DEGRADED"
    elif healthy and configured:
        component_status = "ONLINE"
    elif healthy or configured:
        component_status = "DEGRADED"
    else:
        component_status = "OFFLINE"
    return MediaStatusRead(
        mode="mock" if settings.comfyui_mock_mode else "live",
        status=component_status,
        configured=configured,
        image_workflow_configured=image_ready,
        video_workflow_configured=video_ready,
        video_available=video_available,
        video_engine=video_engine,
        supported_video_aspect_ratios=["1:1", "16:9", "9:16"],
        image_checkpoint_configured=bool(settings.image_checkpoint),
        wan_text_encoder_configured=bool(settings.wan_text_encoder),
        detail=(health_detail + ". FFmpeg creates animated text cards, not AI-generated scenes." if video_engine == "local_ffmpeg" else health_detail),
        hardware_note="NVIDIA T1200 4 GB: start with a small image checkpoint at 512 px. Wan 2.2 video is experimental and usually needs substantially more VRAM/RAM; the configured GGUF is only its text encoder.",
    )


@router.get("/media/generations", response_model=list[MediaGenerationRead])
async def media_generations(request: Request, db: Db, limit: int = Query(default=50, ge=1, le=200)):
    require_local_operator(request)
    rows = list((await db.execute(select(MediaGeneration).order_by(desc(MediaGeneration.id)).limit(limit))).scalars())
    return [_media_generation_read(row) for row in rows]


@router.post("/media/generations", response_model=MediaGenerationRead, status_code=status.HTTP_202_ACCEPTED)
async def create_media_generation(payload: MediaGenerationRequest, request: Request, db: Db):
    require_local_operator(request)
    if payload.kind == MediaKind.VIDEO:
        workflow_path = (
            settings.comfyui_video_workflow
            if not settings.comfyui_mock_mode and settings.comfyui_video_workflow
            else "local-ffmpeg"
            if settings.local_video_fallback
            else ""
        )
    else:
        workflow_path = settings.comfyui_image_workflow
    if payload.kind == MediaKind.VIDEO and not workflow_path:
        raise HTTPException(409, "Video generation needs a ComfyUI workflow or LOCAL_VIDEO_FALLBACK=true")
    if payload.kind == MediaKind.IMAGE and not settings.comfyui_mock_mode and not workflow_path:
        raise HTTPException(409, "No image ComfyUI workflow is configured")
    row = MediaGeneration(
        kind=payload.kind,
        status=MediaGenerationStatus.QUEUED,
        prompt=payload.prompt.strip(),
        negative_prompt=payload.negative_prompt.strip() if payload.negative_prompt else None,
        width=payload.width,
        height=payload.height,
        seed=payload.seed,
        workflow_path=workflow_path or "mock",
    )
    db.add(row)
    await db.flush()
    await activity(db, "media_generation_queued", "media_generation", row.id, {"kind": row.kind.value, "width": row.width, "height": row.height})
    await enqueue(db, "generate_media", {"generation_id": row.id}, idempotency_key=f"media-generation:{row.id}:initial")
    await db.refresh(row)
    return _media_generation_read(row)


@router.get("/media/generations/{generation_id}", response_model=MediaGenerationRead)
async def media_generation(generation_id: int, request: Request, db: Db):
    require_local_operator(request)
    row = await db.get(MediaGeneration, generation_id)
    if row is None:
        raise HTTPException(404, "Media generation not found")
    return _media_generation_read(row)


@router.post("/media/generations/{generation_id}/retry", response_model=MediaGenerationRead, status_code=status.HTTP_202_ACCEPTED)
async def retry_media_generation(generation_id: int, request: Request, db: Db):
    require_local_operator(request)
    row = (await db.execute(select(MediaGeneration).where(MediaGeneration.id == generation_id).with_for_update())).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "Media generation not found")
    active_jobs = list(
        (await db.execute(select(BackgroundJob).where(BackgroundJob.kind == "generate_media", BackgroundJob.status.in_([JobStatus.PENDING, JobStatus.RUNNING])))).scalars()
    )
    if any(int(job.payload.get("generation_id", -1)) == row.id for job in active_jobs):
        return _media_generation_read(row)
    if row.status == MediaGenerationStatus.READY:
        raise HTTPException(409, "Ready media does not need a retry")
    if row.kind == MediaKind.VIDEO and (row.workflow_path == "mock" or not row.workflow_path):
        if settings.local_video_fallback:
            row.workflow_path = "local-ffmpeg"
            if (row.width, row.height) not in {(512, 512), (768, 432), (432, 768)}:
                if row.width == row.height:
                    row.width, row.height = 512, 512
                elif row.width > row.height:
                    row.width, row.height = 768, 432
                else:
                    row.width, row.height = 432, 768
        elif not settings.comfyui_mock_mode and settings.comfyui_video_workflow:
            row.workflow_path = settings.comfyui_video_workflow
        else:
            raise HTTPException(409, "Video generation needs a ComfyUI workflow or LOCAL_VIDEO_FALLBACK=true")
    row.status = MediaGenerationStatus.QUEUED
    row.error = None
    row.output_filename = None
    row.mime_type = None
    row.byte_size = None
    row.sha256 = None
    row.duration_ms = None
    await activity(db, "media_generation_requeued", "media_generation", row.id, {"kind": row.kind.value})
    await enqueue(db, "generate_media", {"generation_id": row.id}, idempotency_key=f"media-generation:{row.id}:retry:{uuid4().hex}")
    await db.refresh(row)
    return _media_generation_read(row)


@router.get("/media/assets/{generation_id}")
async def media_asset(generation_id: int, request: Request, db: Db):
    require_local_operator(request)
    row = await db.get(MediaGeneration, generation_id)
    if row is None or row.status != MediaGenerationStatus.READY or not row.output_filename or not row.mime_type:
        raise HTTPException(404, "Ready media asset not found")
    try:
        path = resolve_asset(row.output_filename)
        data = path.read_bytes()
        detected_mime, _ = detect_media(data, row.kind)
    except (FileNotFoundError, OSError, ComfyUIError) as exc:
        raise HTTPException(410, "Stored media asset is missing or invalid") from exc
    if detected_mime != row.mime_type or len(data) != row.byte_size or hashlib.sha256(data).hexdigest() != row.sha256:
        raise HTTPException(410, "Stored media asset failed its integrity check")
    return FileResponse(path, media_type=row.mime_type, filename=f"generated-{row.id}{path.suffix}")


@router.get("/threads/account", response_model=AccountRead)
async def account(db: Db):
    row = (await db.execute(select(ThreadsAccount).where(ThreadsAccount.connected.is_(True)).order_by(desc(ThreadsAccount.id)).limit(1))).scalar_one_or_none()
    if not row:
        return AccountRead(
            connected=False,
            username=None,
            profile_id=None,
            permissions=[],
            token_status="not_configured",
            token_expires_at=None,
            last_sync_at=None,
            last_success_at=None,
            last_error=None,
            mock_mode=settings.threads_mock_mode,
        )
    expiry = row.token_expires_at
    if expiry is not None and expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    expired = bool(expiry and expiry <= datetime.now(timezone.utc))
    token_status = "mock" if row.is_mock else ("EXPIRED" if expired else ("VALID" if row.connected else "configured"))
    affected = 0
    if row.api_status == "BLOCKED":
        affected = int((await db.execute(select(func.count(ThreadsPost.id)).where(ThreadsPost.status == PostStatus.SCHEDULED))).scalar_one())
    return AccountRead(
        connected=row.connected,
        username=row.username,
        profile_id=row.profile_id,
        permissions=row.permissions,
        token_status=token_status,
        token_expires_at=row.token_expires_at,
        last_sync_at=row.last_sync_at,
        last_success_at=row.last_success_at,
        last_error=row.last_error,
        mock_mode=row.is_mock,
        api_status=row.api_status,
        publishing_ready=not expired and threads_publishing_ready(row),
        last_error_code=row.last_error_code,
        last_error_subcode=row.last_error_subcode,
        last_error_trace_id=row.last_error_trace_id,
        blocked_at=row.blocked_at,
        scheduled_posts_affected=affected,
    )


@router.post("/threads/account/connect")
async def connect_account(db: Db, response: Response):
    """Mock: connects the simulated profile. Real: returns the official Meta Authorization Window URL."""
    from .. import api as _api

    if not settings.threads_mock_mode:
        if not settings.threads_app_id or not settings.threads_app_secret:
            raise HTTPException(501, "Set THREADS_APP_ID and THREADS_APP_SECRET in .env first (docs/threads-api-readiness.md), then reconnect.")
        if not settings.threads_redirect_uri:
            raise HTTPException(501, "THREADS_REDIRECT_URI is not configured.")
        state = make_state()
        _bind_threads_state(response, state)
        return {"status": "redirect_required", "authorize_url": authorize_url(settings.threads_redirect_uri, state)}
    profile = await _api.get_threads_client().profile()
    row = (await db.execute(select(ThreadsAccount).where(ThreadsAccount.profile_id == profile["id"]))).scalar_one_or_none()
    if not row:
        row = ThreadsAccount(profile_id=profile["id"], username=profile["username"])
        db.add(row)
    row.permissions = profile["permissions"]
    row.connected = True
    row.is_mock = True
    mark_threads_ready(row)
    await db.commit()
    return await account(db)


@router.post("/threads/account/token", response_model=AccountRead)
async def connect_account_with_token(payload: TokenConnectRequest, db: Db):
    """Validate a Meta-generated Threads token, then store only its encrypted form."""
    from .. import api as _api

    if settings.threads_mock_mode:
        raise HTTPException(409, "Mock mode is active: set THREADS_MOCK_MODE=false before importing a real token.")
    token = payload.access_token.strip()
    client = _api.LiveThreadsClient(token)
    publishing_allowed = False
    publishing_probe_error: ThreadsAPIError | None = None
    try:
        profile = await client.profile()
        try:
            await client.publishing_limit()
            publishing_allowed = True
        except ThreadsAPIError as exc:
            publishing_probe_error = exc
    except ThreadsAPIError as exc:
        raise HTTPException(502, str(exc)) from exc
    finally:
        with contextlib.suppress(Exception):
            await client.aclose()

    profile_id = str(profile.get("id") or "")
    username = str(profile.get("username") or "")
    if not profile_id or not username:
        raise HTTPException(502, "Threads did not return a usable profile.")

    permissions = ["threads_basic"]
    expires_at = None
    debug = await _api.debug_token(token)
    if isinstance(debug, dict):
        data = debug.get("data") if isinstance(debug.get("data"), dict) else debug
        granted = scopes_from_debug(debug)
        if granted:
            permissions = granted
        expires = data.get("expires_at") if isinstance(data, dict) else None
        if isinstance(expires, (int, float)) and expires > 0:
            expires_at = datetime.fromtimestamp(expires, timezone.utc)
    if publishing_allowed and "threads_content_publish" not in permissions:
        permissions.append("threads_content_publish")

    rows = list((await db.execute(select(ThreadsAccount).where(ThreadsAccount.connected.is_(True)))).scalars())
    for existing in rows:
        existing.connected = False

    row = (await db.execute(select(ThreadsAccount).where(ThreadsAccount.profile_id == profile_id))).scalar_one_or_none()
    if row is None:
        row = ThreadsAccount(profile_id=profile_id, username=username)
        db.add(row)
    row.username = username
    row.access_token_encrypted = encrypt_token(token)
    row.permissions = permissions
    row.token_expires_at = expires_at
    row.connected = True
    row.is_mock = False
    if publishing_allowed:
        mark_threads_ready(row)
    elif publishing_probe_error is not None:
        mark_threads_error(row, publishing_probe_error)
    else:
        row.api_status = "UNKNOWN"
    await activity(db, "threads_account_connected", "threads_account", row.id, {"username": username, "method": "token_generator"})
    await db.commit()
    return await account(db)


@router.post("/threads/account/test", response_model=AccountRead)
async def test_account(db: Db):
    from .. import api as _api

    row = (await db.execute(select(ThreadsAccount).where(ThreadsAccount.connected.is_(True)).order_by(desc(ThreadsAccount.id)).limit(1))).scalar_one_or_none()
    token = decrypt_token(row.access_token_encrypted) if row and row.access_token_encrypted and not row.is_mock else None
    client = _api.get_threads_client(row.profile_id if row else None, token)
    try:
        await client.profile()
        publisher_probe = getattr(client, "publishing_limit", None)
        if publisher_probe is not None:
            await publisher_probe()
    except ThreadsAPIError as exc:
        if row:
            mark_threads_error(row, exc)
            await activity(
                db,
                "threads_account_probe_failed",
                "threads_account",
                row.id,
                {
                    "category": exc.category,
                    "code": exc.graph_code or exc.status_code,
                    "subcode": exc.graph_subcode,
                    "trace_id": exc.fbtrace_id,
                },
            )
            await db.commit()
            if exc.category == "app_blocked":
                return await account(db)
        status_code = 409 if exc.category == "app_blocked" else 502
        raise HTTPException(
            status_code,
            {
                "message": str(exc),
                "category": exc.category,
                "code": exc.graph_code or exc.status_code,
                "subcode": exc.graph_subcode,
                "trace_id": exc.fbtrace_id,
            },
        )
    finally:
        closer = getattr(client, "aclose", None)
        if closer is not None:
            with contextlib.suppress(Exception):
                await closer()
    if row:
        mark_threads_ready(row)
        if token and not row.is_mock:
            scopes = await granted_scopes(token)
            if scopes and scopes != list(row.permissions or []):
                previous = list(row.permissions or [])
                row.permissions = scopes
                await activity(
                    db,
                    "threads_account_permissions_changed",
                    "threads_account",
                    row.id,
                    {
                        "granted": scopes,
                        "added": [item for item in scopes if item not in previous],
                        "removed": [item for item in previous if item not in scopes],
                    },
                )
        paused_jobs = list(
            (
                await db.execute(
                    select(BackgroundJob).where(
                        BackgroundJob.kind.in_(["publish_post", "publish_reply", "sync_community", "collect_metrics"]),
                        BackgroundJob.status == JobStatus.PENDING,
                        BackgroundJob.last_error.like("%blocked%"),
                    )
                )
            ).scalars()
        )
        for job in paused_jobs:
            job.run_at = datetime.now(timezone.utc)
            job.last_error = None
        paused_posts = list(
            (
                await db.execute(
                    select(ThreadsPost).where(
                        ThreadsPost.status == PostStatus.SCHEDULED,
                        ThreadsPost.failure_reason.like("Publication paused:%"),
                    )
                )
            ).scalars()
        )
        for post in paused_posts:
            post.failure_reason = None
        await activity(
            db,
            "threads_account_probe_succeeded",
            "threads_account",
            row.id,
            {"resumed_jobs": len(paused_jobs), "resumed_posts": len(paused_posts)},
        )
        await db.commit()
    return await account(db)


@router.post("/threads/account/disconnect", response_model=AccountRead)
async def disconnect_account(db: Db):
    rows = list((await db.execute(select(ThreadsAccount).where(ThreadsAccount.connected.is_(True)))).scalars())
    for row in rows:
        row.connected = False
        row.access_token_encrypted = None
    jobs = list(
        (
            await db.execute(
                select(BackgroundJob)
                .where(
                    BackgroundJob.kind.in_(["publish_post", "sync_community", "collect_metrics"]),
                    BackgroundJob.status.in_(["PENDING", "RUNNING"]),
                )
                .with_for_update()
            )
        ).scalars()
    )
    for job in jobs:
        job.status = "FAILED"
        job.last_error = "Threads account disconnected"
    await activity(db, "threads_account_disconnected", detail={"cancelled_jobs": len(jobs)})
    await db.commit()
    return await account(db)
