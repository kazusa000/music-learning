"""Prepare a new isolated, quiet native-device UI fixture. No formal/H writes."""
import argparse
import base64
import json
import math
from pathlib import Path
import struct
import sys
import wave

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'src'))
sys.path.insert(0,str(ROOT/'scripts'))
from music_learning import contracts as c  # noqa: E402
from music_learning.database import Database  # noqa: E402
from music_learning.service import Service  # noqa: E402
from music_learning.settings import Settings  # noqa: E402
from music_learning.session import session_token  # noqa: E402
from music_learning.source_reference import build  # noqa: E402
from native_host import NativeHost  # noqa: E402


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--directory',type=Path,required=True)
    parser.add_argument('--port',type=int,default=8794)
    parser.add_argument('--web-root',type=Path,required=True)
    args=parser.parse_args()
    directory=args.directory.resolve()
    directory.relative_to(ROOT/'data/maintenance/native-engine')
    if directory.exists():
        raise ValueError('Use a new independent fixture directory')
    settings=Settings(data_root=directory/'application',port=args.port,instrument_preview_warm=False,
        native_audio_enabled=True,plugin_roots=[ROOT/'runtime/build/msvc/MLFixture_artefacts'])
    database=Database(settings)
    database.migrate()
    service=Service(database)
    def invoke(op,arguments=None,revision=None):
        return service.execute(dict(operation=op,arguments=arguments or {},expected_revision=revision,
            idempotency_key=c.new_id() if c.OPERATIONS[op][1] else None))['result']
    try:
        project=invoke('projects.create',dict(title='Native DAW device workflow'))
        provenance=dict(origin='rule',source_id='native-engine-ui')
        def asset(data,media='application/octet-stream',metadata=None):
            sha=service.storage.put(data)
            with database.transaction() as db:
                service.register_asset(db,project['id'],sha,len(data),media,metadata or {},provenance)
            return sha
        source=directory/'quiet.wav'
        cycle=b''.join(struct.pack('<h',round(6*math.sin(i*2*math.pi*440/44100))) for i in range(4410))
        with wave.open(str(source),'wb') as out:
            out.setparams((1,2,44100,0,'NONE','not compressed'))
            for _ in range(320):
                out.writeframesraw(cycle)
        source_id=asset(source.read_bytes(),'audio/wav',dict(sample_rate=44100,channels=1,duration_samples=44100*32))
        plugin=ROOT/'runtime/build/msvc/MLFixture_artefacts/Release/VST3/MLFixture.vst3'
        descriptor=NativeHost(diagnostics=directory/'inspect').call(dict(schema_version=1,action='inspect',plugin=dict(path=str(plugin))))['plugin']
        state=asset(base64.b64decode(descriptor['state_base64']))
        desc=asset(json.dumps(descriptor).encode())
        parameters={p['id']:.25 if p['name']=='Mode' else 1 if p['name']=='Gain' else p['value'] for p in descriptor['parameters_readback']}
        tracks=[c.Track(id=c.new_id(),name=name,kind='instrument',gain_db=-90,provenance=provenance) for name in ['Piano A','Piano B']]
        snapshot=c.Snapshot(source_asset_id=source_id,duration_samples=44100*32,tracks=tracks,track_order=[t.id for t in tracks],
            plugins=[c.PluginInstance(id=c.new_id(),track_id=t.id,class_id=descriptor['vst3_class_id'],version=descriptor['version'],
                binary_sha256=descriptor['binary_sha256'],state_asset_id=state,descriptor_asset_id=desc,parameters=parameters,order=0) for t in tracks],
            notes=[c.NoteEvent(id=c.new_id(),track_id=t.id,pitch=60+i*7,start_sample=0,end_sample=44100*31,velocity=.5,provenance=provenance) for i,t in enumerate(tracks)])
        waveform=asset(json.dumps(dict(levels=[dict(buckets=[[[0,.0002]]]*320)])).encode(),'application/json')
        snapshot.source_reference=build(namespace=project['id'],source=source_id,frames=44100*32,run_id=c.new_id(),decision='vocal',
            audio=[('vocals','人声',source_id,waveform),('accompaniment','伴奏',source_id,waveform)],
            melody=[dict(sample=0,duration_samples=44100,note=60,velocity=.5)],
            annotations=[dict(start_sample=0,end_sample=44100*16,kind='chord',text='C:maj')],raw_assets=[waveform])
        revision=invoke('revisions.commit',dict(project_id=project['id'],snapshot=snapshot.model_dump(mode='json'),author='test',description='Native device workflow'),project['current_revision'])
        config=dict(data_root=str(settings.data_root),port=args.port,plugin_roots=[str(p) for p in settings.plugin_roots],
            native_audio_enabled=True,instrument_preview_warm=False,
            web_root=str(args.web_root.resolve()))
        (directory/'app.local.json').write_text(json.dumps(config),encoding='utf-8')
        (directory/'fixture.json').write_text(json.dumps(dict(project=project,revision=revision,tracks=[t.id for t in tracks],
            token=session_token(settings),port=args.port)),encoding='utf-8')
        print(directory)
    finally:
        if service.engine_broker:
            service.engine_broker.close()
        database.close()


if __name__=='__main__':
    main()
