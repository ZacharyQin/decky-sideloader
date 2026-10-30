"""Different-version self-update artifacts and strict external native lifecycle boundary."""
import asyncio
from collections import Counter
import io
import json
from pathlib import Path
import shutil
from urllib.parse import unquote, urlparse
import zipfile

from fixture_assets import HERE, digest, write_bytes
from http_fixture import HttpFixture

ROOT = HERE.parent
SPECS = json.loads((HERE / 'fixtures/self-update.json').read_text())
LATEST = 'https://api.github.com/repos/ZacharyQin/decky-sideloader/releases/latest'
NOTE = b'self update must preserve this private runtime note\n'


def self_case(name):
    rows = [row for row in SPECS['cases'] if row['id'] == name]
    if len(rows) != 1:
        raise ValueError('Unknown self-update scenario')
    return {**rows[0], 'source': 'download', 'self_flow': True, 'native': 'success', 'package': 'self'}


def candidate(root):
    root = root.resolve()
    if not root.is_relative_to(ROOT / 'test-results') or root.is_symlink():
        raise ValueError('Self-update candidate must be in the owned test-results tree')
    marker = root / '.sideloader-e2e-candidate.json'
    data = json.loads(marker.read_text())
    archive = root / data['archive']
    if (data.get('schema') != 1 or data.get('source_root') != str(ROOT) or not data.get('complete') or
            archive.is_symlink() or digest(archive.read_bytes()) != data['archive_sha256'] or
            digest((root / 'bin/sideloader-core').read_bytes()) != data['binary_sha256']):
        raise ValueError('Candidate input identities do not match built files')
    return data, archive.read_bytes()


class SelfHttp(HttpFixture):
    def __init__(self, case, info, data):
        self.routes, self.required, self.status_scripts = {}, {}, {}
        self.counts, self.errors, self.clients = Counter(), [], set()
        self.server = None
        self.asset = ('https://github.com/ZacharyQin/decky-sideloader/releases/download/v' + info['version'] + '/' + info['archive'])
        self.assets = {self.asset}
        self.routes[self.asset] = data
        self.required[self.asset] = 1
        self.routes[LATEST] = json.dumps({'draft': False, 'prerelease': False, 'tag_name': 'v' + info['version'],
            'name': 'Fixed executable update ' + info['version'], 'body': 'Different executable and frontend version',
            'published_at': '2020-01-01T00:00:00Z', 'assets': [{'id': 731, 'name': info['archive'],
            'browser_download_url': self.asset, 'size': len(data),
            'digest': 'sha256:' + ('f' * 64 if case['action'] == 'bad_digest' else info['archive_sha256'])}]}).encode()
        self.required[LATEST] = 2
        if case['action'] == 'download_error':
            self.status_scripts[self.asset] = [503]
        self.gates = {self.asset: {'entered': asyncio.Event(), 'released': asyncio.Event()}} if case.get('gate') else {}
        self.allowed_aborts = {self.asset} if case.get('gate') else set()
        self.aborted = []


class SelfFixture:
    methods = {'__native_request__', '__native_confirm__', '__native_cancel__', '__self_metadata__'}

    def __init__(self, home, case, info, data):
        self.home, self.case, self.info, self.data = home, case, info, data
        self.pending, self.confirmed, self.cancelled = None, False, False
        write_bytes(home / 'data/decky-sideloader/private-user-note.bin', NOTE)

    @property
    def metadata(self):
        return {'self_flow': self.case, 'candidate': self.info, 'pending': self.pending,
                'confirmed': self.confirmed, 'cancelled': self.cancelled, 'note_sha256': digest(NOTE)}

    async def control(self, method, args, record):
        if method == '__self_metadata__':
            return self.metadata
        if method == '__native_request__':
            if self.pending or self.confirmed or not self.case['dispatches']:
                raise ValueError('Duplicate or unexpected self-update dispatch')
            uri, name, version, checksum, action = args
            parsed = urlparse(uri)
            path = Path(unquote(parsed.path))
            base = self.home / 'data/decky-sideloader/self-update'
            if (parsed.scheme != 'file' or path.name != 'plugin.zip' or path.parent.parent != base or
                    len(path.parent.name) != 32 or any(c not in '0123456789abcdef' for c in path.parent.name) or
                    path.is_symlink() or path.read_bytes() != self.data or
                    (name, version, checksum, action) != ('Sideloader', self.info['version'], self.info['archive_sha256'], 2)):
                raise ValueError('Self-update native request does not match independent candidate identity')
            request = 'self-native-' + path.parent.name
            self.pending = {'id': path.parent.name, 'request_id': request, 'artifact': str(path)}
            record('dispatch', operation=path.parent.name, args=args)
            return {'name': name, 'version': version, 'request_id': request, 'sha256': checksum, 'mode': 'success'}
        if not self.pending or args != [self.pending['request_id']]:
            raise ValueError('Unknown self-update native request')
        if method == '__native_cancel__':
            if not self.case['cancels'] or self.cancelled:
                raise ValueError('Duplicate or unconfigured self-update cancellation')
            record('cancel', operation=self.pending['id'], request=args[0])
            self.cancelled = True
            return None
        if method == '__native_confirm__':
            if not self.case['confirms'] or self.confirmed or Path(self.pending['artifact']).read_bytes() != self.data:
                raise ValueError('Duplicate, changed or unconfigured self-update confirmation')
            program = self.home / 'plugins/decky-sideloader'
            staged = self.home / '.self-update-native-stage'
            previous = self.home / '.self-update-native-previous'
            if staged.exists() or previous.exists() or program.is_symlink():
                raise ValueError('Unexpected owned native staging path')
            expected = {'LICENSE', 'bin/sideloader-core', 'data/repositories.json', 'dist/backend.json',
                        'dist/build.json', 'dist/index.js', 'dist/notices.json', 'main.py', 'package.json', 'plugin.json'}
            with zipfile.ZipFile(io.BytesIO(self.data)) as archive:
                if set(archive.namelist()) != {'decky-sideloader/' + name for name in expected} or archive.testzip() is not None:
                    raise ValueError('Self-update runtime package member mismatch')
                for entry in archive.infolist():
                    relative = entry.filename.removeprefix('decky-sideloader/')
                    path = staged / relative
                    write_bytes(path, archive.read(entry))
                    path.chmod((entry.external_attr >> 16) & 0o777)
            if (digest((staged / 'bin/sideloader-core').read_bytes()) != self.info['binary_sha256'] or
                    json.loads((staged / 'package.json').read_text())['version'] != self.info['version']):
                raise ValueError('Native staged executable does not match candidate')
            program.rename(previous)
            staged.rename(program)
            shutil.rmtree(previous)
            if (self.home / 'data/decky-sideloader/private-user-note.bin').read_bytes() != NOTE:
                raise ValueError('Self-update changed protected runtime data')
            record('confirm', operation=self.pending['id'], request=args[0])
            self.confirmed = True
            return {'name': 'Sideloader', 'mode': 'success'}
        raise ValueError('Unknown self-update fixture control')
