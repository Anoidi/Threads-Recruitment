from __future__ import annotations

import asyncio
import base64
import hashlib
import ipaddress
import json
import os
import threading
from contextlib import contextmanager
import secrets
import shutil
import subprocess
import tempfile
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import httpx

from .config import settings
from .enums import MediaKind


class ComfyUIError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True)
class StoredMedia:
    filename: str
    mime_type: str
    byte_size: int
    sha256: str
    duration_ms: int | None = None


_MOCK_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
_ALLOWED_DNS_HOSTS = {"localhost", "host.docker.internal", "comfyui"}


def validate_base_url(raw_url: str) -> str:
    parsed = urlparse(raw_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ComfyUIError("COMFYUI_BASE_URL must be an HTTP(S) URL without credentials")
    host = parsed.hostname.lower()
    if host not in _ALLOWED_DNS_HOSTS:
        try:
            address = ipaddress.ip_address(host)
        except ValueError as exc:
            raise ComfyUIError("ComfyUI host is not on the local allowlist") from exc
        allowed = address.is_loopback or address in ipaddress.ip_network("10.0.0.0/8") or address in ipaddress.ip_network("172.16.0.0/12") or address in ipaddress.ip_network("192.168.0.0/16")
        if not allowed:
            raise ComfyUIError("ComfyUI must use a loopback or private network address")
    return raw_url.rstrip("/")


def resolve_workflow_path(configured_path: str) -> Path:
    if not configured_path.strip():
        raise ComfyUIError("No ComfyUI workflow is configured for this media type")
    root = Path(settings.comfyui_workflow_dir).resolve()
    requested = Path(configured_path)
    candidate = (requested if requested.is_absolute() else root / requested).resolve()
    if candidate != root and root not in candidate.parents:
        raise ComfyUIError("Workflow path escapes COMFYUI_WORKFLOW_DIR")
    if not candidate.is_file() or candidate.is_symlink():
        raise ComfyUIError("Configured workflow file does not exist or is a symlink")
    return candidate


def _replace_placeholders(value: Any, replacements: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        return {key: _replace_placeholders(item, replacements) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_placeholders(item, replacements) for item in value]
    if not isinstance(value, str):
        return value
    if value in replacements:
        return replacements[value]
    result = value
    for token, replacement in replacements.items():
        result = result.replace(token, str(replacement))
    return result


def load_workflow(
    configured_path: str,
    *,
    prompt: str,
    negative_prompt: str | None,
    width: int,
    height: int,
    seed: int,
    output_prefix: str,
) -> dict[str, Any]:
    path = resolve_workflow_path(configured_path)
    if path.stat().st_size > 2_000_000:
        raise ComfyUIError("Workflow JSON exceeds the 2 MB limit")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ComfyUIError("Workflow is not valid UTF-8 JSON") from exc
    workflow = raw.get("prompt") if isinstance(raw, dict) and isinstance(raw.get("prompt"), dict) else raw
    if not isinstance(workflow, dict) or not workflow:
        raise ComfyUIError("Workflow must be ComfyUI API-format JSON")
    for node_id, node in workflow.items():
        if not isinstance(node_id, str) or not isinstance(node, dict) or not isinstance(node.get("class_type"), str) or not isinstance(node.get("inputs", {}), dict):
            raise ComfyUIError("Workflow contains a node that is not in API format")
    actual_seed = secrets.randbelow(2_147_483_647) if seed < 0 else seed
    return _replace_placeholders(workflow, {
        "{{PROMPT}}": prompt,
        "{{NEGATIVE_PROMPT}}": negative_prompt or "",
        "{{WIDTH}}": width,
        "{{HEIGHT}}": height,
        "{{SEED}}": actual_seed,
        "{{OUTPUT_PREFIX}}": output_prefix,
        "{{IMAGE_CHECKPOINT}}": settings.image_checkpoint,
        "{{WAN_TEXT_ENCODER}}": settings.wan_text_encoder,
    })


def _validate_mp4_boxes(data: bytes) -> None:
    boxes: set[bytes] = set()
    offset = 0
    while offset + 8 <= len(data):
        size = int.from_bytes(data[offset:offset + 4], "big")
        box_type = data[offset + 4:offset + 8]
        header_size = 8
        if size == 1:
            if offset + 16 > len(data):
                raise ComfyUIError("MP4 contains a truncated extended box")
            size = int.from_bytes(data[offset + 8:offset + 16], "big")
            header_size = 16
        elif size == 0:
            size = len(data) - offset
        if size < header_size or offset + size > len(data):
            raise ComfyUIError("MP4 contains a truncated or invalid box")
        boxes.add(box_type)
        offset += size
    if offset != len(data) or not {b"ftyp", b"moov", b"mdat"}.issubset(boxes):
        raise ComfyUIError("MP4 must contain complete ftyp, moov and mdat boxes")


def detect_media(data: bytes, kind: MediaKind) -> tuple[str, str]:
    detected: tuple[str, str] | None = None
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        detected = ("image/png", ".png")
    elif data.startswith(b"\xff\xd8\xff"):
        detected = ("image/jpeg", ".jpg")
    elif len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        detected = ("image/webp", ".webp")
    elif len(data) >= 12 and data[4:8] == b"ftyp":
        _validate_mp4_boxes(data)
        detected = ("video/mp4", ".mp4")
    elif data.startswith(b"\x1aE\xdf\xa3"):
        detected = ("video/webm", ".webm")
    if detected is None:
        raise ComfyUIError("ComfyUI output has an unsupported or corrupt file signature")
    if kind == MediaKind.IMAGE and not detected[0].startswith("image/"):
        raise ComfyUIError("Image generation returned a non-image file")
    if kind == MediaKind.VIDEO and not detected[0].startswith("video/"):
        raise ComfyUIError("Video generation returned a non-video file")
    return detected


def _probe_video_duration_ms(path: Path) -> int:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise ComfyUIError("ffprobe is required to validate generated video")
    try:
        completed = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
        duration_ms = int(float(completed.stdout.strip()) * 1000)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise ComfyUIError("Generated MP4 cannot be decoded by ffprobe") from exc
    if duration_ms < 500:
        raise ComfyUIError("Generated video is shorter than 0.5 seconds")
    return duration_ms


_MEDIA_THREAD_LOCK = threading.Lock()


@contextmanager
def _media_write_lock(output_dir: Path):
    """Serialize quota-check plus write across threads and Docker processes."""
    with _MEDIA_THREAD_LOCK, (output_dir / '.media.lock').open('a+b') as lock:
        if os.name == 'nt':
            import msvcrt
            if lock.tell() == 0:
                lock.write(b'0')
                lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == 'nt':
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def store_media(data: bytes, kind: MediaKind, generation_id: int) -> StoredMedia:
    output_dir = Path(settings.media_output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    with _media_write_lock(output_dir):
        return _store_media_locked(data, kind, generation_id)


def _store_media_locked(data: bytes, kind: MediaKind, generation_id: int) -> StoredMedia:
    if not data or len(data) > settings.media_max_bytes:
        raise ComfyUIError("Generated media is empty or exceeds MEDIA_MAX_BYTES")
    mime_type, suffix = detect_media(data, kind)
    output_dir = Path(settings.media_output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    used = sum(path.stat().st_size for path in output_dir.iterdir() if path.name != '.media.lock' and path.is_file() and not path.is_symlink())
    if used + len(data) > settings.media_disk_quota_bytes:
        raise ComfyUIError("Local media disk quota is exhausted")
    filename = f"asset-{generation_id}-{uuid4().hex}{suffix}"
    destination = (output_dir / filename).resolve()
    if output_dir not in destination.parents:
        raise ComfyUIError("Invalid output path")
    try:
        destination.write_bytes(data)
        duration_ms = _probe_video_duration_ms(destination) if kind == MediaKind.VIDEO else None
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return StoredMedia(filename, mime_type, len(data), hashlib.sha256(data).hexdigest(), duration_ms)


async def _render_local_motion_video(prompt: str, width: int, height: int) -> bytes:
    if (width, height) not in {(512, 512), (768, 432), (432, 768)}:
        raise ComfyUIError("Local video requires one of the supported aspect presets")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise ComfyUIError("FFmpeg is not installed in the media worker")
    duration = max(2, min(settings.local_video_duration_seconds, 15))
    font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    if not Path(font_path).is_file():
        raise ComfyUIError("The local video font is not installed")
    wrap_width = 34 if width <= height else 54
    title = "\n".join(textwrap.wrap(prompt.strip()[:280], width=wrap_width)) or "Generated video"
    font_size = max(20, min(42, width // 15))
    with tempfile.TemporaryDirectory(prefix="social-video-") as temp_dir:
        temp = Path(temp_dir)
        text_path = temp / "prompt.txt"
        output_path = temp / "output.mp4"
        text_path.write_text(title, encoding="utf-8")
        filters = (
            f"drawbox=x='mod(t*140\\,iw+240)-240':y='ih*0.08':w=240:h='ih*0.84':color=0x5f8f73@0.55:t=fill,"
            f"drawbox=x='iw-mod(t*95\\,iw+180)':y='ih*0.24':w=180:h='ih*0.52':color=0xcd7654@0.42:t=fill,"
            f"drawtext=fontfile={font_path}:textfile={text_path}:fontcolor=white:fontsize={font_size}:"
            f"line_spacing=10:x=(w-text_w)/2:y=(h-text_h)/2:box=1:boxcolor=0x101713@0.72:boxborderw=24"
        )
        process = await asyncio.create_subprocess_exec(
            ffmpeg,
            "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"color=c=0x18211b:s={width}x{height}:r=24:d={duration}",
            "-vf", filters,
            "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(output_path),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(process.communicate(), timeout=90)
        except TimeoutError as exc:
            process.kill()
            await process.communicate()
            raise ComfyUIError("Local FFmpeg video generation timed out", retryable=True) from exc
        if process.returncode != 0 or not output_path.is_file():
            detail = stderr.decode("utf-8", errors="replace")[-400:] if stderr else "unknown error"
            raise ComfyUIError(f"Local FFmpeg video generation failed: {detail}")
        return output_path.read_bytes()


def resolve_asset(filename: str) -> Path:
    output_dir = Path(settings.media_output_dir).resolve()
    candidate = (output_dir / filename).resolve()
    if output_dir not in candidate.parents or not candidate.is_file() or candidate.is_symlink():
        raise FileNotFoundError(filename)
    return candidate


def _extract_output(history: dict[str, Any], prompt_id: str) -> dict[str, str] | None:
    entry = history.get(prompt_id, history)
    outputs = entry.get("outputs", {}) if isinstance(entry, dict) else {}
    for node in outputs.values() if isinstance(outputs, dict) else []:
        if not isinstance(node, dict):
            continue
        for key in ("images", "videos", "gifs"):
            items = node.get(key)
            if isinstance(items, list) and items and isinstance(items[0], dict) and items[0].get("filename"):
                return {
                    "filename": str(items[0]["filename"]),
                    "subfolder": str(items[0].get("subfolder", "")),
                    "type": str(items[0].get("type", "output")),
                }
    status = entry.get("status", {}) if isinstance(entry, dict) else {}
    if isinstance(status, dict) and status.get("completed") and not outputs:
        raise ComfyUIError("ComfyUI completed without a media output")
    return None


async def comfyui_health() -> tuple[bool, str]:
    if settings.comfyui_mock_mode:
        return True, "Mock media mode is active; no model is invoked"
    base_url = validate_base_url(settings.comfyui_base_url)
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(f"{base_url}/system_stats")
            response.raise_for_status()
        return True, "ComfyUI is reachable"
    except (httpx.HTTPError, ComfyUIError):
        return False, "ComfyUI is not reachable"


async def generate_media(
    generation_id: int,
    kind: MediaKind,
    *,
    prompt: str,
    negative_prompt: str | None,
    width: int,
    height: int,
    seed: int,
    workflow_path: str,
) -> tuple[str | None, StoredMedia]:
    if kind == MediaKind.VIDEO and workflow_path == "local-ffmpeg":
        if not settings.local_video_fallback:
            raise ComfyUIError("Local video fallback is disabled")
        data = await _render_local_motion_video(prompt, width, height)
        return f"local-ffmpeg-{generation_id}", await asyncio.to_thread(store_media, data, kind, generation_id)
    if settings.comfyui_mock_mode:
        if kind == MediaKind.VIDEO:
            raise ComfyUIError("Mock video is unavailable without the local FFmpeg fallback")
        return f"mock-{generation_id}", await asyncio.to_thread(store_media, _MOCK_PNG, kind, generation_id)

    base_url = validate_base_url(settings.comfyui_base_url)
    workflow = load_workflow(
        workflow_path,
        prompt=prompt,
        negative_prompt=negative_prompt,
        width=width,
        height=height,
        seed=seed,
        output_prefix=f"social-{generation_id}",
    )
    timeout = max(30, min(settings.comfyui_timeout_seconds, 7200))
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=60.0)) as client:
            submitted = await client.post(f"{base_url}/prompt", json={"prompt": workflow, "client_id": f"social-{uuid4().hex}"})
            submitted.raise_for_status()
            prompt_id = str(submitted.json().get("prompt_id", ""))
            if not prompt_id:
                raise ComfyUIError("ComfyUI did not return a prompt id")
            deadline = asyncio.get_running_loop().time() + timeout
            output: dict[str, str] | None = None
            while asyncio.get_running_loop().time() < deadline:
                history_response = await client.get(f"{base_url}/history/{prompt_id}")
                history_response.raise_for_status()
                output = _extract_output(history_response.json(), prompt_id)
                if output:
                    break
                await asyncio.sleep(2)
            if output is None:
                raise ComfyUIError("ComfyUI generation timed out", retryable=True)
            media_response = await client.get(f"{base_url}/view", params=output)
            media_response.raise_for_status()
            data = media_response.content
    except httpx.HTTPError as exc:
        raise ComfyUIError("ComfyUI request failed", retryable=True) from exc
    return prompt_id, await asyncio.to_thread(store_media, data, kind, generation_id)
