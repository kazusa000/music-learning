from __future__ import annotations

from contextlib import contextmanager
import sqlite3
import time

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event

from .settings import ROOT, Settings


class Database:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.path = settings.path("projects.sqlite3")
        self.engine = create_engine(
            "sqlite:///" + self.path.as_posix(), connect_args={"timeout": 30, "check_same_thread": False}
        )

        @event.listens_for(self.engine, "connect")
        def configure(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA busy_timeout=30000")

    def migrate(self):
        if self.path.exists():
            with self.engine.connect() as c:
                exists = c.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE name='alembic_version'"
                ).first()
                version = (
                    c.exec_driver_sql("SELECT version_num FROM alembic_version").scalar() if exists else None
                )
                if version == "0004":
                    return
                if version not in (None, "0001", "0002", "0003"):
                    raise RuntimeError("Database schema is newer or unknown; refusing downgrade")
            self.backup()
        with self.transaction() as c:
            cfg = Config()
            cfg.set_main_option("script_location", str(ROOT / "services/api/migrations"))
            cfg.attributes["connection"] = c
            command.upgrade(cfg, "head")

    @contextmanager
    def transaction(self):
        # Serialize read-check-write operations across API and worker processes.
        with self.engine.connect() as c:
            c.exec_driver_sql("BEGIN IMMEDIATE")
            try:
                yield c
                c.commit()
            except BaseException:
                c.rollback()
                raise

    def backup(self):
        directory = self.settings.path("backups")
        directory.mkdir(exist_ok=True)
        path = directory / f"projects-{time.time_ns()}.sqlite3"
        with sqlite3.connect(self.path) as source, sqlite3.connect(path) as target:
            source.backup(target)
            if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Backup failed integrity check")
        return path

    def close(self):
        self.engine.dispose()
