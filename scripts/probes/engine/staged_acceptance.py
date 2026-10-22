"""Run the fixed release's acceptance code against explicit repository resources."""
import argparse
import hashlib
import json
from pathlib import Path
import runpy
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'scripts'))
from release_runtime import verify_source  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--release', type=Path, required=True)
    parser.add_argument('--context-output', type=Path, required=True)
    args, remaining = parser.parse_known_args()
    descriptor = args.release.resolve()
    descriptor.relative_to(ROOT / 'runtime')
    record = json.loads(descriptor.read_bytes())
    source = verify_source(ROOT, record)
    script = source / 'scripts/probes/engine/acceptance.py'
    namespace = runpy.run_path(str(script), run_name='fixed_native_acceptance')
    package = Path(sys.modules['music_learning.service'].__file__).resolve()
    package.relative_to(source)
    bundle, build = namespace['pin']()
    if bundle.resolve() != Path(record['native_audio_bundle']).resolve() or build['files'] != record['native_files']:
        raise ValueError('Acceptance native bundle differs from the staged release')
    context = args.context_output.resolve()
    context.relative_to(ROOT / 'data/maintenance/native-engine/n7')
    with context.open('x', encoding='utf-8') as stream:
        json.dump(dict(commit=record['commit'], source_root=str(source), service=str(package),
            script_sha256=hashlib.sha256(script.read_bytes()).hexdigest(), resource_repository=str(ROOT),
            native_audio_bundle=str(bundle.resolve()), native_files=build['files']), stream, indent=2)
    # Code/imports stay in the fixed snapshot. Only resource/audit paths point
    # at the original repository; no data junction or source file is modified.
    namespace['main'].__globals__['ROOT'] = ROOT
    sys.argv = [str(script), *remaining]
    namespace['main']()


if __name__ == '__main__':
    main()
