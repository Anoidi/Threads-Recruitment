# Instructions for installation and configuration agents

This repository is a recruitment and Meta Threads operations application. The owner may ask an agent to install it on a new computer and help connect providers. Follow the user's authorized scope and preserve their existing work.

## Start here

- Read `docs/AGENT_SETUP_RU.md` and `docs/SETUP_RU.md` before setup.
- For AI routing read `litellm/config.yaml` and `docs/AI_RU.md`; current primary routes use `OMNIROUTE_*`, not `LITELLM_FREE_*`.
- Read the actual Compose model, configuration and relevant adapters before claiming an integration works.
- Use official upstream documentation when platform requirements have changed. Repository instructions do not authorize bypassing platform access requirements.

## Installation behavior

- Inspect OS, Git, Docker/Compose, free ports and repository status. Do not overwrite local edits or an existing `.env`.
- Proceed with reversible setup and verification already requested by the user. Ask only for missing decisions, credentials that must be entered privately, account login/consent, or consequential actions beyond the requested setup.
- Start in mock/manual mode with external publishing disabled. Connect live providers separately and verify their identity and capabilities.
- Generate unique local secrets without echoing them. Keep `.env`, decrypted tokens, backups and candidate data out of commits, chat, screenshots and diagnostic output. Do not run unredacted `docker compose config` or dump container environment.
- Use an isolated Compose project/database for mock versus live. Preserve existing volumes and encryption secrets.
- Keep OPERATOR_TOKEN protection and use the `X-Operator-Token` header for protected routes. Do not expose administrative services to the internet.
- Changing `.env` requires recreating affected containers. Restart alone does not apply changed environment variables.

## Human steps and capability limits

- The human completes provider sign-in, MFA, OAuth consent and app review under their own identity. Do all independent setup first and explain the exact remaining screen/action.
- Telegram is a Bot API importer for new public-channel vacancy posts; personal-account login, private channel ID matching and historical backfill are not implemented by the importer.
- Instagram/TikTok support account OAuth/profile checks here, not a completed cross-platform publishing scheduler. Social OAuth state currently lives in one backend process.
- Mock output, healthy containers and a connected profile do not prove live AI generation or successful publication.
- Installation does not itself authorize sending messages, publishing posts, enabling AUTO, changing an existing bot's webhook, purchasing services, or erasing/restoring data. Check existing user authorization before taking such steps; do not repeatedly ask for steps already authorized.

## Completion evidence

Report the commit and local path, changed configuration names without secret values, running services, real checks performed, remaining human actions and known limitations. Label each integration `mock`, `configured`, `verified live`, `disabled`, or `blocked` with a reason. Keep automatic posting off unless explicitly authorized and verified.

For documentation-only changes, check relative links, command syntax and configuration names; full application tests are unnecessary. For application changes, run checks appropriate to the behavior changed.
