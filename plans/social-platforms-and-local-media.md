# Threads Recruitment Manager: Instagram, TikTok and local media

## Objective

Extend the existing Threads-first recruitment dashboard with official Instagram and TikTok account connections, cross-platform scheduling, and a local-first media pipeline that can use ComfyUI workflows for images and video. Keep the default deployment free to run locally, review-first, and safe when credentials or public media hosting are missing.

## Progress

- Plan reviewed adversarially and corrected for local security, applied migration lineage, provider constraints, per-destination delivery semantics and ComfyUI safety.
- Step 0/1 foundation complete: provider/media enums, ORM contract, request/response schemas, local-only guard and contract tests are present.
- Steps 2/3 connection foundation complete: Instagram and TikTok OAuth/profile/test/disconnect endpoints, encrypted token storage and Accounts UI are implemented. Live provider authorization still requires operator-owned app credentials and platform review.
- Step 4 local media foundation complete on 2026-10-08: ComfyUI status, guarded API-format workflows, durable generation jobs, worker processing, output signature/size/hash/quota validation, opaque local asset URLs, mock mode and the Media UI are implemented. A running-container smoke test completed generation `1` as a validated PNG. Live ComfyUI/model execution remains intentionally unconfigured.
- Database audit on 2026-10-08 found the running MySQL database already at Alembic revision `0006_threads_api_status`; `social_accounts` and `media_generations` exist. Future schema changes must therefore use new forward migrations.
- Forward migration `0007_media_asset_metadata` was applied after creating the verified backup `backups/social-media-pre-0007-verified-20261008.sql`; the database now stores MIME, byte size, SHA-256 and optional duration metadata.
- Verification on 2026-10-08: backend `84 passed`, frontend `10 passed`, frontend production build succeeds, Compose configuration validates, and backend/worker/frontend are healthy.
- Video completion fix applied on 2026-10-08: invalid 32-byte mock MP4 files are rejected and legacy placeholders are invalidated by forward migration `0008_invalidate_mock_video`; the free local FFmpeg fallback now produces validated 5-second H.264/yuv420p motion-card videos in `1:1` (512×512), `16:9` (768×432), and `9:16` (432×768). Generation records `2`, `3`, and `4` were verified end-to-end with `ffprobe`. Final verification: backend `96 passed`, frontend `11 passed`, frontend production build succeeds, and backend/worker/frontend are healthy. Backup: `backups/social-media-pre-0008-verified-20261008.sql`.

## Repository context

- Backend: FastAPI + SQLAlchemy async + Alembic in `backend/`.
- Worker: durable database/Redis jobs in `worker/main.py`.
- Frontend: React/Vite/Tailwind in `frontend/src/`.
- Existing Threads OAuth/token encryption and post approval state machine must remain compatible.
- The worktree is intentionally dirty with existing user changes. Every step must preserve unrelated changes and avoid reset/checkout operations.
- A working-tree migration draft `backend/alembic/versions/0005_social_and_media.py` already exists and is referenced by `0006_threads_api_status.py`; treat it as existing project work and reconcile it with the ORM before running migrations.

## Product invariants

1. No external publish action happens without an explicit approved/scheduled post and the existing safety limits.
2. Provider/API errors are persisted per provider and never treated as instructions.
3. OAuth access and refresh tokens are encrypted at rest and never returned to the frontend.
4. Mock mode remains usable without Meta, TikTok, ComfyUI, or model downloads.
5. Platform-specific rules are surfaced before scheduling: Instagram needs a professional account and accessible media; TikTok unaudited direct-post clients are private-only.
6. A media model name or workflow is configuration, not an implicit download. Model licenses and hardware requirements are visible to the operator.

## Dependency graph

```text
Step 0 (local-only operator boundary)
  └── Step 1 (domain/schema + migration audit)
        ├── Step 2 (Instagram provider)
        ├── Step 3 (TikTok provider)
        └── Step 4 (ComfyUI/media jobs)
Step 2 + Step 3 + Step 4
  └── Step 5a (destination/delivery fan-out)
        ├── Step 5b (Instagram delivery)
        ├── Step 5c (TikTok delivery)
        └── Step 5d (partial-success reconciliation)
Step 0..5d
  └── Step 6 (frontend, docs, integration verification)
```

Steps 2–4 can be developed independently after Step 1. They must not be merged into the shared worker or UI contract until Step 1's schema and error semantics are stable.

## Step 0 — Operator boundary and deployment guard

### Context brief

The current repository has no user authentication. OAuth callbacks, manual token import, scheduling and publishing are privileged operations and must remain local-only until an authenticated operator boundary exists.

### Work

- Add an explicit local-only/dev guard for all social OAuth, token import, disconnect, generation and publish routes.
- Reject non-loopback/public deployment unless an authenticated operator session, CSRF protection, origin allowlist, rate limits and single-use OAuth state bound to the session are configured.
- Disable manual token import by default; keep it only as an explicitly enabled local development escape hatch.
- Add audit events for account connect, test, disconnect, token refresh and publish decisions.

### Exit criteria

- A request from a non-loopback origin cannot trigger a privileged action in the default configuration.
- OAuth state is single-use, expires quickly, includes platform and session binding, and cannot be replayed.
- Tests prove raw tokens never appear in responses, logs or frontend bundles.

## Step 1 — Stabilize the social/media domain contract

### Context brief

The current app has a Threads-specific account and post model. The repository contains an untracked social/media migration draft and a later migration that depends on it, but the runtime model/schema/client contract must be checked before adding more migrations.

### Work

- Inspect every target database's `alembic_version` before touching revision lineage. Never modify or downgrade an applied revision; if `0005`/`0006` has shipped, add a new forward migration and require a backup.
- Reconcile `SocialAccount`, `MediaGeneration`, platform/status enums, and migration `0005` with the current `models.py`, Alembic head, and both SQLite and MySQL test metadata.
- Add response/request schemas that never expose encrypted token fields.
- Define whether MVP supports one or multiple accounts per platform and whether text/time can differ per platform. The recommended MVP is multiple accounts, per-platform caption/time, and one immutable approved snapshot.
- Define provider-neutral account status and media assets with MIME, byte size, duration, content hash, opaque storage path, public URL, and quota metadata.
- Define post destinations/delivery records with `(post, platform, account)` uniqueness, approved version/media snapshot, provider staging/publish ids, retry time, error category/code/request id, and `UNKNOWN` outcome for ambiguous external POSTs.
- Add configuration keys with safe empty/local defaults only.

### Verification and exit criteria

- `python -m compileall backend worker` succeeds.
- Alembic upgrade/downgrade can be exercised against a disposable SQLite database.
- Existing backend tests pass unchanged.
- A new contract test covers missing provider credentials and mock mode.

### Rollback

Before any migration is applied, discard only the uncommitted Step 1 draft. After application, use backup plus a corrective forward migration; never use downgrade as an operational rollback.

## Step 2 — Instagram official connection

### Context brief

Use the official Instagram API with Instagram Login for professional accounts. Request only the basic profile and content publishing scopes required by the product. Store long-lived tokens encrypted and record the returned account id, username, expiry, permissions and last API error.

### Work

- Add configurable client id/secret, callback URI and API base/version.
- Add signed OAuth state bound to `instagram` and callback handling.
- Add token exchange, profile check, expiry/status mapping, disconnect and test endpoints.
- Keep a manual token-import path for local development when OAuth callback hosting is unavailable.
- Add tests for state tampering, token encryption, provider error mapping and mock mode.

### Exit criteria

The provider layer can connect, test, refresh-status, and disconnect an Instagram professional account without exposing tokens. UI work is deferred to Step 6.

### Verification

- Fake Instagram server tests cover OAuth code exchange, expiry mapping, permission failure, rate limit and token redaction.
- Provider tests reject text-only posts and any scheduled media without a verified HTTPS-accessible image/reel URL.

## Step 3 — TikTok official connection

### Context brief

Use TikTok Login Kit and Content Posting API. Request only `user.info.basic` and the publishing scope needed by the chosen workflow. The app must display the privacy options returned by TikTok and explain that unaudited direct-post clients are private-only.

### Work

- Add configurable client key/secret, callback URI and TikTok API base URL.
- Add signed OAuth state, code exchange, profile lookup, refresh-token storage and disconnect/test endpoints.
- Persist creator privacy options and provider rate/error metadata.
- Add tests for OAuth state, token encryption, refresh and private-only audit restriction.

### Exit criteria

The provider layer can connect and validate a TikTok account and refuses to claim public publishing when the app is unaudited or the requested privacy level is unavailable. UI work is deferred to Step 6.

### Verification

- Fake TikTok server tests cover OAuth, refresh, creator-info privacy options, rate limits and state replay.
- The MVP supports upload-as-draft/manual completion using `video.upload`; Direct Post scheduling is explicitly gated behind `video.publish` approval and audit status.

## Step 4 — Local ComfyUI media jobs

### Context brief

ComfyUI is an optional local service. The backend should submit API-format workflows and persist durable generation state; it must not download multi-gigabyte models automatically. Templates may use placeholders for prompt, negative prompt, dimensions, seed and model names.

### Work

- Add ComfyUI health/status and configurable image/video workflow paths.
- Add durable media-generation request/status endpoints and a worker job with timeout, retry and output-file validation.
- Restrict ComfyUI to loopback/private allowlisted endpoints; restrict workflow files to an approved directory and validate placeholders/API-format JSON with no traversal or symlink escapes.
- Validate output magic bytes, MIME, size, duration, content hash, disk quota and safe filename. Expose only READY assets through opaque/signed expiring URLs; require `MEDIA_PUBLIC_BASE_URL` and an actual reachable HTTPS check before URL-based publishing.
- Add model guidance for the available 4 GB NVIDIA T1200: start with a small image model; mark Wan 2.2 as experimental and explain that the linked GGUF repository contains only the text encoder.
- Add a runtime compatibility matrix: Ollama model tags are not automatically ComfyUI workflows; every image/video workflow must declare its checkpoint, custom nodes, license and hardware requirements. No implicit downloads.
- Add tests using a fake ComfyUI HTTP server, missing-service path, invalid workflow/path traversal and corrupt output.

### Exit criteria

Mock mode works without ComfyUI; a configured fake server can produce a READY asset; failed/timeouts are visible and retryable.

## Step 5a — Cross-platform destination and delivery fan-out

### Context brief

The existing `ThreadsPost` state machine and worker are the source of truth. Extend it with destinations and per-destination delivery records instead of making one provider failure mark every provider as failed.

### Work

- Add destination selection to post creation/editing and immutable per-platform delivery snapshots.
- Add atomic delivery claims, unique idempotency keys, provider/account caps, retry-after handling and `UNKNOWN` state for ambiguous external POSTs.
- Define aggregate post status for partial success and behavior when an account is disconnected while deliveries are pending.

### Exit criteria

The worker can fan out one approved snapshot independently without one provider failure corrupting another provider's delivery state.

## Step 5b — Instagram delivery

### Work

- Implement supported feed-image, reel and explicitly scoped carousel media-container flows with status polling.
- Reject missing/invalid HTTPS media before enqueueing; record container id, media id and provider error metadata.
- Add fake-server tests for two-step publishing, status timeout, duplicate prevention and partial success.

## Step 5c — TikTok delivery

### Work

- Implement creator-info query before each publish and honor returned privacy/comment/duet/stitch options.
- Implement upload-as-draft/manual completion first; add Direct Post only behind explicit `video.publish` approval/audit configuration.
- Support local file upload separately from verified-domain URL pull; persist publish id and status polling results.
- Add fake-server tests for photo/video initialization, upload chunks, status polling, privacy mismatch, rate limit and ambiguous outcome.

## Step 5d — Partial-success reconciliation

### Work

- Reconcile provider statuses after worker crashes without retrying an ambiguous external POST blindly.
- Provide operator retry only for safe retry classes and preserve the approved content/media/version snapshot.
- Add integration tests for one success + one failure, disconnect during queueing, and duplicate worker claims.

## Step 6 — Frontend, documentation and release gate

### Work

- Add an Accounts area for Threads/Instagram/TikTok cards and OAuth/manual-token actions.
- Add platform selectors and media attachment/generation status to Content and Calendar.
- Add a Media page for ComfyUI health, prompt/workflow selection, generation status and local preview.
- Update `.env.example`, `docker-compose.yml`, callback/origin configuration, media volumes and backup documentation.
- Document Meta/TikTok app setup, callback URLs, HTTPS/public media requirements, model licenses and 4 GB hardware expectations.
- Keep mock fixtures and frontend tests aligned with the new contracts.

### Release gate

- Backend unit/integration tests pass.
- Frontend typecheck, build and tests pass.
- Alembic upgrade is tested on a disposable database.
- Mock mode starts with no external credentials.
- A manual test proves approval → schedule → provider delivery → persisted per-platform status.
- No secret is present in frontend bundles, logs, fixtures or documentation examples.
- Optional live-provider smoke tests are separate from the deterministic mock-only release gate and require explicit operator credentials.

## External constraints to preserve in the implementation

- Instagram publishing is for professional accounts and requires the appropriate content-publishing permission; many media flows require an HTTPS-accessible media URL.
- TikTok Content Posting API requires app/product approval and user authorization; unaudited direct-post clients are restricted to private viewing.
- `wangkanai/wan22-qx-encoders-gguf` is a quantized UMT5 text encoder for Wan 2.2, not a complete video-generation checkpoint. The full Wan model and suitable workflow remain separate downloads.
- “Free” means no paid API/provider is required; local compute, disk, electricity, HTTPS hosting and platform review remain operational constraints.
