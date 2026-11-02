# Threads Recruitment Manager architecture

## Status and scope

This document describes the current architecture of Threads Recruitment Manager (the project has left its greenfield stage; sections written in a planning voice reflect decisions that are already implemented). The project is a Docker-first recruitment and social operations platform for Windows 11 with a React interface, FastAPI application, MySQL persistence (including the durable `BackgroundJob` queue), Redis worker heartbeat, LiteLLM model routing, an optional Hermes orchestration boundary, and an official Meta Threads API adapter.

The complete product flow must work without external credentials in an explicitly labelled mock mode. Real publishing, AI providers, and account connection remain disabled until configured. Human approval is the default for every external action.

## Current architecture

The starting workspace was intentionally clean. The new repository is organized into:

- `frontend/`: React, TypeScript, Vite, Tailwind, TanStack Query, and charts.
- `backend/`: FastAPI, Pydantic, SQLAlchemy 2, Alembic, domain services, API adapters, and tests.
- `worker/`: database-backed background execution and periodic scheduling entry points.
- `hermes/`: Hermes integration configuration and orchestration boundary.
- `litellm/`: logical model-route configuration.
- `database/`: database bootstrap assets when required.
- `scripts/`: Windows-friendly operational scripts.
- `docs/`: architecture and operating documentation.
- `docker-compose.yml`: the primary runtime definition.

Docker Compose is the supported production-like execution path; host Python, Node.js, MySQL, and Redis are not required.

## Design principles

- Make unsafe actions impossible by default: `AUTO_POST`, `AUTO_REPLY`, and `AUTO_EXTERNAL_COMMENT` default to false.
- Keep recruitment data in one domain model. Social records reference vacancies and candidates instead of duplicating them.
- Centralize external integrations behind adapters and never expose secrets to browser code.
- Route every model request through logical LiteLLM aliases; business code never names an AI provider.
- Treat Scout, Strategist, Writer, Community, and Analyst as roles over a shared orchestration stack, not separate platforms.
- Enforce post and reply state transitions in backend services, not merely in UI controls.
- Make jobs idempotent with remote IDs, unique keys, database constraints, and row-level database locks.
- Store times in UTC and render the configured timezone, initially `Europe/Warsaw`.
- Treat comments, posts, and model output as hostile input. Agent tools are allowlisted and external text cannot alter policy or configuration.

## Target architecture

```text
Browser
  -> frontend (React/Vite served by nginx)
  -> backend (FastAPI)
       -> MySQL (system of record, includes the BackgroundJob queue)
       -> Redis (worker heartbeats only)
       -> ThreadsClient (official Meta Threads API or isolated mock adapter)
       -> Hermes orchestration boundary
            -> LiteLLM logical routes
                 -> configured AI providers

worker
  -> claims jobs from the MySQL BackgroundJob table (FOR UPDATE SKIP LOCKED)
  -> re-validates state in MySQL
  -> invokes Threads/Hermes adapters

scheduler
  -> enqueues due publishing, sync, analytics, and agent jobs
```

The backend remains authoritative. A scheduled job does not itself grant permission to publish: immediately before the external call, the worker verifies the post is still approved, due, unmodified, and not already published.

## Database design

### Recruitment domain

- `agencies`: reusable recruitment organization records.
- `vacancies`: structured job facts including location, salary, student rate, currency, schedule, contract, housing, transport, requirements, contact, language, and active state.
- `candidates`: candidate profiles created deliberately by an operator or conversion flow; community activity alone never creates a candidate.

### Threads domain

- `threads_accounts`: connection metadata, encrypted/token reference, scopes, expiry, mock/real mode, sync timestamps, and sanitized errors.
- `threads_posts`: content, hook, type, language, lifecycle state, optional vacancy, remote IDs, AI provenance, approval/schedule/publication timestamps, failure reason, and version.
- `threads_post_revisions`: immutable edit and generation history.
- `threads_post_approvals`: reviewer decisions and reasons.
- `threads_interactions`: deduplicated replies/mentions, classification, confidence, risk, moderation state, and parent context.
- `threads_reply_drafts`: generated/manual replies with approval/publication lifecycle.
- `threads_candidate_signals`: evidence, intent, confidence, optional vacancy/candidate links, and review lifecycle.
- `threads_topics`: Scout discoveries and Strategist ideas with evidence and lifecycle.
- `threads_post_metrics`: timestamped metric snapshots supported by Meta.
- `threads_agent_configs`: logical role configuration, enabled state, route, tool allowlist, and schedule.
- `threads_agent_runs`: execution history, safe input/output summaries, route, duration, status, and error.
- `threads_activity_events`: audit-friendly operational timeline.
- `threads_settings`: singleton or tenant-scoped behavior, branding, timezone, limits, keywords, polling, and safe automation flags.
- `threads_sync_state`: cursors and per-stream synchronization status.

All tables use foreign keys, indexes for common filters, UTC timestamps, and uniqueness for remote IDs/idempotency keys. Schema changes are delivered through Alembic migrations.

## Post state machine

Supported states are `DRAFT`, `AI_GENERATED`, `REVIEW`, `APPROVED`, `SCHEDULED`, `PUBLISHING`, `PUBLISHED`, `FAILED`, and `ARCHIVED`.

The normal AI path is:

```text
AI_GENERATED -> REVIEW -> APPROVED -> SCHEDULED -> PUBLISHING -> PUBLISHED
```

Manual drafts enter `DRAFT`. Rejection returns content to an editable state with an audit record. Failed publication records a sanitized reason and can be retried only through an explicit, idempotent action. Archived and published records are immutable except for safe metadata operations.

## API design

Typed JSON endpoints are grouped under `/api`:

- `/health` and `/dashboard` for service state and operational overview.
- `/vacancies` and `/candidates` for recruitment records.
- `/threads/posts` for content CRUD, AI generation/transforms, review, approval, scheduling, publication, duplication, and archiving.
- `/threads/calendar` for scheduled/published views and rescheduling.
- `/threads/community` for replies, mentions, classification, reply drafts, and review actions.
- `/threads/candidate-signals` for triage and conversion.
- `/threads/topics` for Scout and Strategist opportunities.
- `/threads/analytics` for real stored aggregates and recommendations with time range, sample size, and metric.
- `/threads/agents` for graphical role management and run history.
- `/threads/account` for server-side connection lifecycle and sanitized status.
- `/threads/settings` for language, timezone, brand voice, recruiter identity, contacts, keywords, limits, intervals, and automation policies.

Mutation endpoints validate authorization and state, write activity events, and accept idempotency keys where retries may occur. Long-running actions enqueue work and return promptly.

## Frontend design

The UI is a dense, quiet, scannable operations dashboard rather than a marketing page. A stable sidebar exposes Dashboard, Content, Calendar, Community, Candidates, Vacancies, Analytics, AI Agents, Threads Account, and Settings.

The design uses neutral surfaces, compact tables, restrained status colors, explicit action hierarchy, accessible focus states, responsive drawers/dialogs, and charts only where they clarify stored data. A persistent banner distinguishes mock mode from real integrations.

The frontend uses a centralized typed client and TanStack Query. It never receives application secrets or complete access tokens. Disabled and dangerous actions explain why they are unavailable and require confirmation where appropriate.

## AI orchestration

Hermes is isolated from domain logic behind a typed orchestration interface. Roles share that interface and use LiteLLM logical aliases:

- Scout (`free-fast` or configurable): ranks supported interaction/topic opportunities and creates ideas; never mass-comments.
- Strategist (`free-reasoning`): creates evidence-backed content plans from vacancies, topics, stored metrics, and content mix.
- Writer (`free-default`): returns structured draft fields and a short rationale; never publishes.
- Community (`free-fast`): classifies untrusted interactions and proposes reviewable replies.
- Analyst (`free-reasoning`): interprets stored metrics, always reporting time range, sample size, and metric; it abstains when evidence is insufficient.

When providers are unavailable, deterministic mock implementations return clearly labelled test results. Mock and real account data are isolated by account/mode and never silently mixed.

## Threads integration

`ThreadsClient` is the only interface to Meta. The real adapter implements only operations verified in current official documentation: account/OAuth lifecycle, supported publishing formats, replies/mentions where authorized, supported insights, pagination, rate limits, and normalized errors. Keyword search is exposed only if the current official API and granted permissions support it; no scraping or guessed endpoints are used.

The mock adapter provides a connected fictional account, posts, replies, mentions, metrics, and simulated idempotent publication. It is the default until credentials and real mode are explicitly configured.

OAuth is server-side with state/CSRF protection and documented callback validation. PKCE is used only where supported. Tokens are encrypted or stored through a secret reference and are never logged, returned in full, or embedded in frontend bundles.

## Background processing

The worker polls every few seconds and claims due jobs directly from the MySQL `BackgroundJob` table (`SELECT … FOR UPDATE SKIP LOCKED`); Redis is not part of the job queue and only carries worker heartbeats. The scheduler may share the worker image but runs as a distinct process when useful operationally.

Jobs have bounded retries with backoff, timeouts, durable terminal results, and idempotency keys. Unique database constraints protect remote objects and candidate signals. Row-level MySQL locking prevents concurrent processing, while MySQL remains the final authority.

## Security

- Secrets are supplied at runtime and excluded from version control.
- Logs are structured and redact tokens, keys, passwords, authorization headers, and app secrets.
- Backend validation and role checks protect every mutation; UI visibility is not authorization.
- External text is data, never instructions. It cannot change prompts, tool permissions, settings, or automation policy.
- Agents receive least-privilege allowlisted tools and structured output schemas.
- Auto-post, auto-reply, and external commenting are off by default with configurable hourly safeguards.
- High-risk, legal, complaint, conflict, and low-confidence interactions always require review.
- Candidate data is minimized; test seeds contain no real personal information.
- Approval, publication, agent configuration, account lifecycle, signal conversion, and settings changes are auditable.
- External calls use timeouts, retry limits, rate-limit handling, and sanitized errors.

## Docker and operations

Compose provides frontend, backend, worker/scheduler, MySQL 8, Redis, LiteLLM, Hermes integration, and Adminer. Containers communicate by service DNS names. MySQL host access binds port `3307`, Adminer binds `8081`, and neither should be exposed beyond the host by default. Named volumes preserve MySQL, Redis, and required orchestration state.

Health checks verify real readiness for database, cache, backend, model gateway, orchestration service, and worker heartbeat. Application services use `restart: unless-stopped` where appropriate and depend on health rather than startup order alone.

PowerShell backup and restore scripts run through Docker. Restore requires an explicit target file and confirmation and never overwrites automatically.

## Implementation phases

1. Establish repository structure, environment contract, Compose topology, and the architecture described in this document.
2. Implement SQLAlchemy models, Alembic, MySQL/Redis connectivity, and health reporting.
3. Deliver vacancy and candidate foundations with safe fictional seed data.
4. Deliver the React application shell, navigation, dashboard, and typed API layer.
5. Configure LiteLLM logical routes and the mock/provider-degraded model path.
6. Establish the Hermes orchestration boundary and role/tool configuration.
7. Deliver Writer and Content Manager as the first full vertical slice.
8. Add review, approval, scheduling, worker execution, and calendar.
9. Add the documented Threads adapter, mock account, connection UI, and publishing.
10. Add Community inbox, classification, reply review, and candidate signals.
11. Add Scout and Strategist topic/planning flows.
12. Add stored analytics and Analyst recommendations.
13. Harden security, tests, backup/restore, observability, and operator documentation.

## Definition of done

`docker compose up -d --build` starts a healthy stack. In mock mode an operator can create a vacancy, generate a structured post draft, edit it, submit and approve it, schedule and simulate publication, inspect the calendar, review community activity and candidate signals, manage logical agents, view honest analytics and dependency health, and configure safe settings. Tests never contact Meta or publish real content.
