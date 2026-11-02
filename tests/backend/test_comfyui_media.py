import json
import os
import sqlite3
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from sqlalchemy import select

from app.comfyui import ComfyUIError, detect_media, load_workflow, resolve_workflow_path
from app.config import settings
from app.enums import MediaGenerationStatus, MediaKind
from app.models import BackgroundJob, MediaGeneration
from app.schemas import MediaGenerationRequest
from worker.main import process_media_generation


def test_media_lock_blocks_an_independent_writer_until_release(tmp_path):
    from app import comfyui
    script = (
        "import sys; from app import comfyui; from app.config import settings; "
        "from app.enums import MediaKind; settings.media_output_dir=sys.argv[1]; "
        "print('READY', flush=True); "
        "comfyui.store_media(comfyui._MOCK_PNG, MediaKind.IMAGE, 99); "
        "print('SAVED', flush=True)"
    )
    env = {**os.environ, 'PYTHONPATH': os.pathsep.join(sys.path)}
    process = None
    try:
        with comfyui._media_write_lock(tmp_path):
            process = subprocess.Popen([sys.executable, '-c', script, str(tmp_path)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
            with pytest.raises(subprocess.TimeoutExpired) as pending:
                process.communicate(timeout=2)
            assert b'READY' in (pending.value.output or b'')
            assert list(tmp_path.glob('asset-*')) == []
        output, error = process.communicate(timeout=10)
        assert process.returncode == 0, error.decode()
        assert b'SAVED' in output
        assert len(list(tmp_path.glob('asset-*'))) == 1
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.communicate(timeout=5)


def test_parallel_media_writes_cannot_exceed_disk_quota(tmp_path, monkeypatch):
    from app import comfyui
    monkeypatch.setattr(settings, 'media_output_dir', str(tmp_path))
    monkeypatch.setattr(settings, 'media_disk_quota_bytes', len(comfyui._MOCK_PNG))
    def save(identifier):
        try:
            return comfyui.store_media(comfyui._MOCK_PNG, MediaKind.IMAGE, identifier)
        except ComfyUIError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, [1, 2]))
    assert sum(result is not None for result in results) == 1
    assert sum(path.stat().st_size for path in tmp_path.glob('asset-*')) == len(comfyui._MOCK_PNG)


def test_failed_write_removes_partial_asset(tmp_path, monkeypatch):
    from app import comfyui
    monkeypatch.setattr(settings, 'media_output_dir', str(tmp_path))
    original_write = Path.write_bytes
    def fail_after_partial_write(path, data):
        original_write(path, data[:8])
        raise OSError('disk full')
    monkeypatch.setattr(Path, 'write_bytes', fail_after_partial_write)
    with pytest.raises(OSError):
        comfyui.store_media(comfyui._MOCK_PNG, MediaKind.IMAGE, 1)
    assert list(tmp_path.glob('asset-*')) == []


@pytest.mark.asyncio
async def test_media_storage_runs_outside_event_loop(monkeypatch):
    from app import comfyui
    monkeypatch.setattr(settings, 'comfyui_mock_mode', True)
    main_thread = threading.get_ident()
    observed = []
    def store(*args):
        observed.append(threading.get_ident())
        return 'stored'
    monkeypatch.setattr(comfyui, 'store_media', store)
    result = await comfyui.generate_media(1, MediaKind.IMAGE, prompt='test',
        negative_prompt=None, width=512, height=512, seed=1, workflow_path='')
    assert result == ('mock-1', 'stored')
    assert observed and observed[0] != main_thread


@pytest.mark.asyncio
async def test_mock_media_status_never_claims_real_model_generation(client, monkeypatch):
    monkeypatch.setattr(settings, 'comfyui_mock_mode', True)
    monkeypatch.setattr(settings, 'local_video_fallback', True)
    result = await client.get('/api/media/status')
    assert result.status_code == 200
    data = result.json()
    assert data['mode'] == 'mock'
    assert data['status'] == 'DEGRADED'
    assert 'not AI-generated scenes' in data['detail']
    assert data['supported_video_aspect_ratios'] == ['1:1', '16:9', '9:16']


def test_workflow_placeholders_and_path_guard(tmp_path, monkeypatch):
    workflow = {
        "1": {"class_type": "CLIPTextEncode", "inputs": {"text": "{{PROMPT}}"}},
        "2": {"class_type": "EmptyLatentImage", "inputs": {"width": "{{WIDTH}}", "height": "{{HEIGHT}}"}},
    }
    (tmp_path / "image.json").write_text(json.dumps(workflow), encoding="utf-8")
    monkeypatch.setattr(settings, "comfyui_workflow_dir", str(tmp_path))
    rendered = load_workflow("image.json", prompt="safe prompt", negative_prompt=None, width=512, height=768, seed=7, output_prefix="test")
    assert rendered["1"]["inputs"]["text"] == "safe prompt"
    assert rendered["2"]["inputs"]["width"] == 512
    with pytest.raises(ComfyUIError, match="escapes"):
        resolve_workflow_path("../outside.json")


def test_media_magic_must_match_requested_kind():
    assert detect_media(b"\x89PNG\r\n\x1a\ncontent", "IMAGE")[0] == "image/png"
    with pytest.raises(ComfyUIError, match="non-video"):
        detect_media(b"\x89PNG\r\n\x1a\ncontent", "VIDEO")


def _box(name: bytes, payload: bytes) -> bytes:
    return (8 + len(payload)).to_bytes(4, "big") + name + payload


def test_mp4_requires_complete_ftyp_moov_and_mdat_boxes():
    complete = _box(b"ftyp", b"isom\x00\x00\x00\x00isom") + _box(b"moov", b"metadata") + _box(b"mdat", b"frame")
    assert detect_media(complete, MediaKind.VIDEO) == ("video/mp4", ".mp4")

    for corrupt in (
        _box(b"ftyp", b"isom\x00\x00\x00\x00isom") + _box(b"mdat", b"frame"),
        _box(b"ftyp", b"isom\x00\x00\x00\x00isom") + _box(b"moov", b"metadata") + b"\x00\x00\x00\x20mdatshort",
        b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom\x00\x00\x00\x08mdat",
    ):
        with pytest.raises(ComfyUIError, match="MP4"):
            detect_media(corrupt, MediaKind.VIDEO)


@pytest.mark.parametrize("width,height", [(512, 512), (768, 432), (432, 768)])
def test_video_request_accepts_only_supported_presets(width, height):
    request = MediaGenerationRequest(kind="VIDEO", prompt="motion card", width=width, height=height)
    assert (request.width, request.height) == (width, height)


def test_video_request_rejects_arbitrary_dimensions():
    with pytest.raises(ValueError, match="video preset"):
        MediaGenerationRequest(kind="VIDEO", prompt="motion card", width=640, height=480)


@pytest.mark.asyncio
async def test_mock_generation_is_durable_and_opaque(client, db, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "comfyui_mock_mode", True)
    monkeypatch.setattr(settings, "media_output_dir", str(tmp_path))
    response = await client.post("/api/media/generations", json={
        "kind": "IMAGE", "prompt": "Recruitment poster", "width": 512, "height": 512, "seed": 42,
    })
    assert response.status_code == 202
    payload = response.json()
    assert payload["status"] == "QUEUED"
    assert "output_filename" not in payload

    job = (await db.execute(select(BackgroundJob).where(BackgroundJob.kind == "generate_media"))).scalar_one()
    await process_media_generation(db, int(job.payload["generation_id"]))

    generation = await db.get(MediaGeneration, payload["id"])
    await db.refresh(generation)
    assert generation.status == MediaGenerationStatus.READY
    assert generation.sha256 and len(generation.sha256) == 64

    detail = await client.get(f"/api/media/generations/{generation.id}")
    assert detail.status_code == 200
    assert detail.json()["asset_url"] == f"/api/media/assets/{generation.id}"
    asset = await client.get(detail.json()["asset_url"])
    assert asset.status_code == 200
    assert asset.headers["content-type"].startswith("image/png")


@pytest.mark.asyncio
async def test_live_generation_requires_configured_workflow(client, monkeypatch):
    monkeypatch.setattr(settings, "comfyui_mock_mode", False)
    monkeypatch.setattr(settings, "comfyui_image_workflow", "")
    response = await client.post("/api/media/generations", json={"kind": "IMAGE", "prompt": "test"})
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_mock_video_is_rejected_without_real_fallback(client, db, monkeypatch):
    monkeypatch.setattr(settings, "comfyui_mock_mode", True)
    monkeypatch.setattr(settings, "local_video_fallback", False)
    monkeypatch.setattr(settings, "comfyui_video_workflow", "")

    response = await client.post("/api/media/generations", json={
        "kind": "VIDEO", "prompt": "Recruitment motion card", "width": 432, "height": 768,
    })

    assert response.status_code == 409
    assert "video" in response.json()["detail"].lower()
    assert (await db.execute(select(MediaGeneration))).scalars().all() == []
    assert (await db.execute(select(BackgroundJob))).scalars().all() == []


@pytest.mark.asyncio
async def test_legacy_video_retry_maps_dimensions_to_supported_preset(client, db, monkeypatch):
    monkeypatch.setattr(settings, "comfyui_mock_mode", True)
    monkeypatch.setattr(settings, "local_video_fallback", True)
    generation = MediaGeneration(
        kind=MediaKind.VIDEO,
        status=MediaGenerationStatus.FAILED,
        prompt="legacy portrait",
        width=1024,
        height=1984,
        seed=-1,
        workflow_path="mock",
        error="invalid placeholder",
    )
    db.add(generation)
    await db.commit()
    await db.refresh(generation)

    response = await client.post(f"/api/media/generations/{generation.id}/retry")

    assert response.status_code == 202
    assert (response.json()["width"], response.json()["height"]) == (432, 768)
    await db.refresh(generation)
    assert generation.workflow_path == "local-ffmpeg"


@pytest.mark.asyncio
@pytest.mark.parametrize("width,height", [(512, 512), (768, 432), (432, 768)])
async def test_local_video_fallback_produces_valid_mp4(client, db, tmp_path, monkeypatch, width, height):
    monkeypatch.setattr(settings, "comfyui_mock_mode", True)
    monkeypatch.setattr(settings, "local_video_fallback", True)
    monkeypatch.setattr(settings, "local_video_duration_seconds", 2)
    monkeypatch.setattr(settings, "media_output_dir", str(tmp_path))
    response = await client.post("/api/media/generations", json={
        "kind": "VIDEO", "prompt": f"Recruitment video {width}x{height}", "width": width, "height": height, "seed": 5,
    })
    assert response.status_code == 202
    generation_id = response.json()["id"]
    job = (await db.execute(select(BackgroundJob).where(BackgroundJob.kind == "generate_media"))).scalar_one()
    await process_media_generation(db, int(job.payload["generation_id"]))
    generation = await db.get(MediaGeneration, generation_id)
    await db.refresh(generation)
    assert generation.status == MediaGenerationStatus.READY
    assert generation.byte_size and generation.byte_size > 1_000
    assert generation.duration_ms and generation.duration_ms >= 1_500
    asset = await client.get(f"/api/media/assets/{generation_id}")
    assert asset.status_code == 200
    assert asset.headers["content-type"].startswith("video/mp4")


def test_migration_0008_invalidates_legacy_mock_ready_video(tmp_path):
    root = Path(__file__).resolve().parents[2]
    database = tmp_path / "media-migration.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite+aiosqlite:///{database.as_posix()}"
    base = [sys.executable, "-m", "alembic", "-c", "alembic.ini"]
    subprocess.run([*base, "upgrade", "0007_media_asset_metadata"], cwd=root / "backend", env=env, check=True, capture_output=True, text=True)
    with sqlite3.connect(database) as connection:
        connection.execute(
            """INSERT INTO media_generations
            (kind,status,prompt,width,height,seed,workflow_path,comfy_prompt_id,output_filename,mime_type,byte_size,sha256,duration_ms,created_at,updated_at)
            VALUES ('VIDEO','READY','legacy',432,768,-1,'mock','mock-2','asset-2.mp4','video/mp4',32,'bad',NULL,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"""
        )
        connection.commit()
    subprocess.run([*base, "upgrade", "head"], cwd=root / "backend", env=env, check=True, capture_output=True, text=True)
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT status,error,comfy_prompt_id,output_filename,mime_type,byte_size,sha256,duration_ms FROM media_generations"
        ).fetchone()
    assert row[0] == "FAILED"
    assert "mock" in row[1].lower()
    assert row[2:] == (None, None, None, None, None, None)
