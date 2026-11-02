"""Read-only live probes. Run inside backend; output contains no tokens/content."""
import asyncio
import argparse
import json
import logging

logging.disable(logging.CRITICAL)

import httpx
from sqlalchemy import select
from app.config import settings
from app.database import SessionLocal, engine
from app.models import ThreadsAccount
from app.threads_client import decrypt_token


async def main(sync_metadata=False):
    operator = getattr(settings, "operator_token", "")
    out = {"config": {
        "threads_app_id": settings.threads_app_id,
        "app_id_matches_expected": settings.threads_app_id == "1649309346853697",
        "app_secret_present": bool(settings.threads_app_secret),
        "env_token_present": bool(settings.threads_access_token),
        "dedicated_key_present": bool(settings.threads_token_key),
        "operator_token_present": bool(operator),
        "redirect_uri": settings.threads_redirect_uri,
        "mock": settings.threads_mock_mode,
    }, "local": [], "accounts": []}
    async with httpx.AsyncClient(timeout=20) as http:
        for path in ["/api/health/live", "/api/health/ready", "/api/threads/account", "/api/threads/capabilities"]:
            r = await http.get("http://127.0.0.1:8000" + path, headers={"X-Operator-Token": operator})
            out["local"].append({"path": path, "status": r.status_code})
        async with SessionLocal() as db:
            rows = list((await db.execute(select(ThreadsAccount))).scalars())
            for account in rows:
                item = {"username": account.username, "connected": account.connected,
                        "mock": account.is_mock, "stored_scopes": account.permissions,
                        "expires_at": str(account.token_expires_at), "tests": []}
                out["accounts"].append(item)
                if account.is_mock or not account.access_token_encrypted:
                    item["skip"] = "No stored live token"
                    continue
                try:
                    token = decrypt_token(account.access_token_encrypted)
                except Exception:
                    item["blocked"] = "decrypt_failed"
                    continue

                async def probe(path, params=None):
                    try:
                        response = await http.get("https://graph.threads.net" + path,
                                                  params=params, headers={"Authorization": "Bearer " + token})
                    except httpx.HTTPError:
                        item["tests"].append({"endpoint": path, "http": None, "result": "FAIL", "reason": "transport_error"})
                        return {}
                    try:
                        payload = response.json()
                    except ValueError:
                        payload = {}
                    if not isinstance(payload, dict):
                        payload = {}
                    error = payload.get("error") or {}
                    item["tests"].append({"endpoint": path, "http": response.status_code,
                                          "result": "FAIL" if response.status_code >= 400 or error else "PASS",
                                          "code": error.get("code"), "subcode": error.get("error_subcode")})
                    return payload

                profile = await probe("/v1.0/me", {"fields": "id,username,name"})
                if "id" not in profile:
                    continue
                item["verified_username"] = profile.get("username")
                item["profile_matches_stored"] = str(profile["id"]) == account.profile_id
                debug = await probe("/debug_token", {"input_token": token, "app_id": settings.threads_app_id})
                data = debug.get("data", {})
                item["debug"] = {key: data[key] for key in ["is_valid", "app_id", "scopes", "granted_scopes", "expires_at"] if key in data}
                # Optional local repair only; no publishing jobs are resumed.
                if sync_metadata and account.connected and item["profile_matches_stored"] and data.get("is_valid") is True:
                    from datetime import datetime, timezone
                    scopes = data.get("scopes", data.get("granted_scopes"))
                    if isinstance(scopes, list) and all(isinstance(s, str) for s in scopes):
                        account.permissions = scopes
                    expiry = data.get("expires_at")
                    if isinstance(expiry, (int, float)) and expiry > 0:
                        account.token_expires_at = datetime.fromtimestamp(expiry, timezone.utc)
                    await db.commit()
                    item["metadata_synchronized"] = True
                posts = await probe("/v1.0/me/threads", {"fields": "id", "limit": 1})
                if posts.get("data"):
                    post_id = posts["data"][0]["id"]
                    await probe("/v1.0/" + post_id + "/insights", {"metric": "views,likes,replies,reposts,quotes"})
                    await probe("/v1.0/" + post_id + "/replies", {"fields": "id", "limit": 1})
                else:
                    item["skip_post_tests"] = "No accessible own post"
                await probe("/v1.0/me/threads_publishing_limit", {"fields": "quota_usage,config"})
    await engine.dispose()
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--sync-metadata", action="store_true", help="Repair local scopes/expiry from verified GET responses")
    asyncio.run(main(parser.parse_args().sync_metadata))
