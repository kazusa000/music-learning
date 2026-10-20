"""Small persistent defaults; runtime/library discovery is read-only."""

import json
from pathlib import Path
from sqlalchemy import insert, select, update
from . import contracts as c
from .schema_v8 import preferences
from .service import DomainError, emit
from .settings import ROOT


def defaults(db):
    record = db.execute(select(preferences).where(preferences.c.id == 1)).mappings().first()
    return dict(
        version=record["version"] if record else 0,
        configured=bool(record),
        preferences=c.ApplicationPreferences.model_validate(record["payload"] if record else {}).model_dump(),
    )


def view(service, db):
    result = defaults(db)
    result["runtime"] = dict(
        data_root=str(service.settings.data_root),
        instrument_library=str(service.settings.instrument_library)
        if service.settings.instrument_library
        else None,
        plugin_roots=[str(p) for p in service.settings.plugin_roots],
    )
    result["analysis_models"] = [
        dict(id="muscriptor", name="MuScriptor medium", purpose="多声部扒谱"),
        dict(id="demucs", name="HTDemucs", purpose="原曲分轨"),
        dict(id="sheetsage", name="SheetSage2", purpose="节拍与和声结构"),
    ]
    result["background_verification"] = None
    pointer = ROOT / "runtime/keyscape-full-job.local.json"
    try:
        request_path = Path(json.loads(pointer.read_text())["request"])
        request = json.loads(request_path.read_text())
        status = json.loads((request_path.parent / "status.json").read_text())
        progress = json.loads((Path(request["run_directory"]) / "progress.json").read_text())
        result["background_verification"] = dict(
            status=status["status"], total=progress["total"], counts=progress["counts"]
        )
    except (OSError, ValueError, KeyError):
        pass
    return result


def dispatch(service, db, op, args, request):
    if op == "application.settings.get":
        return view(service, db)
    before = defaults(db)
    if before["version"] != args.expected_version:
        raise DomainError("settings_conflict", "设置已在其他窗口更新，请刷新后重试。", 409)
    patch = args.preferences.model_dump(exclude_unset=True)
    payload = c.ApplicationPreferences.model_validate({**before["preferences"], **patch}).model_dump()
    if before["configured"]:
        db.execute(
            update(preferences)
            .where(preferences.c.id == 1)
            .values(version=before["version"] + 1, payload=payload)
        )
    else:
        db.execute(insert(preferences).values(id=1, version=1, payload=payload))
    emit(db, "application.settings.changed", request_id=request.request_id)
    return view(service, db)
