"""Two installed plugins, one verified update, one explicit API failure, and no installation."""
from collections import Counter
import io
import json
import zipfile

from fixture_assets import HERE, write_bytes
from http_fixture import HttpFixture
from workflow_fixture import checked
from self_fixture import LATEST

CHECKS = json.loads((HERE / 'fixtures/checks.json').read_text())
ASSET = 'https://github.com/fixture/example/releases/download/v2.0.0/plugin.zip'


def checks_case(name):
    cases = [case for case in CHECKS['cases'] if case['id'] == name or case.get('detached_case') == name]
    if len(cases) != 1:
        raise ValueError('Unknown update-check scenario')
    return {**cases[0], 'id': name, 'detached': name == cases[0].get('detached_case'), 'source': 'download', 'checks': True, 'native': 'success', 'package': 'overwrite'}


class ChecksHttp(HttpFixture):
    def __init__(self, case):
        self.routes, self.required, self.status_scripts = {}, {}, {}
        self.counts, self.errors, self.clients = Counter(), [], set()
        self.server = None
        self.asset, self.assets = ASSET, {ASSET}
        # With no mounted native-management responder, plugin eligibility cannot be established.
        # The scheduler must not query plugin repositories using an unproven Loader snapshot.
        requests = 0 if case.get('detached') else 1
        _, expected, data = checked('2.0.0')
        releases = 'https://api.github.com/repos/fixture/example/releases?per_page=100&page=1'
        self.routes[releases] = json.dumps([{'tag_name': 'v2.0.0', 'name': 'Fixed update', 'draft': False, 'prerelease': False,
            'published_at': '2020-01-01T00:00:00Z', 'assets': [{'name': 'plugin.zip', 'browser_download_url': ASSET,
            'size': len(data), 'digest': 'sha256:' + expected['archive']}]}]).encode()
        self.required[releases] = requests
        failed = 'https://api.github.com/repos/fixture/other/releases?per_page=100&page=1'
        self.routes[failed], self.required[failed] = b'', requests
        if requests:
            self.status_scripts[failed] = [503]
        self.routes[ASSET], self.required[ASSET] = data, 0
        self.required[ASSET + ';range=bytes=-65557'] = requests
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entry = archive.getinfo('example/package.json')
            offset = entry.header_offset
            content = offset + 30 + len(entry.filename.encode()) + len(entry.extra)
            for start, end in ((offset, offset + 29), (offset + 30, content - 1), (content, content + entry.compress_size - 1)):
                self.required[ASSET + f';range=bytes={start}-{end}'] = requests
        version = CHECKS['self_version']
        filename = 'decky-sideloader-' + version + '.zip'
        self.routes[LATEST] = json.dumps({'tag_name': 'v' + version, 'name': 'Already current', 'draft': False,
            'prerelease': False, 'assets': [{'id': 1, 'name': filename, 'browser_download_url':
            'https://github.com/ZacharyQin/decky-sideloader/releases/download/v' + version + '/' + filename,
            'size': 1, 'digest': 'sha256:' + 'a' * 64}]}).encode()
        self.required[LATEST] = 1


class ChecksFixture:
    methods = set()

    def __init__(self, home, case):
        self.case = case
        write_bytes(home / 'plugins/other/plugin.json', b'{"name":"Other Fixture","author":"Fixture"}\n')
        write_bytes(home / 'plugins/other/package.json', b'{"name":"other","version":"1.0.0","repository":"fixture/other"}\n')
        write_bytes(home / 'plugins/other/main.py', b'# original other fixture\n')
        write_bytes(home / 'plugins/other/dist/index.js', b'export const other = true;\n')
        write_bytes(home / 'settings/decky-sideloader/settings.json', json.dumps({'bindings': {}, 'networkMode': 'direct',
            'autoCheck': False, 'autoCheckMinutes': 360, 'checkNotifications': True, 'deletePackageAfterInstall': False, 'downloadTimeoutMinutes': 10}).encode())

    @property
    def metadata(self):
        return {'checks': self.case}
