# Threads Recruitment Manager

For current step-by-step setup, provider connection checks and agent-assisted
installation, see [the Russian setup index](docs/SETUP_RU.md) and
[the agent handoff prompt](docs/AGENT_PROMPT_RU.md). The technical details below
also include configuration history; use the setup guides for a new machine.


Threads Recruitment Manager is a Docker-first recruitment and Meta Threads
operations dashboard. It keeps vacancies, content, approval, scheduling,
community triage, candidate signals, and analytics in one review-first workflow.

The default setup is deliberately safe: Threads and AI run in mock mode,
`AUTO_POST`, `AUTO_REPLY`, and `AUTO_EXTERNAL_COMMENT` are disabled, and no real
external action occurs until credentials and explicit settings are supplied.

## Requirements

- Windows 11 with WSL2 enabled
- Docker Desktop with the Compose v2 plugin
- At least 8 GB RAM available to Docker Desktop (12 GB is more comfortable when
  Hermes is active)
- PowerShell 5.1 or newer

Python, Node.js, MySQL, and Redis are not required on the host for normal use.

## First startup

Open PowerShell in the repository directory:

```powershell
Copy-Item .env.example .env
docker run --rm python:3.12-alpine python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Run the password command several times and place different values in `.env` for
`MYSQL_PASSWORD`, `MYSQL_ROOT_PASSWORD`, `LITELLM_MASTER_KEY`, and
`HERMES_API_KEY`. Update `DATABASE_URL` with the same application database user
and password. `token_urlsafe` output needs no URL encoding; if you choose another
password format, URL-encode reserved characters in `DATABASE_URL`.

Then validate and start the stack:

```powershell
.\scripts\validate-infrastructure.ps1
docker compose up -d --build
docker compose ps
```

Initial image downloads and builds can take several minutes. Healthy services
become available at:

| Component | URL / port | Exposure |
| --- | --- | --- |
| Web application | http://localhost:3000 | Host |
| FastAPI / OpenAPI | http://localhost:8000/docs | Host |
| Health report | http://localhost:8000/api/health | Host |
| LiteLLM | http://localhost:4000 | Loopback only |
| Adminer | http://localhost:8081 | Loopback only |
| MySQL | `127.0.0.1:3307` | Loopback only |
| Hermes API | http://localhost:8642 | Optional (`hermes` profile), loopback only |
| Redis | `redis:6379` | Docker network only |

In Adminer select `MySQL`, use server `mysql`, and enter the `MYSQL_USER`,
`MYSQL_PASSWORD`, and `MYSQL_DATABASE` values from `.env`.

## Services and data flow

- `frontend`: React/Vite application served by nginx; `/api` is proxied to the
  backend over Docker DNS.
- `backend`: FastAPI REST API and the only service permitted to authorize domain
  changes or external social actions.
- `worker`: asynchronous publishing, synchronization, classification, agent, and
  analytics jobs with database-backed durability and Redis wakeups/locks.
- `mysql`: source of truth for recruitment, Threads, approvals, jobs, and audit
  events. The host mapping is `3307 -> 3306`.
- `redis`: ephemeral runtime coordination plus append-only persistence. It is not
  the source of truth for scheduled work.
- `litellm`: single model gateway exposing the logical aliases `free-fast`,
  `free-default`, `free-reasoning`, `free-coder`, and `free-long-context`.
- `hermes`: official Nous Research Hermes Agent gateway, optional and off by
  default (see the Hermes section); the application does not depend on it.
- `adminer`: local-only database diagnostics.

Persistent state lives in the named volumes `mysql_data`, `redis_data`, and
`hermes_data`. `docker compose down` preserves them. **`docker compose down -v`
deletes them** and should be used only after a verified backup and an intentional
reset.

## Mock development mode

The checked-in defaults are usable without Meta or AI provider credentials:

```dotenv
THREADS_MOCK_MODE=true
AI_MOCK_MODE=true
LITELLM_CONFIG_FILE=/app/config.mock.yaml
AUTO_POST=false
AUTO_REPLY=false
AUTO_EXTERNAL_COMMENT=false
```

Mock and live account data must not be mixed. Before connecting a real account,
use a separate database/Compose project or deliberately clear test data after a
backup. The UI displays the mock state so operators do not mistake simulated
publishing for a live action.

## Content preparation and stock

In `AUTO` mode the pipeline is **generate → deterministic checks → model QA →
repair and recheck (at most two repairs) → schedule → publish**. A failed draft
stays outside the publishing queue with its reason and revision history. An
operator edit resets its repair allowance. Vacancy sources that need confirmation
require a person; rewriting cannot make those facts trusted.

The writer, repairer and reviewer use the same canonical vacancy fact lines and
confirmed account contact. QA is bound to the exact content/version, language,
content type, vacancy facts, source confirmation and contact. Changing any of
these invalidates the result. Publication performs a cheap fingerprint,
permission, duplication and freshness check, without calling a model.

The hourly `prepare_content` job runs in `worker-agents`. In `AUTO`, with the
content plan enabled and emergency stop off, it revalidates the queue, fills
free slots from eligible drafts and requests content batches of up to six posts
until the rolling three-day schedule is covered. Generation is bounded to one
refill batch per hour and four per local day, in addition to the normal daily
plan. Pending/running plan jobs prevent a second concurrent refill batch. Refill plans use only fresh confirmed vacancies; without them they can generate general advice and questions without vacancy-specific claims.
Autonomy policy and topic restrictions still apply. A day does not repeat a primary subject across refill batches. Format experiments keep their controlled wording and are reviewed without automatic rewriting; fallback drafts explicitly marked for operator review remain manual. Manually approved content is
not automatically rewritten by the legacy-rejection recovery.

The dashboard shows checked scheduled posts, ready reserve, approximate stock in
days, next publication, empty slots and rejection reasons. Gap and recovery events
are saved to the activity history only when the upcoming gap state changes.
These are in-app notices; no external messages are sent.

## Automatic monitoring and content planning

The worker schedules recurring work itself — no external cron is required:

- **Threads monitoring**: every `polling_interval_seconds` (default 300 s) it
  enqueues `sync_community`, which pulls incoming replies and mentions,
  classifies them, creates candidate signals and reply drafts. Controlled by
  the `auto_community_sync` setting. Every cycle records what it fetched, what
  was new and what the cursor hid in `community_sync_completed`, so a silent
  loss of replies cannot look like "no engagement" again. The
  `reply_scan_interval_minutes` setting (default 30) controls how often a
  post's conversation is re-read; keep it below 60 minutes, because the sync
  cursor only carries one hour of slack.
- **Audience profile**: every captured reply and candidate signal is reduced
  to the condition it is about (`backend/app/content_subjects.py`). The
  profile compares what the audience asks with what the account has already
  answered and names the gap — `GET /api/threads/audience` and the "Аудитория"
  block on the Analytics page. The planner receives that profile, and a
  repeated or unused subject is replaced by an angle on the theme the
  audience asked about and we have not answered yet. A comment is not a
  question: only real asks count as demand, and the report states its own
  sample size.
- **Strategy angle**: once a day the strategist proposes one new content
  angle (`auto_strategy_angle`). The angle becomes a topic the content plan
  can use, so the strategist feeds the pipeline instead of accumulating
  unused ideas. Near-duplicate angles are rejected.
- **Capability report**: `GET /api/threads/capabilities` and the
  "Разрешения Threads" block on the Account page show which Meta permissions
  the account has, which are missing and what stays invisible without them.
  `docs/meta-scopes-request.md` is the ready-to-submit request for the
  missing scopes.
- **Recruitment funnel**: a captured question is a signal, not a candidate. A
  candidate record requires a confirmed contact channel, and every stage
  change (`NEW → CONTACTED → QUALIFIED → HIRED`) is a manual decision written
  to an audit trail with the actor and the reason. The dashboard reports the
  confirmed funnel — signals in the queue, contacts confirmed, qualified,
  hired — so an unanswered question never looks like a result.
- **Autonomy Policy Engine**: what the system may do on its own is decided on
  the server, never by an agent. One policy row holds the mode
  (`MANUAL` / `SEMI_AUTO` / `AUTO`), the limits, the emergency stop and the
  allowlists; the old `auto_post` / `auto_reply` flags are derived from it
  and are no longer a second source of permission. The check runs immediately
  before the external action, so tightening the mode stops a publication that
  is already queued. Every allow, review and deny is written to
  `threads_policy_decisions` with the policy version. `AUTO` cannot be
  entered while the vacancy freshness and model QA checks are missing.
- **Vacancy freshness and model QA**: the two checks the automatic mode
  depends on. Freshness is a state, not a guess: the source is trusted for
  14 days after a human confirmation, and a source that changed after
  publication is *drift* — the published conditions are no longer the
  conditions in the source, and every post that quoted the old numbers is
  flagged. The model review checks the language, the support for every
  claim, the honesty of the promises and the voice. It is deliberately
  one-sided: it can block a post, never clear one. The deterministic publish
  gate keeps the last word.
- **Metric collection**: insights are collected for every published post. A
  post whose media id the platform can no longer load (published under another
  account, deleted, or outside the retention window) is marked
  `metrics_status=UNAVAILABLE` and excluded from the comparison instead of
  being retried forever. `POST /api/threads/posts/{id}/metrics/retry` clears
  the mark after the account is reconnected.
- **Audience content plan**: once per day it enqueues `content_plan`. The
  local AI proposes up to six posts for the audience (FAQ, educational,
  engagement, myth-busting — not only vacancies), built strictly from stored
  vacancy facts and recent audience questions. Each idea becomes an editable
  `AI_GENERATED` draft that still requires review and approval before it can
  be scheduled or published. Controlled by the `auto_content_plan` setting.
  Two posts in one run may not cover the same condition: every post is reduced
  to the subjects it talks about (`backend/app/content_subjects.py`), and a
  repeated subject is replaced by a deterministic angle built from confirmed
  vacancy facts (`backend/app/editorial_angles.py`). Without that rule one
  plan produced six posts about the same housing deduction in six different
  wordings.
- **Telegram vacancy import**: when `TELEGRAM_BOT_TOKEN` and
  `TELEGRAM_CHANNEL` are configured, the worker polls every 60 seconds (bounded
  to 30–3600 s). The bot must be a channel administrator. Imports are
  deduplicated and retain the original message URL; Telegram Bot API polling
  only delivers updates available to the bot and does not backfill old channel
  history. Fields not explicit in the source remain blank or “not specified”.
- **Topic/news scout**: every six hours it reads public Google News RSS and,
  after a live Threads account is connected with `threads_keyword_search`, recent
  public Threads results. It stores source-linked topics and creates concise
  cited drafts; discovery itself never publishes.
- **Automatic publishing**: the `auto_post` setting is **disabled by default**
  (`backend/app/config.py`, `auto_post = False`), matching the checked-in
  `.env.example` (`AUTO_POST=false`). Permission to publish automatically is
  decided exclusively by the Autonomy Policy — not by the legacy flag: the
  policy matrix publishes automatically only in `AUTO` mode
  (`auto_after_qa`, gated on vacancy freshness and model QA) and in
  `SEMI_AUTO` after explicit human approval. In the default `MANUAL` mode
  generated posts always remain drafts until a human approves and schedules
  them. Publishing additionally requires a non-mock Threads mode and a
  connected, non-mock account with a stored token; with mock mode active
  nothing can reach Meta even when the mode is switched upward. Never treat
  the `AUTO_*` environment defaults as a quality guarantee — the policy and
  the publish gate in the worker remain authoritative.

Both are safe to leave on: monitoring is read-only and planning only creates
drafts unless the auto-post gates above are met. Trigger them manually with `POST /api/threads/content-plan` or the
**Plan content** button on the Content page. Stable idempotency keys keep at
most one sync per polling window and one automatic plan per day, even across
container restarts.

Do not expose the local API through a public tunnel for OAuth yet. The API is
bound to loopback for local use and does not currently have user authentication;
use a properly secured HTTPS deployment/callback before enabling public access.
The Telegram bot token was exposed during local setup and must be regenerated
with BotFather, then replaced in the ignored local `.env` before relying on it.

## Configure an AI provider

Application and agent code use logical model aliases rather than provider model
names.

### Model provider

The model routes go through the local OmniRoute gateway (OpenAI-compatible
`/v1`), with the local Ollama model as the reserve of every alias. The alias
models are chosen explicitly: OmniRoute's `auto/*` routes answer in the
language of whatever model they pick, and this account publishes in Ukrainian
only.

| Alias | Model | Used for |
| --- | --- | --- |
| `free-fast` | `antigravity/gemini-3.1-flash-lite` | comment analysis, quick rewrites |
| `free-default` | `antigravity/gemini-3-flash` | post writing |
| `free-reasoning` | `antigravity/gemini-3.8-flash-high` | planning, strategies, analysis |
| `local` | `ollama_chat/qwen3:4b-instruct` | reserve when the gateway or its credentials run out |

Measured on a Ukrainian recruitment prompt: 1.6–2.8 s per call through the
gateway against 90–130 s on the local 4B model. The daily content plan went
from 176 s to 13 s. `litellm/README.md` has the details and the reasons the
automatic routes are not used.

To run fully local, point `OMNIROUTE_BASE_URL` at nothing and set
`OMNIROUTE_MODEL_*` to the local model id — or simply keep the reserve
alias `local` reachable, which is the default.

Never put a provider key in a YAML file. Keep secrets only in `.env` or a
production secret manager.

The LiteLLM liveness endpoint can remain healthy even when a provider key or
model is invalid; always run a completion test before enabling real AI features.

### Cloud provider (optional)

Each alias reads its own model id and endpoint, so a route can point at any
OpenAI-compatible provider:

```dotenv
OMNIROUTE_MODEL_DEFAULT=openai/gpt-4o-mini
OMNIROUTE_BASE_URL=https://api.openai.com/v1
OMNIROUTE_API_KEY=<ключ>
```

Set `LITELLM_CONFIG_FILE=/app/config.yaml`, `AI_MOCK_MODE=false` and
recreate the gateway, Hermes, backend, and worker.

```powershell
docker compose up -d --force-recreate litellm hermes backend worker
docker compose logs --tail 100 litellm hermes
```

Keep secrets only in `.env` or a production secret manager.

The LiteLLM liveness endpoint can remain healthy even when a provider key or
model is invalid; always run a completion test before enabling real AI features.

### Why the planner asks for two ideas per call

`backend/app/ai.py` requests at most `PLANNER_BATCH_SIZE` ideas per completion
and repeats the call until the requested count is reached. A single completion
for six posts of up to 500 characters exhausts the token budget of a 4B local
model: the JSON is truncated, the request times out, and the day ends with no
content. When a proposal repeats an angle that is already published, the
planner substitutes a deterministic angle built from confirmed vacancy facts
(`backend/app/editorial_angles.py`) instead of leaving the slot empty.

## Hermes Agent (optional)

Hermes is the official Nous Research agent gateway and reaches LiteLLM at
`http://litellm:4000/v1`. It is **disabled by default** and lives behind the
`hermes` Compose profile:

```powershell
docker compose --profile hermes up -d hermes hermes-init
```

Why it is off by default: the Hermes system prompt is about 13 000 tokens. A
small local model cannot prefill that inside a usable time, so every Hermes
call ends in a timeout — and because Hermes retries with backoff, a running
Hermes service keeps the model busy and slows the content pipeline down. The
measured difference on this hardware: the strategist agent needed 200 s and
always fell back to a deterministic angle, while the same task through the
plain LiteLLM route takes 14 s and returns a real proposal.

Use Hermes when a fast model or a cloud provider is configured. The
application code does not depend on it: the strategist asks the configured
LiteLLM route directly, and `HERMES_FOR_STRATEGIST=true` switches it back.

If an upstream Hermes upgrade reports that interactive setup is required, use
the supported setup flow against the same volume:

```powershell
docker compose --profile hermes run --rm hermes setup
docker compose --profile hermes up -d hermes
```

Hermes is a powerful agent runtime. Its port is loopback-only, it is not given the
Docker socket, and broad terminal/filesystem tools are not provisioned by this
project. Backend tool allowlists and approval checks remain authoritative.

## Configure the Meta Threads API

Create an app in the Meta developer portal and enable the official Threads API.
Configure the exact callback URL from `THREADS_REDIRECT_URI`, then set
`THREADS_APP_ID` and `THREADS_APP_SECRET` in `.env`. Prefer the server-side OAuth
flow in the Threads Account page; `THREADS_ACCESS_TOKEN` exists for controlled
development only. Never put any of these values in `VITE_*` variables.

Before switching modes:

1. Confirm the requested permissions and callback URL in Meta.
2. Keep all `AUTO_*` values `false`.
3. Set `THREADS_MOCK_MODE=false` and recreate backend/worker.
4. Connect and test the account from the UI.
5. Publish a single reviewed test post before increasing limits.

The adapter uses only documented official endpoints. Missing permissions or
credentials should degrade account features, not stop vacancy/content workflows.

## Common operations

```powershell
# Start or apply changes
docker compose up -d --build

# Stop while preserving data
docker compose down

# Rebuild application images
docker compose build --pull backend worker frontend
docker compose up -d

# View status and logs
docker compose ps
docker compose logs --tail 200 backend worker
docker compose logs -f litellm hermes

# Run database migrations manually when needed
docker compose run --rm backend alembic upgrade head

# Run backend and frontend tests in disposable build targets
docker compose --profile test run --rm backend-test
docker compose --profile test run --rm frontend-test
```

## Backup and restore

Backups use `mysqldump` inside the running MySQL container, so no host MySQL tools
are required:

```powershell
.\scripts\backup-db.ps1
.\scripts\backup-db.ps1 -OutputDirectory D:\Backups\Threads
```

Files are gzip-compressed and written under `backups/` by default. The script
refuses to overwrite an existing file.

Restore is intentionally guarded. Back up the current database first, then pass
the exact target database name and confirm interactively:

```powershell
.\scripts\restore-db.ps1 .\backups\threads-recruitment-20261005-120000.sql.gz `
  -ConfirmDatabase threads_recruitment
```

For non-interactive recovery automation, add `-ConfirmRestore` only after the
backup path and target name have been independently verified. A restore may
replace existing tables; it is never triggered by startup.

### Scheduled backups (audit 1.3)

`scheduled-backups.ps1` wraps `backup-db.ps1` so the backup runs every day,
copies the newest archive to a second drive (drive-loss is the scenario the
audit warns about) and prunes old copies:

```powershell
# Dry run by default: prints the exact task that would be created.
.\scripts\schedule-backups.ps1

# Register the daily task (03:30), copy to a second drive, keep 14 archives.
.\scripts\schedule-backups.ps1 -Register -SecondaryPath "D:\Backups\threads-recruitment"

# One backup cycle right now (what the scheduled task executes).
.\scripts\schedule-backups.ps1 -Run -SecondaryPath "D:\Backups\threads-recruitment"
```

The scheduled task runs only while you are logged in (Docker Desktop must be
running); failed cycles are visible in Task Scheduler history and the backup
script aborts on any container error.

### Monthly restore drill

Once a month, prove the backup is usable — into a temporary drill database,
never the working one:```powershell
.\scripts\restore-db.ps1 .\backups\<newest-archive>.sql.gz `
  -TargetDatabase threads_restore_test_drill `
  -ConfirmDatabase threads_restore_test_drill -ConfirmRestore
```

The script refuses `-TargetDatabase` names outside `threads_restore_test_*`,
creates the drill database if needed and leaves real data untouched. Check the
restored tables there (vacancies, threads_posts, candidates counts), then drop
the drill database.

### Data retention (audit 3.2)

Weekly the primary worker enqueues a `retention` task. By default it is a
**dry run**: it logs the `retention_dry_run` warning with the counts it would
remove so growth stays visible, and deletes nothing. Deletion happens only
after `RETENTION_ENABLED=true` in `.env` — an owner decision that must follow
`scripts/backup-db.ps1`. What is pruned (never live content):

| Data | Cut-off (days) | Rule |
|---|---|---|
| `threads_activity_events` | `RETENTION_ACTIVITY_DAYS` (90) | by `created_at` |
| `threads_policy_decisions` | `RETENTION_POLICY_DAYS` (180) | by `created_at` |
| `background_jobs` | `RETENTION_JOBS_DAYS` (30) | terminal (`DONE`/`FAILED`) only, by `updated_at` ≈ completion time |
| `threads_agent_runs` | `RETENTION_AGENT_RUNS_DAYS` (90) | terminal (`COMPLETED`/`FAILED`/`BLOCKED`) only, by `finished_at` |

Deletion runs in batches of 1000 rows, so one weekly task cannot lock a big
table for long.

### Token key rotation (audit 1.2)

After setting `THREADS_TOKEN_KEY` in `.env`, re-encrypt stored tokens once so
the old `LITELLM_MASTER_KEY` stand-in can be retired:

```powershell
docker compose run --rm backend python scripts/rotate_token_key.py            # dry run, prints what it would change
docker compose run --rm backend python scripts/rotate_token_key.py --apply --i-have-a-backup   # after scripts/backup-db.ps1
```

Without `--apply` nothing is written; rows that fail to decrypt are reported
and left alone.

## Testing and verification

```powershell
.\scripts\validate-infrastructure.ps1
docker compose build backend worker frontend
docker compose run --rm backend alembic upgrade head
docker compose --profile test run --rm backend-test
docker compose --profile test run --rm frontend-test
docker compose up -d
Invoke-RestMethod http://localhost:8000/api/health
Invoke-WebRequest http://localhost:3000/healthz
docker compose exec mysql mysqladmin ping -u root -p
docker compose exec redis redis-cli ping
```

Tests mock all external Threads calls and must never be run against live publish
credentials with automatic actions enabled.

## Troubleshooting

**A service remains unhealthy**

Run `docker compose ps` and `docker compose logs --tail 200 <service>`. MySQL can
take 30-90 seconds on its first initialization. Backend and worker wait for their
dependencies before starting.

**Password or database settings changed but login still fails**

MySQL initialization variables apply only to a new volume. Changing `.env` does
not rewrite accounts in an existing database. Change credentials inside MySQL or
back up, deliberately remove the MySQL volume, and restore. Never remove a volume
as a troubleshooting shortcut without checking what it contains.

**Port already in use**

Change the corresponding host port (`FRONTEND_PORT`, `BACKEND_PORT`,
`LITELLM_PORT`, `ADMINER_PORT`, `MYSQL_HOST_PORT`, or `HERMES_HOST_PORT`) in
`.env`, then recreate that service. Container-to-container addresses do not
change.

**AI is healthy but returns mock text**

This is the default. Set a valid provider model/key, switch
`LITELLM_CONFIG_FILE` to `/app/config.yaml`, and recreate LiteLLM. Check its logs
and run a completion against a logical alias before disabling `AI_MOCK_MODE`.

**A model call times out or falls back**

A 4B local model needs 90–130 s for a planner-sized prompt. The route
timeouts in `litellm/config.yaml` are set accordingly; raising them costs
nothing but waiting. If a call falls back, check the LiteLLM logs for
`exceeds the available context size`: the prompt does not fit the route's
`num_ctx`. Also make sure nothing else is using the model — a running
Hermes service retries a 13K-token prompt and keeps the model busy.

**Hermes is unhealthy**

Hermes is optional and off by default. Enable it with
`docker compose --profile hermes up -d hermes hermes-init` and only with a
fast model: its prompt is about 13 000 tokens, which a small local model
cannot prefill in time.

Inspect `docker compose logs --tail 200 hermes`. Verify LiteLLM is healthy and the
Hermes API key is at least eight characters. If Hermes reports incomplete setup,
run the setup command in the Hermes section. State remains in `hermes_data`.

**Frontend loads but API requests fail**

Check backend health directly and inspect both nginx and backend logs. Inside the
Compose network the frontend proxy must use `backend:8000`, never `localhost`.

## Security notes

- `.env`, dumps, logs, and local secret files are ignored by Git.
- Secrets are never exposed as Vite build variables.
- MySQL, Adminer, LiteLLM, and Hermes bind to loopback; Redis has no host port.
- External Threads text is untrusted input. It cannot alter settings, tool
  allowlists, approval state, or automatic-action flags.
- Human approval and backend state-transition checks are required by default.
- Compose is the supported single-machine deployment. It is not a public-internet
  perimeter; place production access behind authenticated TLS and host controls.
  
# Language Policy

- Communication with the project owner: Russian.
- All generated social media publications: Ukrainian.
- All recruitment advertising: Ukrainian.
- All captions, hashtags, and calls to action: Ukrainian.
- Never infer publication language from the user's language.
- Do not translate company names, addresses, or URLs.
- Default content locale: uk-UA.
- Validate publication language before saving or publishing.
