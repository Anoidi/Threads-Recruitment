import pytest
from pydantic import ValidationError
from sqlalchemy import select
from fastapi import HTTPException
from starlette.requests import Request

from app.enums import MediaGenerationStatus, MediaKind, SocialPlatform
from app.models import MediaGeneration, SocialAccount
from app.schemas import MediaGenerationRequest, SocialTokenConnectRequest
from app.config import settings
from app.security import require_local_operator, require_manual_token_import


@pytest.mark.asyncio
async def test_social_and_media_models_are_available_in_test_schema(db):
    account = SocialAccount(
        platform=SocialPlatform.INSTAGRAM,
        external_id="ig-123",
        username="recruiter",
        access_token_encrypted="encrypted-token",
        permissions=["instagram_business_basic"],
    )
    generation = MediaGeneration(
        kind=MediaKind.IMAGE,
        prompt="a clean recruitment illustration",
        width=512,
        height=768,
        seed=42,
        workflow_path="/app/workflows/image.json",
    )
    db.add_all([account, generation])
    await db.commit()

    saved_account = (await db.execute(select(SocialAccount))).scalar_one()
    saved_generation = (await db.execute(select(MediaGeneration))).scalar_one()
    assert saved_account.platform == SocialPlatform.INSTAGRAM
    assert saved_generation.status == MediaGenerationStatus.QUEUED
    assert saved_generation.output_filename is None


def test_social_requests_do_not_accept_short_tokens_or_invalid_dimensions():
    with pytest.raises(ValidationError):
        SocialTokenConnectRequest(access_token="too-short")
    with pytest.raises(ValidationError):
        MediaGenerationRequest(kind=MediaKind.IMAGE, prompt="test", width=32)


def test_privileged_operations_are_local_only_by_default(monkeypatch):
    monkeypatch.setattr(settings, "social_local_only", True)
    request = Request({"type": "http", "client": ("203.0.113.9", 443), "headers": []})
    with pytest.raises(HTTPException, match="local-only"):
        require_local_operator(request)

    monkeypatch.setattr(settings, "allow_manual_token_import", False)
    with pytest.raises(HTTPException, match="disabled"):
        require_manual_token_import()
