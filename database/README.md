# Database ownership

MySQL creates the database and application user from Compose environment
variables. `init/001-bootstrap.sql` establishes UTF-8 metadata only on a brand-new
volume. All domain tables, constraints, and indexes are managed by Alembic in the
backend so schema history remains reproducible.

Times are stored in UTC. The frontend renders them in the configured user
timezone (`Europe/Warsaw` by default).

Do not edit a live schema through Adminer. Use a new Alembic revision. Adminer is
intended for local diagnostics and is bound to `127.0.0.1` by Compose.

