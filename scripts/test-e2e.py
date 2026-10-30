"""Build the private cfg(test) IPC driver, then run deterministic application E2E."""
import json
import os
from pathlib import Path
import subprocess
import sys

from backend_build import source_fingerprint

ROOT = Path(__file__).resolve().parents[1]
MARKERS = (b'.sideloader-e2e-origin', b'.sideloader-e2e-share-addresses', b'.sideloader-e2e-backup-gate')
program = ROOT / 'bin/sideloader-core'
if not program.is_file() or not (ROOT / 'dist/build.json').is_file():
    raise SystemExit('Run pnpm build before test:e2e')
if any(marker in program.read_bytes() for marker in MARKERS):
    raise SystemExit('Private fixture configuration unexpectedly present in the distributed binary')
backend = json.loads((ROOT / 'dist/backend.json').read_text())
if backend.get('fingerprint') != source_fingerprint():
    raise SystemExit('Stale Rust runtime: run pnpm build before test:e2e')
environment = os.environ.copy()
environment.setdefault('CARGO_NET_OFFLINE', 'true')
command = ['cargo', 'build', '--locked', '--manifest-path', str(ROOT / 'backend-rs/Cargo.toml'),
           '--features', 'e2e-process-fixture', '--test', 'e2e-process', '--message-format=json']
worker = subprocess.Popen(command, cwd=ROOT, env=environment, stdout=subprocess.PIPE, text=True)
executable = None
for line in worker.stdout:
    message = json.loads(line)
    if message.get('reason') == 'compiler-message':
        print(message['message'].get('rendered', ''), file=sys.stderr, end='')
    if (message.get('reason') == 'compiler-artifact' and
            message['target']['name'] == 'e2e-process' and message.get('executable')):
        executable = Path(message['executable']).resolve()
if worker.wait() != 0 or not executable or not executable.is_relative_to(ROOT / 'backend-rs/target'):
    raise SystemExit('Private E2E driver build failed')
if any(marker not in executable.read_bytes() for marker in MARKERS):
    raise SystemExit('Private driver adapter missing')
print(json.dumps({'fixtureDriver':'Cargo cfg(test), production entry/Engine',
                  'distributedBinaryFixtureMarker':False}), flush=True)
candidate_args = []
selected = sys.argv[sys.argv.index('--case') + 1] if '--case' in sys.argv else None
self_names = {row['id'] for row in json.loads((ROOT / 'e2e/fixtures/self-update.json').read_text())['cases']}
if selected is None or selected in self_names:
    import importlib.util
    spec = importlib.util.spec_from_file_location('sideloader_candidate_builder', ROOT / 'scripts/build-e2e-candidate.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    candidate = module.build_candidate(ROOT / 'test-results/self-update-candidate')
    candidate_args = ['--self-candidate', candidate['root']]
    print(json.dumps({'candidateVersion': candidate['version'], 'candidateBinary': candidate['binary_sha256'],
                      'candidateFrontend': candidate['frontend']['fingerprint'], 'candidateCached': candidate['cached']}), flush=True)
result = subprocess.run(['node', '--experimental-vm-modules', 'e2e/app.mjs',
                         '--fixture-core', str(executable), *candidate_args, *sys.argv[1:]], cwd=ROOT, env=environment)
raise SystemExit(result.returncode)
