"""Read-only comparison with N1's formal-project and fixed-runtime baseline."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    output=args.output.resolve()
    output.relative_to(ROOT/'data/maintenance/native-engine')
    if output.exists():
        raise ValueError('Use a new evidence filename')
    baseline=json.loads((ROOT/'data/maintenance/native-engine/n1/before.json').read_text(encoding='utf-8'))
    formal={}
    with sqlite3.connect((ROOT/'data/app-data/projects.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        for table in baseline['formal']:
            if table=='heads':
                formal[table]=[list(row) for row in db.execute('select id,current_revision from projects order by id')]
            else:
                rows=sorted(db.execute('SELECT * FROM '+table).fetchall(),key=repr)
                sha=hashlib.sha256(json.dumps(rows,sort_keys=True,ensure_ascii=False,default=str).encode()).hexdigest()
                formal[table]=dict(count=len(rows),sha256=sha)
    pins={}
    for path in baseline['pins']:
        with Path(path).open('rb') as stream:
            pins[path]=hashlib.file_digest(stream,'sha256').hexdigest()
    result=dict(formal=formal,pins=pins,formal_equal=formal==baseline['formal'],pins_equal=pins==baseline['pins'])
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(formal_equal=result['formal_equal'],pins_equal=result['pins_equal'])))
    if not result['formal_equal'] or not result['pins_equal']:
        raise SystemExit(1)


if __name__=='__main__':
    main()
