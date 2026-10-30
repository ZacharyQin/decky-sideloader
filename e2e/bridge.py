"""Private, network-denied host fixture for the real Python adapter/Rust child."""
import asyncio
import ctypes
import importlib.util
import json
import logging
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import types
from urllib.parse import unquote, urlparse

from fixture_assets import INPUTS, apply_native, checked_package, digest, scenario, seed_install, write_bytes
from http_fixture import HttpFixture
from workflow_fixture import WorkflowFixture, WorkflowHttp, workflow_case
from self_fixture import SelfFixture, SelfHttp, candidate as self_candidate, self_case
from discovery_fixture import DiscoveryFixture, DiscoveryHttp, discovery_case
from checks_fixture import ChecksFixture, ChecksHttp, checks_case
from diagnostics_fixture import DiagnosticsFixture, DiagnosticsHttp, diagnostics_case
from phase_fixture import BackupGate

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = Path(sys.argv[1]).resolve()
CASE_NAME = sys.argv[2] if len(sys.argv) > 2 else 'baseline'
if (FIXTURE.parent != Path(tempfile.gettempdir()).resolve() or
        not FIXTURE.name.startswith('sideloader-e2e-') or
        not FIXTURE.is_dir() or any(FIXTURE.iterdir())):
    raise SystemExit('Refusing a nonempty or non-fixture directory')
if CASE_NAME == 'baseline':
    CASE = None
elif CASE_NAME.startswith('self_'):
    CASE = self_case(CASE_NAME)
elif CASE_NAME.startswith('discovery_'):
    CASE = discovery_case(CASE_NAME)
elif CASE_NAME.startswith('checks_'):
    CASE = checks_case(CASE_NAME)
elif CASE_NAME.startswith('diagnostics_'):
    CASE = diagnostics_case(CASE_NAME)
elif CASE_NAME.startswith('workflow_'):
    CASE = workflow_case(CASE_NAME)
else:
    CASE = scenario(CASE_NAME)
DOWNLOAD = bool(CASE and CASE.get('source') == 'download')
DRIVER = Path(sys.argv[3]).resolve() if len(sys.argv) > 3 else None
CANDIDATE = Path(sys.argv[4]).resolve() if len(sys.argv) > 4 else None
if bool(CASE and CASE.get('self_flow')) != bool(CANDIDATE):
    raise SystemExit('Only full self-update scenarios accept a candidate')
if DOWNLOAD != bool(DRIVER) or (DRIVER and not DRIVER.is_relative_to(ROOT / 'backend-rs/target')):
    raise SystemExit('Only download cases may use the private Cargo test target')


def deny_network(allow_fixture_tcp=False):
    library = ctypes.CDLL('libseccomp.so.2', use_errno=True)
    library.seccomp_init.argtypes = [ctypes.c_uint32]
    library.seccomp_init.restype = ctypes.c_void_p
    library.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    library.seccomp_syscall_resolve_name.restype = ctypes.c_int

    class Compare(ctypes.Structure):
        _fields_ = [('arg', ctypes.c_uint), ('op', ctypes.c_int),
                    ('datum_a', ctypes.c_uint64), ('datum_b', ctypes.c_uint64)]

    library.seccomp_rule_add_array.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int,
                                              ctypes.c_uint, ctypes.POINTER(Compare)]
    library.seccomp_load.argtypes = [ctypes.c_void_p]
    library.seccomp_release.argtypes = [ctypes.c_void_p]
    context = library.seccomp_init(0x7fff0000)
    if not context:
        raise RuntimeError('seccomp initialization failed')
    try:
        number = library.seccomp_syscall_resolve_name(b'socket')
        comparisons = [Compare(0, 6, socket.AF_INET, 0), Compare(0, 4, 0, 0)] if allow_fixture_tcp else [Compare(0, 1, socket.AF_UNIX, 0)]
        for comparison in comparisons:
            if number < 0 or library.seccomp_rule_add_array(context, 0x00050001, number, 1, ctypes.byref(comparison)) != 0:
                raise RuntimeError('seccomp rule failed')
        if allow_fixture_tcp:
            # No IPv4 datagram/raw sockets; the test-only Transport pins TCP to the fixture origin.
            for socket_type in (socket.SOCK_DGRAM, socket.SOCK_RAW):
                terms = (Compare * 2)(Compare(0, 4, socket.AF_INET, 0), Compare(1, 7, 0xf, socket_type))
                if library.seccomp_rule_add_array(context, 0x00050001, number, 2, terms) != 0:
                    raise RuntimeError('seccomp datagram rule failed')
        if library.seccomp_load(context) != 0:
            raise RuntimeError('seccomp load failed')
    finally:
        library.seccomp_release(context)
    for family in ((socket.AF_INET6,) if allow_fixture_tcp else (socket.AF_INET, socket.AF_INET6)):
        try:
            connection = socket.socket(family, socket.SOCK_STREAM)
        except PermissionError:
            continue
        connection.close()
        raise RuntimeError('network sandbox is not active')


def send(value):
    sys.stdout.write(json.dumps(value, ensure_ascii=False, separators=(',', ':')) + '\n')
    sys.stdout.flush()


async def run():
    home = FIXTURE / 'home'
    plugin_dir = home / 'plugins/decky-sideloader'
    version = json.loads((ROOT / 'package.json').read_text())['version']
    for name in ('bin', 'dist', 'data'):
        (plugin_dir / name).mkdir(parents=True)
    core_source = DRIVER if DRIVER else ROOT / 'bin/sideloader-core'
    for source, target in ((core_source, plugin_dir / 'bin/sideloader-core'),
                           (ROOT / 'data/repositories.json', plugin_dir / 'data/repositories.json')):
        shutil.copyfile(source, target)
    (plugin_dir / 'bin/sideloader-core').chmod(0o755)
    for name in ('backend.json', 'build.json', 'index.js', 'notices.json'):
        shutil.copyfile(ROOT / 'dist' / name, plugin_dir / 'dist' / name)
    (plugin_dir / 'plugin.json').write_text('{"name":"Sideloader"}')
    (plugin_dir / 'package.json').write_text(json.dumps({'name': 'decky-sideloader', 'version': version}))
    example = home / 'plugins/example'
    write_bytes(example / 'plugin.json', b'{"name":"Example","author":"Fixture"}\n')
    write_bytes(example / 'package.json', b'{"name":"example","version":"1.0.0","repository":"fixture/example"}\n')
    write_bytes(example / 'main.py', b'# original fixture example\n')
    write_bytes(example / 'dist/index.js', b'export const original = true;\n')
    for relative, text in INPUTS['protected'].items():
        write_bytes(home / relative, text.encode())
    settings = home / 'settings/decky-sideloader'
    settings.mkdir(parents=True)
    (home / 'settings/loader.json').write_text('{"frozenPlugins":[]}')
    write_bytes(home / 'data/decky-sideloader/cache/native-updates.json',
                b'{"channel":"https://plugins.deckbrew.xyz/plugins","names":[]}')
    user = home / 'user'
    user.mkdir()
    workflow = WorkflowFixture(home, CASE) if CASE and CASE.get('workflow') else None
    self_flow = SelfFixture(home, CASE, *self_candidate(CANDIDATE)) if CANDIDATE else None
    discovery_flow = DiscoveryFixture(home, CASE) if CASE and CASE.get('discovery') else None
    checks_flow = ChecksFixture(home, CASE) if CASE and CASE.get('checks') else None
    diagnostics_flow = DiagnosticsFixture(home, CASE) if CASE and CASE.get('diagnostics') else None
    active_flow = self_flow or discovery_flow or checks_flow or diagnostics_flow or workflow
    http = SelfHttp(CASE, self_flow.info, self_flow.data) if self_flow else DiscoveryHttp(CASE) if discovery_flow else ChecksHttp(CASE) if checks_flow else DiagnosticsHttp(CASE) if diagnostics_flow else WorkflowHttp(CASE) if workflow else HttpFixture(CASE) if DOWNLOAD else None
    phase_gate = await BackupGate(FIXTURE, home, CASE.get('phase_gate','backup-first-chunk')).start() if CASE and (CASE.get('backup_gate') or CASE.get('phase_gate')) else None
    if http:
        origin = await http.start()
        (user / '.sideloader-e2e-origin').write_text(origin)
        if diagnostics_flow:
            snapshot = json.loads(subprocess.check_output([str(DRIVER), '--fixture-addresses'],
                env={'PATH': os.environ.get('PATH', ''), 'HOME': str(user)}, text=True))
            addresses = snapshot.get('addresses')
            if not isinstance(addresses, list) or not addresses or not all(isinstance(value, str) for value in addresses):
                raise RuntimeError('No private IPv4 host interface is available for the diagnostic application test')
            diagnostics_flow.addresses = addresses
            (user / '.sideloader-e2e-share-addresses').write_text(json.dumps(addresses))
        identity = json.loads(subprocess.check_output([str(DRIVER), '--identity'],
                              env={'PATH': os.environ.get('PATH', ''), 'HOME': str(user)}, text=True))
        metadata = json.loads((plugin_dir / 'dist/backend.json').read_text())
        metadata.update({key: identity[key] for key in ('fingerprint', 'target', 'sha256')})
        if metadata['sha256'] != digest((plugin_dir / 'bin/sideloader-core').read_bytes()):
            raise ValueError('Private driver identity/hash mismatch')
        (plugin_dir / 'dist/backend.json').write_text(json.dumps(metadata))
        package, expected, _ = checked_package('new')
        install = {'operation_id': None, 'request_id': 'fixture-native-download', 'package': package,
                   'hashes': expected['files'], 'archive_sha256': expected['archive'],
                   'artifact': None, 'backup_sha256': None}
    else:
        # The distributed binary ignores this private test-driver configuration.
        (user / '.sideloader-e2e-origin').write_text('http://127.0.0.1:1')
        install = seed_install(home, CASE) if CASE else None
    if active_flow:
        install = {'operation_id': None, **active_flow.metadata}
    sdk = types.ModuleType('decky')
    sdk.logger = logging.getLogger('isolated-sideloader-e2e')
    for key, value in {
        'DECKY_HOME': home, 'DECKY_PLUGIN_DIR': plugin_dir, 'DECKY_PLUGIN_VERSION': version,
        'DECKY_PLUGIN_NAME': 'Sideloader', 'DECKY_PLUGIN_RUNTIME_DIR': home / 'data/decky-sideloader',
        'DECKY_PLUGIN_SETTINGS_DIR': settings, 'DECKY_PLUGIN_LOG_DIR': home / 'logs/decky-sideloader',
        'DECKY_USER_HOME': user, 'DECKY_VERSION': 'fixture',
    }.items():
        setattr(sdk, key, str(value))

    async def emit(name, *args):
        send({'event': name, 'args': args})

    sdk.emit = emit
    sys.modules['decky'] = sdk
    spec = importlib.util.spec_from_file_location('sideloader_e2e_adapter', ROOT / 'backend/main.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    deny_network(DOWNLOAD)
    plugin = module.Plugin()
    tasks = set()
    lifecycle = asyncio.Lock()
    history = []
    history_path = FIXTURE / 'native-events.jsonl'

    def record(kind, **fields):
        item = {'kind': kind, 'operation': install['operation_id'] if install else None, **fields}
        history.append(item)
        with history_path.open('a') as output:
            output.write(json.dumps(item, sort_keys=True) + '\n')

    def native_snapshot():
        rows = []
        for directory in sorted((home / 'plugins').iterdir()):
            if not directory.is_dir() or not (directory / 'plugin.json').is_file():
                continue
            manifest = json.loads((directory / 'plugin.json').read_text())
            package = json.loads((directory / 'package.json').read_text())
            rows.append({'name': manifest['name'], 'version': package['version'], 'load_type': 0, 'disabled': False})
        return rows

    async def restart(age_operation=False):
        nonlocal plugin, version
        previous = plugin._process.pid if plugin._process else None
        previous_boot = plugin._hello.result()['boot_id']
        await plugin._unload()
        if age_operation:
            path = home / 'data/decky-sideloader/installations/operation.json'
            value = json.loads(path.read_text())
            if value['stage'] != 'awaiting' or not value.get('request_id'):
                raise ValueError('Aging requires an actually dispatched operation')
            value['submitted_at'] = 1
            path.write_text(json.dumps(value))
        if self_flow:
            version = json.loads((plugin_dir / 'package.json').read_text())['version']
            sdk.DECKY_PLUGIN_VERSION = version
        plugin = module.Plugin()
        await plugin._main()
        return {'previous_pid': previous, 'core_pid': plugin._process.pid,
                'previous_boot': previous_boot, 'boot': plugin._hello.result()['boot_id'],
                'identity': plugin._hello.result()}

    async def control(method, args):
        nonlocal plugin
        if active_flow and method in active_flow.methods:
            return await active_flow.control(method, args, record)
        if method == '__snapshot__':
            return native_snapshot()
        if method == '__metadata__':
            return {'history': history, 'fixture': active_flow.metadata if active_flow else install, 'case': CASE,
                    'http': http.report() if http else None,
                    'core_pid': plugin._process.pid, 'version': version, 'identity': plugin._hello.result()}
        if method == '__native_request__':
            if not install:
                raise ValueError('Native install is not configured')
            if DOWNLOAD:
                candidate = Path(unquote(urlparse(args[0]).path))
                base = home / 'data/decky-sideloader/installations'
                if (candidate.name != 'plugin.zip' or candidate.parent.parent != base or
                        len(candidate.parent.name) != 32 or any(c not in '0123456789abcdef' for c in candidate.parent.name) or
                        candidate.is_symlink()):
                    raise ValueError('Prepared artifact is outside fixture operation directory')
                install['artifact'] = str(candidate)
                install['operation_id'] = candidate.parent.name
            record('dispatch', args=args)
            if sum(item['kind'] == 'dispatch' for item in history) != 1:
                raise ValueError('Duplicate native install dispatch')
            uri, name, selected_version, checksum, install_type = args
            expected = install['package']
            parsed = urlparse(uri)
            if (parsed.scheme != 'file' or unquote(parsed.path) != install['artifact'] or
                    (name, selected_version, checksum) != (expected['name'], expected['version'], install['archive_sha256']) or
                    install_type != (4 if CASE['package'] == 'overwrite' else 0)):
                raise ValueError('Native dispatch does not match independent fixture manifest')
            return {'name': name, 'version': selected_version, 'request_id': install['request_id'],
                    'sha256': checksum, 'mode': CASE['native']}
        if method == '__native_confirm__':
            if not install or args != [install['request_id']]:
                raise ValueError('Unknown native confirmation')
            record('confirm', request=args[0])
            if sum(item['kind'] == 'confirm' for item in history) != 1:
                raise ValueError('Duplicate confirmation')
            apply_native(home, install, CASE['native'] == 'tampered_file')
            return {'mode': CASE['native'], 'name': install['package']['name']}
        if method == '__native_cancel__':
            if not install or args != [install['request_id']]:
                raise ValueError('Unknown native cancellation')
            record('cancel', request=args[0])
            return None
        if method == '__restart__':
            async with lifecycle:
                return await restart(bool(args and args[0].get('age_operation')))
        if method == '__phase_status__' and phase_gate:
            return phase_gate.status()
        if method == '__phase_disable__' and phase_gate:
            return phase_gate.disable_for_next_core()
        if method == '__kill_core__':
            previous = plugin._process
            previous.kill()
            await previous.wait()
            return {'core_pid': previous.pid, 'returncode': previous.returncode}
        if method == '__seed_self_update__':
            async with lifecycle:
                mode = args[0]
                if mode not in ('matching', 'binary', 'backend', 'bundle'):
                    raise ValueError('Unknown self-update fixture mode')
                previous = plugin._process.pid
                previous_boot = plugin._hello.result()['boot_id']
                await plugin._unload()
                shutil.copyfile(ROOT / 'dist/index.js', plugin_dir / 'dist/index.js')
                build = json.loads((plugin_dir / 'dist/build.json').read_text())
                backend = json.loads((plugin_dir / 'dist/backend.json').read_text())
                names = ['plugin.json', 'package.json', 'dist/index.js', 'dist/build.json',
                         'bin/sideloader-core', 'dist/backend.json', 'data/repositories.json']
                hashes = {name: digest((plugin_dir / name).read_bytes()) for name in names}
                package = {'name': 'Sideloader', 'folder': 'decky-sideloader', 'version': version,
                           'hashes': hashes, 'sha256': 'd' * 64, 'warnings': [], 'frontend': build['fingerprint'],
                           'backend': 'f' * 64 if mode == 'backend' else backend['fingerprint'],
                           'binary': 'f' * 64 if mode == 'binary' else backend['sha256']}
                state = {'schema': 1, 'check': None, 'operation': {
                    'id': 'b' * 32, 'stage': 'verifying', 'release': {'version': version},
                    'package': package, 'from_boot': previous_boot, 'previous_stamp': None,
                    'request_id': 'fixture-self-request', 'saw_start': True, 'submitted_at': 1,
                    'cancel_requested': False, 'error': None}}
                path = home / 'data/decky-sideloader/self-update/state.json'
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(state))
                if mode == 'bundle':
                    with (plugin_dir / 'dist/index.js').open('ab') as output:
                        output.write(b'/* replaced after verification */')
                plugin = module.Plugin()
                await plugin._main()
                return {'previous_pid': previous, 'core_pid': plugin._process.pid, 'frontend': build,
                        'previous_boot': previous_boot, 'boot': plugin._hello.result()['boot_id']}
        raise ValueError('Unknown fixture control: ' + method)

    async def dispatch(request):
        try:
            if not isinstance(request.get('id'), int) or not isinstance(request.get('args', []), list):
                raise ValueError('Invalid fixture frame')
            method = request['method']
            args = request.get('args', [])
            if method.startswith('__'):
                result = await control(method, args)
            elif method in module.RPC_METHODS:
                result = await asyncio.wait_for(getattr(plugin, method)(*args), 12)
            else:
                raise ValueError('Unsupported fixture RPC')
            send({'id': request['id'], 'result': result})
        except Exception as exc:
            send({'id': request.get('id'), 'error': str(exc)})

    reader = asyncio.StreamReader(limit=32 * 1024 * 1024)
    pipe = await asyncio.get_running_loop().connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin)
    try:
        await plugin._main()
        send({'ready': True, 'core_pid': plugin._process.pid, 'network_denied': not DOWNLOAD,
              'network_mode': 'test-only loopback Transport; UDP/IPv6 denied' if DOWNLOAD else 'seccomp denies non-Unix sockets',
              'case': CASE_NAME, 'fixture': install})
        while line := await reader.readline():
            request = json.loads(line)
            if request.get('method') == '__shutdown__':
                break
            task = asyncio.create_task(dispatch(request))
            tasks.add(task)
            task.add_done_callback(tasks.discard)
    finally:
        pipe[0].close()
        if phase_gate:
            await phase_gate.close()
        await plugin._unload()
        if tasks:
            try:
                await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 15)
            except TimeoutError:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        if http:
            await http.close()
        send({'closed': True, 'core_returncode': plugin._process.returncode,
              'phase_gate': phase_gate.status() if phase_gate else None,
              'http': http.report() if http else None,
              'native_counts': {kind: sum(row['kind'] == kind for row in history)
                                for kind in ('dispatch', 'confirm', 'cancel')}})


asyncio.run(run())
