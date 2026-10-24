"""Adding library persistence preserves an existing composition and its tables."""
from alembic import command
from alembic.config import Config

from conftest import invoke
from music_learning.database import Database
from music_learning.service import Service
from music_learning.settings import ROOT


def test_v10_upgrade_keeps_existing_project_rows_and_revision(settings):
    db = Database(settings)
    try:
        with db.transaction() as connection:
            config = Config()
            config.set_main_option("script_location", str(ROOT / "services/api/migrations"))
            config.attributes["connection"] = connection
            command.upgrade(config, "0010")
        service = Service(db)
        project = invoke(service, "projects.create", dict(title="已有编曲", kind="composition", tempo=90, bars=8))
        revision = invoke(service, "revisions.get", dict(project_id=project["id"]))
        with db.transaction(write=False) as connection:
            names = [r[0] for r in connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'")]
            before = {name: connection.exec_driver_sql('SELECT * FROM "' + name + '"').fetchall()
                      for name in names if name != "alembic_version"}
        db.migrate()
        with db.transaction(write=False) as connection:
            for name, rows in before.items():
                assert connection.exec_driver_sql('SELECT * FROM "' + name + '"').fetchall() == rows
            assert connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar() == "0011"
            for name in ["instrument_drafts", "instrument_personal_presets"]:
                assert connection.exec_driver_sql("SELECT count(*) FROM " + name).scalar() == 0
        assert invoke(service, "revisions.get", dict(project_id=project["id"])) == revision
        assert len(list(settings.path("backups").glob("*.sqlite3"))) == 1
    finally:
        db.close()
