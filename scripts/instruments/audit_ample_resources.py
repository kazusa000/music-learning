"""Freeze and supervise the missing full SHA audits of prepared Ample banks."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def load(path):
    return json.loads(Path(path).read_bytes())


def save(path, value):
    from music_learning.kontakt_import import atomic_write_json
    atomic_write_json(Path(path), value)


def prepare(run, *, start=False):
    sys.path.insert(0, str(ROOT / 'src'))
    from music_learning.ample_publication import PRODUCTS
    from music_learning.kontakt_resource_audit import _plain_path
    run = Path(run).absolute()
    allowed, _ = _plain_path(ROOT / 'data/maintenance/instrument-intake', directory=True)
    if not run.is_relative_to(allowed) or run == allowed or run.exists():
        raise ValueError('Choose a new intake evidence directory')
    _plain_path(run.parent, directory=True)
    run.mkdir(exist_ok=False)
    code = run / 'code'
    inputs = [*sorted((ROOT / 'src/music_learning').rglob('*.py')), Path(__file__).resolve()]
    inventory = ROOT / 'data/maintenance/instrument-intake/20261021-fix/ample/inventory.json'
    inputs.append(inventory)
    pins = {}
    for original in inputs:
        target = code / original.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        expected = sha(original)
        shutil.copy2(original, target)
        if sha(target) != expected or sha(original) != expected:
            raise ValueError('Source changed while freezing Ample audit')
        pins[str(target)] = expected
    journals = {}
    for product, spec in PRODUCTS.items():
        original, _ = _plain_path(Path(r'H:\music\Sound Library') / spec['bank_root'] / 'preparation.json', directory=False)
        target = run / (product + '-preparation.json')
        expected = sha(original)
        shutil.copy2(original, target)
        if sha(target) != expected or sha(original) != expected:
            raise ValueError('Ample preparation changed during freeze')
        pins[str(target)] = expected
        journals[product] = str(target)
    save(run / 'pins.json', dict(files=pins))
    plan = dict(schema_version=1, repo_root=str(ROOT), run_root=str(run), code_root=str(code),
                inventory=str(code / inventory.relative_to(ROOT)), inventory_sha256=sha(inventory),
                journals=journals, pins_sha256=sha(run / 'pins.json'), max_seconds=24*3600,
                resource_root=r'H:\music\Sound Library')
    save(run / 'plan.json', plan)
    record = dict(status='prepared', plan=str(run / 'plan.json'), plan_sha256=sha(run / 'plan.json'))
    if start:
        import psutil
        command = [sys.executable, str(code / Path(__file__).resolve().relative_to(ROOT)),
                   '--execute-plan', record['plan'], '--plan-sha256', record['plan_sha256']]
        with (run / 'stdout.log').open('wb') as out, (run / 'stderr.log').open('wb') as err:
            process = subprocess.Popen(command, cwd=ROOT, stdout=out, stderr=err,
                creationflags=subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS)
        actual = psutil.Process(process.pid)
        record.update(status='started', pid=process.pid, creation_time=actual.create_time(), command=command)
        save(ROOT / 'runtime/ample-resource-audit-job.local.json', record)
    save(run / 'launch.json', record)
    return record


def execute(plan_path, expected_sha):
    if sha(plan_path) != expected_sha:
        raise ValueError('Ample audit plan digest differs')
    plan = load(plan_path)
    repo, run, code = (Path(plan[k]) for k in ('repo_root', 'run_root', 'code_root'))
    if (repo != Path(r'E:\Intrest\music-learning') or not run.is_relative_to(repo / 'data/maintenance/instrument-intake')
            or run != Path(plan_path).absolute().parent or code != run / 'code'
            or plan['resource_root'] != r'H:\music\Sound Library'):
        raise ValueError('Ample audit plan escaped its authorized roots')
    sys.path.insert(0, str(code / 'src'))
    import psutil
    from music_learning.ample_resource_audit import audit_bank
    from music_learning.instrument_probe_budget import loading_budget
    from music_learning.kontakt_runner import _queue_lock
    if sha(run / 'pins.json') != plan['pins_sha256']:
        raise ValueError('Ample audit pin manifest differs')
    for path, expected in load(run / 'pins.json')['files'].items():
        if sha(path) != expected:
            raise ValueError('Ample audit frozen input changed: ' + path)
    if sha(plan['inventory']) != plan['inventory_sha256']:
        raise ValueError('Ample inventory digest differs')
    state = dict(status='starting', pid=os.getpid(), creation_time=psutil.Process().create_time(),
                 products={}, catalog_written=False, formal_project_written=False)
    deadline = time.monotonic() + plan['max_seconds']

    def update(**fields):
        state.update(fields, updated_at_utc=datetime.now(timezone.utc).isoformat())
        save(run / 'progress.json', state)

    try:
        with _queue_lock(run / 'resource-audit'):
            inventory = load(plan['inventory'])
            for product, journal in plan['journals'].items():
                output = run / (product + '-result.json')
                checkpoint = run / (product + '-checkpoint.json')
                while time.monotonic() < deadline:
                    budget = loading_budget('H:')
                    if not budget['allowed']:
                        update(status='waiting_storage', current=product, storage_budget=budget)
                        time.sleep(20)
                        continue
                    update(status='auditing', current=product)
                    result = audit_bank(load(journal), inventory, product, checkpoint,
                        inventory_sha256=plan['inventory_sha256'], allowed_library_root=Path(plan['resource_root']))
                    state['products'][product] = {k: result[k] for k in
                        ('status', 'prepared_files_audited', 'prepared_file_count', 'audited_total_bytes', 'failures') if k in result}
                    update(last_result_status=result['status'])
                    if result['status'] == 'deferred_io':
                        time.sleep(20)
                        continue
                    save(output, result)
                    break
                else:
                    raise TimeoutError('Ample audit duration budget expired; checkpoints preserved')
            update(status='completed' if all(x['status']=='audited' for x in state['products'].values()) else 'completed_with_failures', current=None)
    except Exception as error:
        update(status='failed', error=repr(error))
        return 1
    return 0 if state['status'] == 'completed' else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path)
    parser.add_argument('--start', action='store_true')
    parser.add_argument('--execute-plan', type=Path)
    parser.add_argument('--plan-sha256')
    args = parser.parse_args()
    if args.execute_plan:
        if not args.plan_sha256:
            parser.error('--plan-sha256 is required')
        sys.exit(execute(args.execute_plan, args.plan_sha256))
    if not args.run:
        parser.error('--run is required')
    print(json.dumps(prepare(args.run, start=args.start), ensure_ascii=True))
