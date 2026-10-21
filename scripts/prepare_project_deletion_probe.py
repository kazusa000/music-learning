"""Small independent deletion fixture. Never accepts the formal data root."""
import argparse
import io
import json
from pathlib import Path
import struct
import wave

from music_learning import contracts as c
from music_learning.database import Database
from music_learning.service import Service
from music_learning.settings import ROOT, Settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory",type=Path)
    args = parser.parse_args()
    root = args.directory.resolve()
    expected = ROOT/"data/maintenance/project-delete-browser"
    if root != expected or (root/"fixture.json").exists():
        raise ValueError("Only a new independent project-delete-browser fixture is allowed")
    root.mkdir(parents=True,exist_ok=True)
    settings = Settings(data_root=root/"application",port=8791,plugin_roots=[],instrument_preview_warm=False)
    database = Database(settings)
    database.migrate()
    service = Service(database)
    def call(op,arguments=None,head=None):
        return service.execute(dict(operation=op,arguments=arguments or {},expected_revision=head,
            idempotency_key=c.new_id() if c.OPERATIONS[op][1] else None))["result"]
    stream = io.BytesIO()
    with wave.open(stream,"wb") as audio:
        audio.setparams((2,2,44100,44100,"NONE","not compressed"))
        audio.writeframes(struct.pack("<hh",2048,-2048)*44100)
    projects = [call("projects.create",dict(title=title)) for title in ("删除演示","保留工程","最后工程")]
    sha = service.storage.put(stream.getvalue())
    for project in projects[:2]:
        with database.transaction() as db:
            service.register_asset(db,project["id"],sha,len(stream.getvalue()),"audio/wav",
                dict(sample_rate=44100,duration_samples=44100,channels=2),dict(origin="user",source_id="deletion-probe"))
        rev = call("revisions.commit",dict(project_id=project["id"],
            snapshot=c.Snapshot(source_asset_id=sha,duration_samples=44100).model_dump(),author="user",description="Independent source"),project["current_revision"])
        project["current_revision"] = rev["id"]
    private = service.storage.put(b"private model output")
    with database.transaction() as db:
        service.register_asset(db,projects[0]["id"],private,20,"application/octet-stream",{},dict(origin="rule",source_id="deletion-probe"))
    snapshot = call("revisions.get",dict(project_id=projects[0]["id"]))["snapshot"]
    snapshot["annotations"] = [c.Annotation(id=c.new_id(),kind="beat",start_sample=0,end_sample=44100,text="Private analysis",
        details={"asset_id":private},provenance=dict(origin="rule",source_id="deletion-probe")).model_dump()]
    rev = call("revisions.commit",dict(project_id=projects[0]["id"],snapshot=snapshot,author="user",description="Private artifact"),projects[0]["current_revision"])
    projects[0]["current_revision"] = rev["id"]
    call("projects.organize",dict(project_id=projects[0]["id"],archived=True),projects[0]["current_revision"])
    config = dict(data_root=str(settings.data_root),port=settings.port,plugin_roots=[],instrument_library=None,instrument_preview_warm=False)
    (root/"app.local.json").write_text(json.dumps(config),encoding="utf-8")
    (root/"fixture.json").write_text(json.dumps(dict(projects=projects,shared=sha,private=private),ensure_ascii=False,indent=2),encoding="utf-8")
    database.close()
    print(json.dumps(dict(config=str(root/"app.local.json"),projects=len(projects))))


if __name__ == "__main__":
    main()
