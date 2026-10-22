"""Pin a diagnostic and capture only the caller's owned native engine process."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid

import psutil

ROOT = Path(__file__).resolve().parents[3]
REPOSITORY = (ROOT / 'runtime').resolve().parent


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def pin_tool():
    source = ROOT / 'runtime/build/engine-v1/bin'
    content = (source / 'process-loopback.build.json').read_bytes()
    metadata = json.loads(content)
    if metadata.get('schema_version') != 1 or metadata.get('source_hash_policy') != 'normalized LF text':
        raise ValueError('Unknown process capture build')
    expected_sources = {'native/engine/ProcessLoopback.cpp','native/engine/AudioScheduling.h',
                        'native/engine/CMakeLists.txt','scripts/build_engine.py'}
    if set(metadata['source_files']) != expected_sources:
        raise ValueError('Incomplete process capture source attestation')
    for relative, expected in metadata['source_files'].items():
        if hashlib.sha256((ROOT / relative).read_bytes().replace(b'\r\n', b'\n')).hexdigest() != expected:
            raise ValueError('Process capture differs from the fixed source: ' + relative)
    original = source / 'ml_process_loopback.exe'
    if digest(original) != metadata['executable_sha256']:
        raise ValueError('Process capture executable changed')
    directory = (ROOT / 'runtime/engine-diagnostics' / hashlib.sha256(content).hexdigest()).resolve()
    directory.relative_to(REPOSITORY / 'runtime/engine-diagnostics')
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / original.name
    if target.exists():
        if digest(target) != metadata['executable_sha256']:
            raise ValueError('Pinned process capture changed')
    else:
        pending = directory / (uuid.uuid4().hex + '.partial')
        shutil.copyfile(original, pending)
        if digest(pending) != metadata['executable_sha256']:
            raise ValueError('Process capture changed during pinning')
        os.replace(pending, target)
    record = directory / 'build.json'
    if record.exists() and record.read_bytes() != content:
        raise ValueError('Pinned process capture attestation changed')
    if not record.exists():
        pending = directory / (uuid.uuid4().hex + '.partial')
        pending.write_bytes(content)
        os.replace(pending, record)
    return target, metadata


def capture_engine(broker, output, seconds=1.2, *, tool=None):
    output = Path(output).resolve()
    output.relative_to(REPOSITORY / 'data/maintenance/native-engine/n7')
    if output.exists() or not output.parent.is_dir() or not .2 <= seconds <= 5:
        raise ValueError('Use a new bounded process capture in an existing evidence directory')
    state = broker.status()
    owner = broker.owner
    if (owner is None or owner.process.poll() is not None or state.get('pid') != owner.process.pid
            or state.get('status') in ('closed', 'stopped')):
        raise ValueError('No live owned native engine to capture')
    executable = Path(owner.process.args[0]).resolve()
    if executable.name.lower() != 'ml_engine.exe' or Path(psutil.Process(state['pid']).exe()).resolve() != executable:
        raise ValueError('Process capture target identity changed')
    capture, metadata = tool or pin_tool()
    capture.resolve().relative_to(REPOSITORY / 'runtime/engine-diagnostics')
    if digest(capture) != metadata['executable_sha256']:
        raise ValueError('Pinned capture changed before execution')
    result = subprocess.run([str(capture), str(state['pid']), str(output), str(seconds)],
        capture_output=True, text=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise RuntimeError('Process loopback failed: ' + result.stderr[:1000])
    observation = json.loads(result.stdout)
    if observation['target_pid'] != state['pid'] or observation['scope'] != 'windows_process_tree_loopback':
        raise ValueError('Process loopback did not confirm the owned target')
    observation['capture_executable_sha256'] = metadata['executable_sha256']
    return observation
