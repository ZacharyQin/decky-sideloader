"""Search/monorepo/multi-asset fixtures with dependencies absent from the original ZIP."""
import asyncio
import base64
from collections import Counter
import hashlib
import io
import json
from pathlib import Path
from urllib.parse import unquote, urlparse
import zipfile

from fixture_assets import HERE, archive_bytes, digest, write_bytes
from http_fixture import HttpFixture

INPUT = json.loads((HERE / 'fixtures/discovery.json').read_text())
HASHES = json.loads((HERE / 'fixtures/discovery-hashes.json').read_text())
SEARCH = 'https://api.github.com/search/repositories?q=beta+tools+fork%3Atrue&per_page=20'
BASE = 'https://api.github.com/repos/fixture/monorepo/'
RELEASES = BASE + 'releases?per_page=100&page=1'
ASSET = 'https://github.com/fixture/monorepo/releases/download/v3.2.1/beta.zip'
ALPHA = 'https://github.com/fixture/monorepo/releases/download/v3.2.1/alpha.zip'


def discovery_case(name):
    rows = [row for row in INPUT['cases'] if row['id'] == name]
    if len(rows) != 1:
        raise ValueError('Unknown discovery scenario')
    return {**rows[0], 'source': 'download', 'discovery': True, 'native': 'success', 'package': 'beta'}


def checked(kind):
    package = INPUT['packages'][kind]
    expected = HASHES['packages'][kind]
    if {name: digest(value.encode()) for name, value in package['files'].items()} != expected['files']:
        raise ValueError('Discovery inputs changed without reviewed hashes')
    data = io.BytesIO(archive_bytes(package))
    if package.get('comment'):
        with zipfile.ZipFile(data, 'a') as archive:
            archive.comment = package['comment'].encode()
    result = data.getvalue()
    if digest(result) != expected['archive']:
        raise ValueError('Discovery archive inputs changed')
    if package.get('merged_files') and {name: digest(value.encode()) for name, value in package['merged_files'].items()} != expected['merged_files']:
        raise ValueError('Independent merged-file expectations changed')
    return package, expected, result


class DiscoveryHttp(HttpFixture):
    def __init__(self, case):
        self.routes, self.required, self.status_scripts = {}, {}, {}
        self.counts, self.errors, self.clients = Counter(), [], set()
        self.server = None
        self.asset, self.assets = ASSET, {ASSET, ALPHA, *(row['url'] for row in INPUT['dependencies'])}
        self.routes[SEARCH] = json.dumps({'items': [{'full_name': INPUT['repo'], 'description': 'Fixed two-plugin repository', 'stargazers_count': 7, 'fork': True, 'archived': False}]}).encode()
        self.required[SEARCH] = case['searches']
        tree = []
        for kind in ('alpha', 'beta'):
            package, _, _ = checked(kind)
            for name in ('plugin.json', 'package.json'):
                data = package['files'][name].encode()
                key = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
                tree.append({'path': f'packages/{kind}/{name}', 'type': 'blob', 'mode': '100644', 'sha': key, 'size': len(data)})
                url = BASE + 'git/blobs/' + key
                self.routes[url] = json.dumps({'encoding': 'base64', 'content': base64.b64encode(data).decode()}).encode()
                self.required[url] = 0 if case['action'] == 'truncated' else case['probes']
        tree_url = BASE + 'git/trees/HEAD?recursive=1'
        self.routes[tree_url] = json.dumps({'sha': 'a' * 40, 'truncated': case['action'] == 'truncated', 'tree': tree}).encode()
        self.required[tree_url] = case['probes']
        package, beta_hashes, beta = checked('beta')
        _, alpha_hashes, alpha = checked('alpha')
        self.routes[ASSET], self.routes[ALPHA] = beta, alpha
        prepare = case['action'] in ('success', 'dependency_failure', 'restart', 'crash_dependency')
        self.required[ASSET], self.required[ALPHA] = int(prepare), 0
        self.required[ASSET + ';range=bytes=-65557'] = int(prepare)
        with zipfile.ZipFile(io.BytesIO(beta)) as archive:
            entry = archive.getinfo('beta-fixture/package.json')
            offset, content = entry.header_offset, entry.header_offset + 30 + len(entry.filename.encode()) + len(entry.extra)
            for start, end in ((offset, offset + 29), (offset + 30, content - 1), (content, content + entry.compress_size - 1)):
                self.required[ASSET + f';range=bytes={start}-{end}'] = int(prepare)
        release = {'tag_name': INPUT['tag'], 'name': 'Two different plugins', 'draft': False, 'prerelease': False,
            'published_at': '2020-01-01T00:00:00Z', 'assets': [
                {'name': 'alpha.zip', 'browser_download_url': ALPHA, 'size': len(alpha), 'digest': 'sha256:' + alpha_hashes['archive']},
                {'name': 'beta.zip', 'browser_download_url': ASSET, 'size': len(beta), 'digest': 'sha256:' + beta_hashes['archive']}]}
        releases = [release]
        if case['action'] == 'pagination_failure':
            releases = [{**release, 'tag_name': f'v0.{index}.0', 'assets': []} for index in range(100)]
            next_page = BASE + 'releases?per_page=100&page=2'
            self.routes[next_page], self.required[next_page], self.status_scripts[next_page] = b'', 1, [503]
        self.routes[RELEASES], self.required[RELEASES] = json.dumps(releases).encode(), case['releases']
        for index, row in enumerate(INPUT['dependencies']):
            if digest(row['text'].encode()) != row['sha256']:
                raise ValueError('Dependency input hash mismatch')
            self.routes[row['url']] = b'fixed corrupt second dependency' if case['action'] == 'dependency_failure' and index == 1 else row['text'].encode()
            self.required[row['url']] = (2 if case['action'] == 'crash_dependency' else 1) if prepare else 0
        if case['action'] == 'crash_dependency':
            key = INPUT['dependencies'][1]['url']
            self.gates = {key: {'entered': asyncio.Event(), 'released': asyncio.Event(), 'once': True, 'prefix': 4}}
            self.allowed_aborts, self.aborted = {key}, []


class DiscoveryFixture:
    methods = {'__native_request__', '__native_confirm__', '__native_cancel__', '__discovery_metadata__'}

    def __init__(self, home, case):
        self.home, self.case = home, case
        self.pending, self.confirmed = None, False

    @property
    def metadata(self):
        return {'discovery': self.case, 'pending': self.pending, 'confirmed': self.confirmed}

    async def control(self, method, args, record):
        if method == '__discovery_metadata__':
            return self.metadata
        package, expected, original = checked('beta')
        if method == '__native_request__':
            if not self.case['dispatches'] or self.pending:
                raise ValueError('Unexpected or duplicate monorepo dispatch')
            uri, name, version, checksum, kind = args
            path = Path(unquote(urlparse(uri).path))
            if (urlparse(uri).scheme != 'file' or path.name != 'plugin.zip' or
                    path.parent.parent != self.home / 'data/decky-sideloader/installations' or
                    len(path.parent.name) != 32 or any(c not in '0123456789abcdef' for c in path.parent.name) or path.is_symlink() or
                    (name, version, kind) != ('Beta Fixture', '3.2.1', 0) or digest(path.read_bytes()) != checksum or
                    path.with_name('source.zip').read_bytes() != original):
                raise ValueError('Monorepo native request does not match independently selected plugin/version/source')
            with zipfile.ZipFile(path) as archive:
                wanted = {'beta-fixture/' + name for name in expected['merged_files']}
                if (set(archive.namelist()) != wanted or archive.testzip() is not None or
                        archive.comment != package['comment'].encode() or
                        {name.removeprefix('beta-fixture/'): digest(archive.read(name)) for name in archive.namelist()} != expected['merged_files']):
                    raise ValueError('Merged package differs from frozen independent file/metadata expectations')
                for binary_name in ('lib-a', 'lib-b'):
                    if (archive.getinfo('beta-fixture/bin/' + binary_name).external_attr >> 16) & 0o777 != 0o755:
                        raise ValueError('Merged remote dependency is not executable')
            request = 'discovery-native-' + path.parent.name
            self.pending = {'id': path.parent.name, 'artifact': str(path), 'sha256': checksum, 'request_id': request}
            record('dispatch', operation=path.parent.name, args=args)
            return {'name': name, 'version': version, 'sha256': checksum, 'request_id': request, 'mode': 'success'}
        if method != '__native_confirm__' or not self.pending or self.confirmed or args != [self.pending['request_id']]:
            raise ValueError('Unknown or repeated monorepo native confirmation')
        path = Path(self.pending['artifact'])
        if digest(path.read_bytes()) != self.pending['sha256']:
            raise ValueError('Merged archive changed before native confirmation')
        with zipfile.ZipFile(path) as archive:
            for name in package['merged_files']:
                info = archive.getinfo('beta-fixture/' + name)
                data = archive.read(info)
                if digest(data) != expected['merged_files'][name]:
                    raise ValueError('Native merged member changed')
                target = self.home / 'plugins/beta-fixture' / name
                write_bytes(target, data)
                target.chmod((info.external_attr >> 16) & 0o777)
        record('confirm', operation=self.pending['id'], request=args[0])
        self.confirmed = True
        return {'name': 'Beta Fixture', 'mode': 'success'}
