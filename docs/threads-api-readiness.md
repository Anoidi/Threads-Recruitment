# Threads API readiness checklist (Этап F)

Status: the adapter runs in mock mode (`THREADS_MOCK_MODE=true`). The local
`auto_post` policy is enabled, but code refuses to deliver posts unless mock
mode is off and a non-mock account is connected. No live publication has been
verified against Meta.

## 1. Meta app setup (manual, outside this repo)

1. Create an app at https://developers.facebook.com and add the **Threads API**
   product.
2. Deploy the callback at a secure public HTTPS address, set that exact address
   in `THREADS_REDIRECT_URI`, and register the exact same URL in the Meta app.
   The local HTTP default is rejected by Meta. Do not use a public tunnel yet:
   this API is currently intended for loopback/local use and lacks user auth.
3. Generate `THREADS_APP_ID` and `THREADS_APP_SECRET` and put them in `.env`.
4. Request the permissions the adapter uses:
   - `threads_basic` (identity + publishing prerequisites),
   - `threads_content_publish` (create posts),
   - `threads_manage_replies` / `threads_manage_insights` for community
     sync and metrics.
   - `threads_manage_mentions` and `threads_keyword_search` for mentions and
     topic discovery.
   Meta subjects these to app review; sandbox mode limits the token to
   Instagram test users.
5. Complete the server-side OAuth flow from the **Threads Account** page in
   this application. Prefer the short-lived token exchange to a long-lived
   token. `THREADS_ACCESS_TOKEN` exists for controlled development only.

## 2. Guardrails before leaving mock mode

- Keep `AUTO_REPLY=false` and `AUTO_EXTERNAL_COMMENT=false`. Auto-post is
  enabled in the local settings but hard-gated by mock mode and live account
  connection; before changing either gate, validate the copy and publishing
  caps on a secured live deployment.
- Do not mix mock and live data: back up MySQL (`scripts/backup-db.ps1`) and
  either start a fresh database or deliberately clear test posts first.
- Confirm hourly caps (`MAX_POSTS_PER_HOUR`, `MAX_REPLIES_PER_HOUR`) are still
  conservative, and check the Threads publishing quota through
  `GET /api/threads/account/test` (`threads_publishing_limit`). The quota guard
  is enforced by the backend and refuses to publish once the window is
  exhausted.
- Never expose any of these values through `VITE_*` variables.

## 3. Switch procedure

```powershell
# 1. credentials present in .env
# 2. THREADS_MOCK_MODE=false
docker compose up -d --force-recreate backend worker
# 3. connect the account from the UI (Threads Account page)
# 4. check GET /api/threads/account shows connected + permissions
# 5. generate -> review -> approve ONE post, publish manually, verify it on
#    the real profile before raising any limits
```

Rollback: set `THREADS_MOCK_MODE=true` again and recreate backend/worker.

## 4. Current adapter coverage

- Publish: `POST /{threads-user-id}/threads` (text) with an idempotency key,
  rate-limit guard, and per-post failure reasons.
- Replies/mentions sync and metrics use only documented Graph endpoints.
- Missing permissions degrade account features; vacancy/content workflows
  keep working.

## 5. Verification steps that must pass before going live

- `GET /api/health` shows `threads: ONLINE` (not `DEGRADED mock adapter`).
- `POST /api/threads/account/test` returns a profile with the expected
  permissions.
- One manually approved test post publishes and appears on the real profile.
- A forced failure (revoke the token) marks the post `FAILED` with a readable
  `failure_reason` in the UI instead of silently retrying.
