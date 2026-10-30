"""Run local verification without publishing or contacting application services."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
environment = os.environ.copy()
environment.setdefault('CARGO_NET_OFFLINE', 'true')
commands = [
    ['pnpm', 'typecheck'], ['pnpm', 'test'],
    ['cargo', 'fmt', '--check', '--manifest-path', 'backend-rs/Cargo.toml'],
    ['cargo', 'clippy', '--locked', '--all-targets', '--manifest-path', 'backend-rs/Cargo.toml', '--', '-D', 'warnings'],
    ['cargo', 'clippy', '--locked', '--features', 'e2e-process-fixture', '--test', 'e2e-process', '--manifest-path', 'backend-rs/Cargo.toml', '--', '-D', 'warnings'],
    ['pnpm', 'test:rust'], ['pnpm', 'test:python'], ['pnpm', 'test:reference'], ['pnpm', 'test:licenses'],
]


def run(args, env=environment):
    start = time.monotonic()
    print('\n>>> ' + ' '.join(args), flush=True)
    subprocess.run(args, cwd=ROOT, env=env, check=True)
    print('<<< %.2fs' % (time.monotonic() - start), flush=True)


with tempfile.TemporaryDirectory(prefix='sideloader-test-all-') as temp:
    temporary = Path(temp)
    dist = ROOT / 'dist'
    preserved = []
    for path in dist.glob('*.zip') if dist.is_dir() else ():
        if path.is_symlink():
            raise RuntimeError('refusing to rebuild with a symlinked ZIP in dist: ' + str(path))
        if path.is_file():
            copy = temporary / path.name
            shutil.copyfile(path, copy)
            preserved.append((path, copy))
    try:
        for command in commands:
            run(command)
        run(['pnpm', 'build'])
        report = environment.get('SIDELOADER_E2E_REPORT')
        run(['pnpm', 'test:e2e', *(['--report', report] if report else [])])
        version = json.loads((ROOT / 'package.json').read_text())['version']
        package = temporary / f'decky-sideloader-{version}.zip'
        run(['pnpm', 'package-plugin'], {**environment, 'SIDELOADER_PACKAGE_OUTPUT': str(package)})
        with zipfile.ZipFile(package) as archive:
            expected = {'decky-sideloader/' + name for name in (
                'LICENSE', 'plugin.json', 'package.json', 'main.py', 'data/repositories.json',
                'bin/sideloader-core', 'dist/index.js', 'dist/build.json', 'dist/backend.json', 'dist/notices.json')}
            if archive.testzip() is not None or set(archive.namelist()) != expected or len(archive.namelist()) != len(expected):
                raise SystemExit('package member/CRC check failed')
            binary = archive.read('decky-sideloader/bin/sideloader-core')
            metadata = json.loads(archive.read('decky-sideloader/dist/backend.json'))
            if any(marker in binary for marker in (b'.sideloader-e2e-origin', b'.sideloader-e2e-share-addresses', b'.sideloader-e2e-backup-gate')) or hashlib.sha256(binary).hexdigest() != metadata['sha256']:
                raise SystemExit('distributed runtime identity/fixture exclusion check failed')
            if binary != (ROOT / 'bin/sideloader-core').read_bytes():
                raise SystemExit('package runtime differs from the verified release artifact')
            print(json.dumps({'packageVerification':'passed', 'members':len(expected),
                              'privateFixtureExcluded':True, 'runtimeSHA256':metadata['sha256']}), flush=True)
    finally:
        for destination, saved in preserved:
            if not destination.exists():
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(saved, destination)
            elif destination.read_bytes() != saved.read_bytes():
                backup = destination.with_name(destination.name + '.pre-test-backup')
                with backup.open('xb') as stream:
                    stream.write(saved.read_bytes())
                raise RuntimeError('existing ZIP unexpectedly changed; saved copy: ' + str(backup))
