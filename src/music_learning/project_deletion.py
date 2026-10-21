"""Transactional project erasure; library inventory and runtime settings are outside its scope."""
from sqlalchemy import text
from . import schema_v1 as t
from .service import DomainError, row, emit


def erase(service, db, project_id, request_id):
    row(db, t.projects, "id", project_id)
    active = db.execute(text("SELECT id FROM jobs WHERE project_id=:p AND status IN ('running','queued','cancel_requested')"), {"p":project_id}).first()
    if active:
        raise DomainError("project_busy", "Cancel active project tasks before deletion", 409)
    tables = [r[0] for r in db.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    ids = {"project_id":[project_id]}
    for table, column in (("revisions","revision_id"),("jobs","job_id"),("dialogue_turns","turn_id"),("dialogue_sessions","session_id")):
        if table in tables:
            ids[column] = [r[0] for r in db.execute(text(f'SELECT id FROM "{table}" WHERE project_id=:p'), {"p":project_id})]
    db.exec_driver_sql("PRAGMA defer_foreign_keys=ON")
    removed = {}
    for table in tables:
        if table == "projects":
            continue
        columns = {r[1] for r in db.exec_driver_sql(f'PRAGMA table_info("{table}")')}
        clauses, args = [], {}
        for key, values in ids.items():
            if key not in columns or not values:
                continue
            names = []
            for i,value in enumerate(values):
                parameter = f"{key}_{i}"
                names.append(":"+parameter)
                args[parameter] = value
            clauses.append(f'"{key}" IN ('+",".join(names)+")")
        if clauses:
            count = db.execute(text(f'DELETE FROM "{table}" WHERE '+" OR ".join(clauses)), args).rowcount
            if count:
                removed[table] = count
    db.execute(text("DELETE FROM projects WHERE id=:p"), {"p":project_id})
    emit(db, "project.deleted", request_id=request_id, payload={"project_id":project_id})
    return {"project_id":project_id, "deleted":True, "removed_rows":removed}
