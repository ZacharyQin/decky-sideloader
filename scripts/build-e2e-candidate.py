"""Build a different, executable Sideloader version in an owned non-distributed workspace."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

from backend_build import source_fingerprint

ROOT = Path(__file__).resolve().parents[1]
MARKER = '.sideloader-e2e-candidate.json'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_candidate(output):
    output = output.resolve()
    if not output.is_relative_to(ROOT / 'test-results') or output == ROOT / 'test-results':
        raise ValueError('Candidate must be in an owned test-results subdirectory')
    package = json.loads((ROOT / 'package.json').read_text())
    source_version = package['version']
    parts = source_version.split('.')
    if len(parts) != 3 or not all(part.isdecimal() for part in parts):
        raise ValueError('Candidate source version must have three numeric components')
    target_version = '.'.join([*parts[:2], str(int(parts[2]) + 1)])
    files = [ROOT / path for path in ('package.json', 'pnpm-lock.yaml', 'rollup.config.js', 'plugin.json',
        'LICENSE', 'backend/main.py', 'scripts/backend_build.py', 'scripts/build-identity.mjs',
        'scripts/license-notices.mjs', 'scripts/package-plugin.py', 'scripts/build-e2e-candidate.py')]
    files += [path for directory in ('src', 'data', 'licenses') for path in (ROOT / directory).rglob('*') if path.is_file()]
    inputs = {'backend': source_fingerprint(), 'files': {str(path.relative_to(ROOT)): sha(path) for path in sorted(files)}}
    fingerprint = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
    identity = {'schema': 1, 'source_root': str(ROOT), 'source_version': source_version,
                'version': target_version, 'inputs': fingerprint}
    marker = output / MARKER
    if output.exists():
        if output.is_symlink() or not marker.is_file() or marker.is_symlink():
            raise ValueError('Refusing to replace a directory not created by the candidate builder')
        previous = json.loads(marker.read_text())
        if previous.get('schema') != 1 or previous.get('source_root') != str(ROOT):
            raise ValueError('Candidate workspace ownership marker does not match')
        if all(previous.get(key) == value for key, value in identity.items()) and previous.get('complete'):
            archive = output / previous['archive']
            program = output / 'bin/sideloader-core'
            if (archive.is_file() and not archive.is_symlink() and program.is_file() and
                    sha(archive) == previous.get('archive_sha256') and sha(program) == previous.get('binary_sha256')):
                return {**previous, 'root': str(output), 'cached': True}
        shutil.rmtree(output)
    output.mkdir(parents=True)
    marker.write_text(json.dumps({**identity, 'complete': False}, indent=2) + '\n')
    for directory in ('src', 'scripts', 'data', 'licenses'):
        shutil.copytree(ROOT / directory, output / directory)
    for filename in ('package.json', 'pnpm-lock.yaml', 'rollup.config.js', 'plugin.json', 'LICENSE', 'tsconfig.json'):
        shutil.copyfile(ROOT / filename, output / filename)
    (output / 'backend').mkdir()
    shutil.copyfile(ROOT / 'backend/main.py', output / 'backend/main.py')
    (output / 'backend-rs').mkdir()
    shutil.copytree(ROOT / 'backend-rs/src', output / 'backend-rs/src')
    for filename in ('Cargo.toml', 'Cargo.lock', 'build.rs'):
        shutil.copyfile(ROOT / 'backend-rs' / filename, output / 'backend-rs' / filename)
    (output / 'node_modules').symlink_to(ROOT / 'node_modules', target_is_directory=True)
    package['version'] = target_version
    (output / 'package.json').write_text(json.dumps(package, indent=2) + '\n')
    manifest = output / 'backend-rs/Cargo.toml'
    before = manifest.read_text()
    pattern = 'version = "' + source_version + '"'
    if before.count(pattern) != 1:
        raise ValueError('Candidate Cargo package version is ambiguous')
    manifest.write_text(before.replace(pattern, 'version = "' + target_version + '"'))
    lock = output / 'backend-rs/Cargo.lock'
    before = lock.read_text()
    pattern = 'name = "sideloader-core"\nversion = "' + source_version + '"'
    if before.count(pattern) != 1:
        raise ValueError('Candidate lock package version is ambiguous')
    lock.write_text(before.replace(pattern, 'name = "sideloader-core"\nversion = "' + target_version + '"'))
    manifest.write_text(manifest.read_text() + '\n[[bin]]\nname = "sideloader-update-fixture"\npath = "src/main.rs"\n')
    build_script = output / 'scripts/backend_build.py'
    script = build_script.read_text()
    target_filter = 'item.get("target", {}).get("name") == "sideloader-core"'
    if script.count(target_filter) != 1:
        raise ValueError('Candidate build executable selector is ambiguous')
    build_script.write_text(script.replace(target_filter, 'item.get("target", {}).get("name") == "sideloader-update-fixture"'))
    target = os.environ.get('SIDELOADER_RUST_TARGET', 'x86_64-unknown-linux-gnu')
    original_programs = [ROOT / 'bin/sideloader-core', ROOT / 'backend-rs/target' / target / 'release/sideloader-core']
    original_hashes = {path: sha(path) for path in original_programs if path.is_file()}
    environment = os.environ.copy()
    environment['CARGO_NET_OFFLINE'] = 'true'
    environment['CARGO_TARGET_DIR'] = str(ROOT / 'backend-rs/target')
    subprocess.run(['pnpm', 'build'], cwd=output, env=environment, check=True)
    if any(sha(path) != before for path, before in original_hashes.items()):
        raise ValueError('Candidate build changed an existing source-version executable')
    program = output / 'bin/sideloader-core'
    if b'.sideloader-e2e-origin' in program.read_bytes():
        raise ValueError('Candidate unexpectedly contains private test routing')
    executable = json.loads(subprocess.check_output([str(program), '--identity'], env=environment, text=True))
    backend = json.loads((output / 'dist/backend.json').read_text())
    frontend = json.loads((output / 'dist/build.json').read_text())
    if (executable['version'] != target_version or backend['version'] != target_version or
            executable['sha256'] != sha(program) or backend['sha256'] != sha(program) or
            executable['fingerprint'] != backend['fingerprint'] or frontend['version'] != target_version or
            frontend['bundle'] != sha(output / 'dist/index.js')):
        raise ValueError('Candidate executable and frontend identities do not match their built files')
    archive = output / ('decky-sideloader-' + target_version + '.zip')
    environment['SIDELOADER_PACKAGE_OUTPUT'] = str(archive)
    subprocess.run(['python3', '-B', 'scripts/package-plugin.py'], cwd=output, env=environment, check=True)
    result = {**identity, 'complete': True, 'archive': archive.name, 'archive_sha256': sha(archive),
              'binary_sha256': sha(program), 'backend': backend, 'frontend': frontend}
    marker.write_text(json.dumps(result, indent=2) + '\n')
    return {**result, 'root': str(output), 'cached': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT / 'test-results/self-update-candidate')
    args = parser.parse_args()
    print(json.dumps(build_candidate(args.output)))
