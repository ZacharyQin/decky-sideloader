"""Deterministic, independently hashed input artifacts for application E2E."""
import hashlib
import io
import json
from pathlib import Path
import stat
import zipfile

HERE = Path(__file__).resolve().parent
INPUTS = json.loads((HERE / 'fixtures/install.json').read_text())
HASHES = json.loads((HERE / 'fixtures/install-hashes.json').read_text())


def digest(data):
    return hashlib.sha256(data).hexdigest()


def archive_bytes(package):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_STORED) as archive:
        for relative, text in sorted(package['files'].items()):
            info = zipfile.ZipInfo(package['folder'] + '/' + relative, (2020, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, text.encode())
    return buffer.getvalue()


def scenario(name):
    matches = [row for row in INPUTS['cases'] + INPUTS['downloads'] if row['id'] == name]
    if len(matches) != 1:
        raise ValueError('Unknown fixture scenario')
    return matches[0]


def checked_package(kind):
    package = INPUTS['packages'][kind]
    expected = HASHES['packages'][kind]
    actual = {name: digest(text.encode()) for name, text in package['files'].items()}
    if actual != expected['files']:
        raise ValueError('Fixture content changed without reviewed hash update')
    archive = archive_bytes(package)
    if digest(archive) != expected['archive']:
        raise ValueError('Fixture archive changed without reviewed hash update')
    return package, expected, archive


def write_bytes(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def stamp(home, folder):
    """Serialize fixture inputs in the public durable-record format, not an oracle."""
    root = home / 'plugins' / folder
    if not (root / 'package.json').is_file():
        return None
    rows = []
    for relative in ('package.json', 'plugin.json', 'dist/index.js', 'main.py'):
        path = root / relative
        if not path.is_file():
            rows.append([relative, None])
            continue
        metadata = path.stat()
        rows.append([relative, metadata.st_ino, metadata.st_mtime_ns, metadata.st_ctime_ns,
                     metadata.st_size, digest(path.read_bytes())])
    return rows


def seed_install(home, case):
    package, expected, archive = checked_package(case['package'])
    identifier = digest(case['id'].encode())[:32]
    runtime = home / 'data/decky-sideloader'
    directory = runtime / 'installations' / identifier
    directory.mkdir(parents=True)
    artifact = directory / 'plugin.zip'
    artifact.write_bytes(archive)
    backup = None
    if case['package'] == 'overwrite':
        backup_path = directory / 'data-backup.zip'
        with zipfile.ZipFile(backup_path, 'w', compression=zipfile.ZIP_STORED) as output:
            for relative, text in sorted(INPUTS['protected'].items()):
                output.writestr(relative, text)
            output.writestr('backup-manifest.json', '{"fixture":true,"files":2}\n')
        backup = {'files': len(INPUTS['protected']), 'bytes': sum(len(x.encode()) for x in INPUTS['protected'].values()),
                  'created_at': '2020-01-01T00:00:00Z', 'paths': [str(home / 'settings/example'), str(home / 'data/example')],
                  'archive': str(backup_path), 'scope': 'Fixture settings and data', 'sha256': digest(backup_path.read_bytes())}
    record = {
        'id': identifier, 'stage': 'ready', 'repo': package['repo'], 'tag': 'v2.0.0',
        'asset_url': 'https://github.com/' + package['repo'] + '/releases/download/v2.0.0/plugin.zip',
        'existing_name': 'Example' if case['package'] == 'overwrite' else None,
        'created_at': '2020-01-01T00:00:00Z', 'error': None, 'backup': backup,
        'artifact': artifact.as_uri(), 'directory': str(directory), 'request_id': None,
        'previous_stamp': stamp(home, package['folder']), 'mode': 'install', 'outcome': None,
        'saw_start': False, 'saw_finish': False, 'binding_saved': False, 'cancel_requested': False,
        'network_mode': 'direct', 'observations': [],
        'download': {'status': 'complete', 'received': len(archive), 'total': len(archive), 'route': 'direct', 'attempt': 1},
        'dependencies': {'count': 2, 'names': ['dependency-a', 'dependency-b']},
        'package': {'name': package['name'], 'folder': package['folder'], 'version': package['version'],
                    'sha256': expected['archive'], 'hashes': expected['files'], 'warnings': []},
    }
    (runtime / 'installations/operation.json').write_text(json.dumps(record))
    if case.get('tamper') == 'archive':
        with artifact.open('ab') as output:
            output.write(b'changed-after-validation')
    elif case.get('tamper') == 'backup':
        with (directory / 'data-backup.zip').open('ab') as output:
            output.write(b'changed-after-backup')
    return {'operation_id': identifier, 'request_id': 'fixture-native-' + identifier,
            'package': package, 'hashes': expected['files'], 'archive_sha256': expected['archive'],
            'artifact': str(artifact), 'backup_sha256': backup['sha256'] if backup else None,
            'protected_hashes': HASHES['protected']}


def apply_native(home, fixture, tamper=False):
    archive = Path(fixture['artifact'])
    if digest(archive.read_bytes()) != fixture['archive_sha256']:
        raise ValueError('Native fixture refused changed archive')
    package = fixture['package']
    with zipfile.ZipFile(archive) as source:
        expected_names = {package['folder'] + '/' + name for name in fixture['hashes']}
        if set(source.namelist()) != expected_names or source.testzip() is not None:
            raise ValueError('Native fixture archive member mismatch')
        for name in source.namelist():
            relative = Path(name)
            if relative.is_absolute() or '..' in relative.parts:
                raise ValueError('Native fixture refused unsafe path')
            write_bytes(home / 'plugins' / relative, source.read(name))
    if tamper:
        write_bytes(home / 'plugins' / package['folder'] / 'bin/dependency-b', b'CORRUPTED-FIXTURE-DEPENDENCY')
