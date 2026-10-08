"""Run every supported browser workflow with an isolated database per viewport/spec."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
results = []
with tempfile.TemporaryDirectory(prefix='clubsp-browser-check-') as temp:
    for spec in sorted(root.glob('tests/*browser.spec.js')):
        for project in ('desktop', 'mobile'):
            log = Path(temp) / (spec.stem + '-' + project + '.json')
            with log.open('w') as out:
                result = subprocess.run([str(root / 'node_modules/.bin/playwright'), 'test',
                    '--config=playwright.validation.config.js', str(spec.relative_to(root)),
                    '--project=' + project, '--reporter=json'],cwd=root,stdout=out,stderr=subprocess.DEVNULL)
            try:
                report = json.loads(log.read_text())
                stats = report['stats']
            except Exception:
                stats = {'unexpected': 1, 'error': 'Runner did not return a valid report'}
            row = {'spec': spec.name,'project': project,'exit_code': result.returncode,**stats}
            results.append(row)
            print(json.dumps(row),flush=True)
            if result.returncode:
                (root / ('validation-failure-' + spec.stem + '-' + project + '.json')).write_text(log.read_text())
print(json.dumps({'summary': {'passed': sum(r.get('expected',0) for r in results),
    'unexpected': sum(r.get('unexpected',0) for r in results),'runs':len(results)}}),flush=True)
sys.exit(any(r['exit_code'] for r in results))
