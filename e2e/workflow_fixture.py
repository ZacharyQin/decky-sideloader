"""Fixed version workflows: only external GitHub and native Loader boundaries are fixtures."""
import asyncio
import base64
from collections import Counter
import hashlib
import io
import json
from pathlib import Path
import shutil
from urllib.parse import unquote, urlparse
import zipfile

from fixture_assets import HERE, INPUTS, archive_bytes, digest, write_bytes
from http_fixture import HttpFixture

WORKFLOWS = json.loads((HERE / 'fixtures/workflows.json').read_text())
HASHES = json.loads((HERE / 'fixtures/workflow-hashes.json').read_text())
RELEASES = 'https://api.github.com/repos/fixture/example/releases?per_page=100&page=1'


def workflow_case(name):
    matches = [row for row in WORKFLOWS['cases'] if row['id'] == name]
    if len(matches) != 1:
        raise ValueError('Unknown workflow')
    return {**matches[0], 'source': 'download', 'workflow': True, 'native': 'success', 'package': 'overwrite'}


def checked(version):
    package = WORKFLOWS['packages'][version]
    expected = HASHES['packages'][version]
    if {name: digest(text.encode()) for name, text in package['files'].items()} != expected['files']:
        raise ValueError('Workflow input changed without reviewed hashes')
    data = archive_bytes(package)
    if digest(data) != expected['archive']:
        raise ValueError('Workflow archive changed without reviewed hashes')
    return package, expected, data


class WorkflowHttp(HttpFixture):
    def __init__(self, case):
        self.routes, self.required, self.status_scripts = {}, {}, {}
        self.counts, self.errors, self.clients = Counter(), [], set()
        self.server = None
        prefix = {'gh-proxy': 'https://gh-proxy.org/', 'ghfast': 'https://ghfast.top/'}.get(case.get('network'), '')
        query_prefix = '' if case.get('network') == 'ghfast' else prefix
        self.assets = set()
        releases = []
        for version in ('2.0.0', '1.0.0', '0.5.0'):
            package, expected, data = checked(version)
            asset = 'https://github.com/fixture/example/releases/download/v' + version + '/plugin.zip'
            releases.append({'draft': False, 'prerelease': False, 'tag_name': 'v' + version, 'name': version,
                'published_at': {'2.0.0': '2022', '1.0.0': '2021', '0.5.0': '2020'}[version] + '-01-01T00:00:00Z',
                'body': 'Fixed version workflow', 'assets': [{'name': 'plugin.zip', 'browser_download_url': asset,
                'size': len(data), 'digest': 'sha256:' + expected['archive']}]})
            routed = prefix + asset
            self.assets.add(routed)
            self.routes[routed] = data
            self.required[routed] = case['downloads'].get(version, 0)
            probes = case['probes'].get(version, 0)
            self.required[routed + ';range=bytes=-65557'] = probes
            with zipfile.ZipFile(io.BytesIO(data)) as source:
                entry = source.getinfo('example/package.json')
                offset = entry.header_offset
                content = offset + 30 + len(entry.filename.encode()) + len(entry.extra)
                for start, end in ((offset, offset + 29), (offset + 30, content - 1), (content, content + entry.compress_size - 1)):
                    self.required[routed + f';range=bytes={start}-{end}'] = probes
            if case['action'] == 'retry' and version == '2.0.0':
                self.status_scripts[routed] = [503, 200]
        self.asset = next(iter(self.assets))
        self.routes[query_prefix + RELEASES] = json.dumps(releases).encode()
        self.required[query_prefix + RELEASES] = case['releases']
        if case.get('crash_download'):
            key = prefix + 'https://github.com/fixture/example/releases/download/v2.0.0/plugin.zip'
            self.gates = {key: {'entered': asyncio.Event(), 'released': asyncio.Event(), 'once': True, 'prefix': 64}}
            self.allowed_aborts, self.aborted = {key}, []
        if case.get('repo_probe'):
            tree = []
            base = 'https://api.github.com/repos/fixture/example/git/'
            package, _, _ = checked('2.0.0')
            for name in ('plugin.json', 'package.json'):
                data = package['files'][name].encode()
                key = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
                tree.append({'path': name, 'type': 'blob', 'mode': '100644', 'sha': key, 'size': len(data)})
                self.routes[query_prefix + base + 'blobs/' + key] = json.dumps({'encoding': 'base64', 'content': base64.b64encode(data).decode()}).encode()
                self.required[query_prefix + base + 'blobs/' + key] = 1
            self.routes[query_prefix + base + 'trees/HEAD?recursive=1'] = json.dumps({'sha': 'a' * 40, 'truncated': False, 'tree': tree}).encode()
            self.required[query_prefix + base + 'trees/HEAD?recursive=1'] = 1
        if case.get('store'):
            self.routes['https://plugins.deckbrew.xyz/plugins'] = b'[{"name":"Example"}]'
            self.required['https://plugins.deckbrew.xyz/plugins'] = 1
        if case.get('map'):
            mapping = json.loads((HERE.parent / 'data/repositories.json').read_text())
            mapping['revision'] += 1
            mapping['updated_at'] = '2026-10-30'
            mapping['repositories']['Example'] = 'fixture/mapped'
            url = 'https://raw.githubusercontent.com/ZacharyQin/decky-sideloader/main/data/repositories.json'
            self.routes[url] = json.dumps(mapping).encode()
            self.required[url] = 1
            self.routes['https://api.github.com/repos/fixture/fork/releases?per_page=100&page=1'] = b'[]'
            self.required['https://api.github.com/repos/fixture/fork/releases?per_page=100&page=1'] = 1


class WorkflowFixture:
    methods = {'__native_request__', '__native_confirm__', '__native_cancel__', '__native_uninstall__',
               '__repair_backup__', '__workflow_metadata__', '__inspect_backup__', '__store_versions__',
               '__frozen_update__', '__frozen_invalidate__', '__release_frozen_write__', '__change_identity__', '__release_uninstall__'}

    def __init__(self, home, case):
        self.home, self.case = home, case
        self.index, self.pending, self.completed, self.uninstalls = 0, None, [], 0
        self.store_reads, self.frozen_attempts, self.frozen_pending = 0, 0, False
        self.frozen_release = asyncio.Event()
        self.uninstall_pending = False
        self.uninstall_release = asyncio.Event()
        if case.get('store'):
            names = ['Other Fixture', 'Example'] if case.get('prior_frozen') else ['Other Fixture']
            (home / 'settings/loader.json').write_text(json.dumps({'frozenPlugins': names, 'fixtureOtherSetting': 'preserve-me'}))
        if case['action'] == 'continue':
            data = home / 'data/example'
            self.private = home / 'private-fixture-data'
            data.rename(self.private)
            data.symlink_to(self.private, target_is_directory=True)

    @property
    def metadata(self):
        return {'workflow': self.case, 'completed': self.completed, 'pending': self.pending,
                'uninstalls': self.uninstalls, 'store_reads': self.store_reads,
                'frozen_attempts': self.frozen_attempts, 'frozen_pending': self.frozen_pending,
                'uninstall_pending': self.uninstall_pending}

    async def control(self, method, args, record):
        if method == '__workflow_metadata__':
            return self.metadata
        if method == '__store_versions__':
            if (not self.case.get('store') or self.store_reads or
                    args != ['https://plugins.deckbrew.xyz/plugins/versions', 'GET', {'X-Decky-Version': 'fixture'}]):
                raise ValueError('Unknown or overconsumed native store-version request')
            self.store_reads += 1
            record('store_versions', args=args)
            return {'Example': ['2.0.0', 'fixed-store-version']}
        if method == '__frozen_invalidate__':
            if not self.case.get('store') or args:
                raise ValueError('Unexpected native freeze invalidation')
            record('frozen_invalidate')
            return None
        if method == '__release_frozen_write__':
            if self.case.get('ownership') != 'cross_instance' or not self.frozen_pending:
                raise ValueError('No owned native freeze write is pending')
            self.frozen_release.set()
            return None
        if method == '__frozen_update__':
            if not self.case.get('store') or len(args) != 1 or not isinstance(args[0], list):
                raise ValueError('Unexpected native freeze write')
            names = args[0]
            if len(names) != len(set(names)) or not {'Other Fixture'} <= set(names) <= {'Other Fixture', 'Example'}:
                raise ValueError('Freeze write changed unrelated policy')
            self.frozen_attempts += 1
            record('frozen_write', names=names)
            if self.case.get('ownership') == 'write_failure' and self.frozen_attempts == 1:
                record('frozen_write_failed')
                raise ValueError('Fixed native freeze write failure before applying')
            if self.case.get('ownership') == 'cross_instance' and self.frozen_attempts == 1:
                self.frozen_pending = True
                await asyncio.wait_for(self.frozen_release.wait(), 8)
                self.frozen_pending = False
            path = self.home / 'settings/loader.json'
            settings = json.loads(path.read_text())
            settings['frozenPlugins'] = names
            path.write_text(json.dumps(settings))
            record('frozen_applied', names=names)
            return None
        if method == '__change_identity__':
            if self.case.get('ownership') != 'identity' or args:
                raise ValueError('Identity change is not configured')
            path = self.home / 'plugins/example/package.json'
            value = json.loads(path.read_text())
            value['name'] = 'different-fixture-package'
            value['repository'] = 'fixture/other'
            path.write_text(json.dumps(value))
            record('identity_changed')
            return None
        if method == '__inspect_backup__':
            identifier, = args
            if len(identifier) != 32 or any(c not in '0123456789abcdef' for c in identifier):
                raise ValueError('Invalid owned backup identifier')
            path = self.home / 'data/decky-sideloader/installations' / identifier / 'data-backup.zip'
            if path.is_symlink():
                raise ValueError('Unsafe workflow backup')
            with zipfile.ZipFile(path) as archive:
                if archive.testzip() is not None:
                    raise ValueError('Corrupt actual backup')
                return {'sha256': digest(path.read_bytes()), 'mode': path.stat().st_mode & 0o777,
                    'manifest': json.loads(archive.read('backup-manifest.json')),
                    'files': {name: base64.b64encode(archive.read(name)).decode() for name in archive.namelist() if name != 'backup-manifest.json'},
                    'modes': {entry.filename: (entry.external_attr >> 16) & 0o777 for entry in archive.infolist()}}
        if method == '__repair_backup__':
            path = self.home / 'data/example'
            if self.case['action'] != 'continue' or not path.is_symlink() or path.resolve() != self.private:
                raise ValueError('No owned workflow backup fault to repair')
            path.unlink()
            self.private.rename(path)
            return None
        if method == '__release_uninstall__':
            if not self.case.get('hold_uninstall') or not self.uninstall_pending or args:
                raise ValueError('No owned native uninstall is pending')
            self.uninstall_release.set()
            return None
        if method == '__native_uninstall__':
            if args != ['Example'] or self.uninstalls >= self.case['uninstalls']:
                raise ValueError('Unexpected native uninstall')
            path = self.home / 'plugins/example'
            if not path.is_dir() or path.is_symlink():
                raise ValueError('Workflow program missing or unsafe before uninstall')
            if self.case.get('hold_uninstall'):
                self.uninstall_pending = True
                await asyncio.wait_for(self.uninstall_release.wait(), 8)
                self.uninstall_pending = False
            record('uninstall', name='Example')
            shutil.rmtree(path)
            self.uninstalls += 1
            return None
        if method == '__native_request__':
            if self.pending or self.index >= len(self.case['native_types']):
                raise ValueError('Duplicate or unconfigured workflow native dispatch')
            uri, name, version, checksum, action_type = args
            expected_version = self.case['targets'][self.index]
            package, expected, data = checked(expected_version)
            artifact = Path(unquote(urlparse(uri).path))
            base = self.home / 'data/decky-sideloader/installations'
            if (urlparse(uri).scheme != 'file' or artifact.name != 'plugin.zip' or artifact.parent.parent != base or
                    len(artifact.parent.name) != 32 or any(c not in '0123456789abcdef' for c in artifact.parent.name) or
                    artifact.is_symlink() or (name, version, checksum, action_type) !=
                    ('Example', expected_version, expected['archive'], self.case['native_types'][self.index]) or
                    artifact.read_bytes() != data):
                raise ValueError('Workflow native action differs from independent version/type/archive inputs')
            if artifact.parent.name in [row['operation'] for row in self.completed]:
                raise ValueError('Workflow redispatched a completed operation')
            request = 'workflow-native-' + artifact.parent.name
            self.pending = {'operation': artifact.parent.name, 'artifact': str(artifact), 'version': version,
                            'request_id': request, 'type': action_type}
            record('dispatch', operation=artifact.parent.name, args=args)
            return {'name': name, 'version': version, 'sha256': checksum, 'request_id': request, 'mode': 'success'}
        if method in ('__native_confirm__', '__native_cancel__'):
            if not self.pending or args != [self.pending['request_id']]:
                raise ValueError('Unknown workflow native request')
            if method == '__native_cancel__':
                record('cancel', operation=self.pending['operation'], request=args[0])
                self.pending = None
                return None
            package, expected, data = checked(self.pending['version'])
            if Path(self.pending['artifact']).read_bytes() != data:
                raise ValueError('Changed workflow archive at confirmation')
            program = self.home / 'plugins/example'
            if program.exists():
                if program.is_symlink():
                    raise ValueError('Unsafe workflow program path')
                shutil.rmtree(program)
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                if archive.testzip() is not None or len(archive.namelist()) != len(expected['files']):
                    raise ValueError('Invalid fixed workflow archive')
                for name, text in package['files'].items():
                    if archive.read('example/' + name) != text.encode():
                        raise ValueError('Workflow native archive member mismatch')
                    write_bytes(program / name, text.encode())
            for name, text in INPUTS['protected'].items():
                if (self.home / name).read_bytes() != text.encode():
                    raise ValueError('Native fixture changed protected user data')
            record('confirm', operation=self.pending['operation'], request=args[0])
            self.completed.append(self.pending)
            self.pending = None
            self.index += 1
            return {'name': 'Example', 'mode': self.case.get('native_mode', 'success')}
        raise ValueError('Unknown workflow control')
