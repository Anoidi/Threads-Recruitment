# Local recovery checklist

Backups contain personal data and encrypted account credentials. Keep them off public Git/cloud links, preferably on encrypted storage. The `backups/` directory is ignored by Git.

1. Run `scripts/backup-db.ps1` and `scripts/backup-media.ps1` from the project root. Preserve the resulting SQL gzip and media ZIP together. ZIP CRC and a printed SHA-256 check archive integrity, not completeness of a database/media pair.
2. Separately preserve the original `.env` in an encrypted password manager or encrypted offline backup. Do not paste it into chats. Required decryption keys are `THREADS_TOKEN_KEY` and `SOCIAL_TOKEN_KEY`; if empty, preserve the fallback `LITELLM_MASTER_KEY` too. Keep app credentials and database credentials. Changing keys after restoring does not decrypt old tokens: reconnect accounts if the originals are lost.
3. Keep the source code, workflows and model configuration. Media archives do not include models, workflows or keys. The current media engine remains mock/FFmpeg, not real AI generation.
4. Before restoring production, stop backend/worker, keep an extra backup of the current database and explicitly confirm the database name to `scripts/restore-db.ps1`. Never run a restore against a database selected only by guesswork. Restore is destructive and is not automatically performed by these instructions.
5. Restore ZIP assets first to a new isolated directory with `python -m app.media_restore ARCHIVE.zip NEW_DIRECTORY` (backend Python environment). The utility checks CRC, flat asset names, duplicates, symlinks and unpacked size limits; it refuses an existing destination. If a disk write fails, retain the partial directory for inspection and retry into a different new directory. Copy validated assets into the configured media volume only after checking identity; do not overwrite current assets. Coordinated point-in-time database/media restore still needs verification.
6. Start services, verify migrations, `/api/health`, media previews and account connectivity. Inspect uncertain publication jobs against Threads before retrying. Do not restart scheduled publishing until remote-delivery checks are complete.

Verified 2026-10-09: database-only restoration into a network-isolated disposable MySQL restored 22 tables, 60 posts, 11 vacancies and migration 0008. It did not alter the production database. This is not yet a verified full-system disaster recovery drill.

Media restore separately verified in a disposable network-isolated container: all five archived assets were restored and SHA-256 matched the corresponding ZIP contents. Original media and archive were mounted read-only/unmodified.
