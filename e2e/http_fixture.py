"""Exact-route loopback server used only by the non-distributed Cargo test target."""
import asyncio
import base64
from collections import Counter
import hashlib
import io
import json
import zipfile

from fixture_assets import checked_package


class HttpFixture:
    def __init__(self, case):
        package, expected, archive = checked_package('new')
        source = {name: package['files'][name].encode() for name in ('plugin.json', 'package.json')}
        source['main.py'] = b'import decky\n\nclass Plugin:\n    pass\n'
        base = 'https://api.github.com/repos/fixture/target/git/'
        tree = []
        self.routes = {}
        self.required = {}
        for name, data in source.items():
            git_hash = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
            tree.append({'path':name, 'type':'blob', 'mode':'100644', 'sha':git_hash, 'size':len(data)})
            self.routes[base + 'blobs/' + git_hash] = json.dumps({'encoding':'base64','content':base64.b64encode(data).decode()}).encode()
            self.required[base + 'blobs/' + git_hash] = 1
        self.routes[base + 'trees/HEAD?recursive=1'] = json.dumps({'sha':'a'*40,'truncated':False,'tree':tree}).encode()
        self.required[base + 'trees/HEAD?recursive=1'] = 1
        release_url = 'https://api.github.com/repos/fixture/target/releases?per_page=100&page=1'
        asset_url = 'https://github.com/fixture/target/releases/download/v2.0.0/plugin.zip'
        self.routes[release_url] = json.dumps([{'tag_name':'v2.0.0','name':'Fixture 2.0.0','body':'Fixed fixture release',
            'draft':False,'prerelease':False,'published_at':'2020-01-01T00:00:00Z',
            'assets':[{'name':'plugin.zip','browser_download_url':asset_url,'size':len(archive),
                       'digest':'sha256:'+expected['archive']}]}]).encode()
        self.required[release_url] = 2  # UI read, then preparation revalidates the release.
        self.routes[asset_url] = b'INVALID-FIXED-ZIP' if case['id'] == 'download_bad_zip' else archive
        self.required[asset_url] = 1
        self.required[asset_url + ';range=bytes=-65557'] = 1
        if case['id'] != 'download_bad_zip':
            with zipfile.ZipFile(io.BytesIO(archive)) as incoming:
                info = incoming.getinfo('fixture-target/package.json')
                offset = info.header_offset
                content = offset + 30 + len(info.filename.encode())
                self.required[asset_url + f';range=bytes={offset}-{offset+29}'] = 1
                self.required[asset_url + f';range=bytes={offset+30}-{content-1}'] = 1
                self.required[asset_url + f';range=bytes={content}-{content+info.compress_size-1}'] = 1
        self.counts = Counter()
        self.errors = []
        self.clients = set()
        self.server = None
        self.asset = asset_url

    async def start(self):
        self.server = await asyncio.start_server(self.handle, '127.0.0.1', 0, limit=16384)
        self.port = self.server.sockets[0].getsockname()[1]
        return f'http://127.0.0.1:{self.port}'

    async def handle(self, reader, writer):
        task = asyncio.current_task()
        self.clients.add(task)
        try:
            header = (await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), 5)).decode('ascii')
            lines = header.split('\r\n')
            method, path, protocol = lines[0].split(' ')
            fields = {}
            for line in lines[1:]:
                if line:
                    name, value = line.split(':', 1)
                    if name.lower() in fields:
                        raise ValueError('duplicate HTTP header')
                    fields[name.lower()] = value.strip()
            target = path[1:]
            if method != 'GET' or protocol != 'HTTP/1.1' or target not in self.routes:
                raise ValueError('unconfigured HTTP route: ' + lines[0])
            if set(fields) - {'host', 'accept', 'accept-encoding', 'user-agent', 'range'}:
                raise ValueError('unexpected HTTP header')
            if fields.get('user-agent') != 'decky-sideloader':
                raise ValueError('unexpected HTTP client identity')
            if fields.get('host') != f'127.0.0.1:{self.port}' or fields.get('accept-encoding') != 'identity':
                raise ValueError('unexpected HTTP host/encoding')
            if 'authorization' in fields or 'cookie' in fields:
                raise ValueError('unexpected HTTP credentials')
            if fields.get('accept') != ('application/octet-stream' if target in getattr(self, 'assets', {self.asset}) else 'application/vnd.github+json'):
                raise ValueError('unexpected Accept header')
            range_value = fields.get('range')
            key = target + (';range=' + range_value if range_value else '')
            if key not in self.required:
                raise ValueError('unconfigured HTTP range')
            self.counts[key] += 1
            if self.counts[key] > self.required[key]:
                raise ValueError('HTTP fixture overconsumed: ' + key)
            gate = getattr(self, 'gates', {}).get(key)
            if gate and gate.get('once') and self.counts[key] > 1:
                gate = None
            prefixed = None
            if gate:
                if gate.get('prefix'):
                    if range_value:
                        raise ValueError('Partial-body gate cannot wrap a Range response')
                    payload = self.routes[target]
                    prefix = gate['prefix']
                    if not 0 < prefix < len(payload):
                        raise ValueError('Invalid fixed partial-body gate')
                    writer.write((f'HTTP/1.1 200 OK\r\nContent-Length: {len(payload)}\r\nConnection: close\r\n\r\n').encode() + payload[:prefix])
                    await writer.drain()
                    prefixed = (payload, prefix)
                gate['entered'].set()
                release = asyncio.create_task(gate['released'].wait())
                disconnected = asyncio.create_task(reader.read(1))
                try:
                    done, _ = await asyncio.wait((release, disconnected), timeout=10, return_when=asyncio.FIRST_COMPLETED)
                    if disconnected in done:
                        if disconnected.result() != b'' or key not in self.allowed_aborts:
                            raise ValueError('Unexpected data or disconnect at HTTP gate')
                        self.aborted.append(key)
                        return
                    if release not in done:
                        raise TimeoutError('Fixture HTTP gate was not released or cancelled')
                finally:
                    for waiting in (release, disconnected):
                        if not waiting.done():
                            waiting.cancel()
                    await asyncio.gather(release, disconnected, return_exceptions=True)
                if prefixed:
                    payload, prefix = prefixed
                    writer.write(payload[prefix:])
                    await writer.drain()
                    return
            data = self.routes[target]
            headers = ''
            status = '200 OK'
            if range_value:
                start, end = range_value.removeprefix('bytes=').split('-')
                start, end = (max(0,len(data)-int(end)),len(data)-1) if not start else (int(start),int(end))
                headers = f'Content-Range: bytes {start}-{end}/{len(data)}\r\n'
                data = data[start:end+1]
                status = '206 Partial Content'
            script = getattr(self, 'status_scripts', {}).get(key)
            if script:
                scripted_status = script.pop(0)
                if scripted_status != 200:
                    status, headers, data = f'{scripted_status} Scripted', '', b''
            writer.write((f'HTTP/1.1 {status}\r\n{headers}Content-Length: {len(data)}\r\nConnection: close\r\n\r\n').encode() + data)
            await writer.drain()
        except Exception as error:
            self.errors.append(str(error))
            writer.write(b'HTTP/1.1 500 Fixture mismatch\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass
            self.clients.discard(task)

    def report(self):
        missing = {url: {'expected': count, 'actual': self.counts[url]}
                   for url, count in self.required.items() if self.counts[url] != count}
        pending = [f'unconsumed response script: {key}' for key, values in getattr(self, 'status_scripts', {}).items() if values]
        return {'counts':dict(self.counts), 'required':self.required, 'mismatches':missing, 'errors':self.errors + pending,
                'active_clients':len(self.clients), 'listening':self.server.is_serving(),
                'aborted':getattr(self, 'aborted', []),
                'gates':{key: value['entered'].is_set() for key, value in getattr(self, 'gates', {}).items()}}

    async def close(self):
        self.server.close()
        await self.server.wait_closed()
        for gate in getattr(self, 'gates', {}).values():
            gate['released'].set()
        if self.clients:
            await asyncio.wait_for(asyncio.gather(*self.clients, return_exceptions=True), 6)
