import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from app.database import Base
import app.models  # noqa: F401


def test_alembic_revision_ids_fit_default_version_column():
    root = Path(__file__).resolve().parents[2]
    config = Config(root / "backend" / "alembic.ini")
    config.set_main_option("script_location", str(root / "backend" / "alembic"))
    script = ScriptDirectory.from_config(config)
    assert all(len(revision.revision) <= 32 for revision in script.walk_revisions())


def test_alembic_upgrade_creates_every_declared_table(tmp_path):
    root = Path(__file__).resolve().parents[2]
    database = tmp_path / "migration.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite+aiosqlite:///{database.as_posix()}"
    subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"],
        cwd=root / "backend",
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    with sqlite3.connect(database) as connection:
        actual = {row[0] for row in connection.execute("select name from sqlite_master where type='table'")}
    assert set(Base.metadata.tables).issubset(actual)


def test_ai_usage_migration_emits_mysql_compatible_text_column():
    root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    # Offline SQL generation: never connects to this deliberately fake URL.
    env["DATABASE_URL"] = "mysql+aiomysql://test:test@localhost/test"
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade",
         "0017_signal_vacancy_backfill:0018_ai_usage", "--sql"],
        cwd=root / "backend", env=env, check=True, capture_output=True, text=True,
    )
    assert "CREATE TABLE ai_usage" in result.stdout
    error_line = next(line for line in result.stdout.splitlines() if "error TEXT" in line)
    assert "DEFAULT" not in error_line
    assert "NOT NULL" in error_line
