"""Read-only, bounded discovery of Decky projects in a GitHub repository."""
# Semantic index:
# - owner: docs/upstream-conflict/sideload/plugin-management.md -- discover root and monorepo candidates without treating incomplete scans as plugin evidence.
import base64
import binascii
import json
import re
import time
from pathlib import PurePosixPath

from github_meta import parse_github_input

MAX_PROJECTS = 10
MAX_TREE_ENTRIES = 20000
MAX_FILE = 256 * 1024
DECKY_PACKAGES = ('@decky/api', '@decky/ui', '@decky/rollup', 'decky-frontend-lib')
SHA = re.compile(r'(?:[0-9a-f]{40}|[0-9a-f]{64})')
PYTHON_IMPORT = re.compile(r'^\s*(?:from\s+decky(?:_plugin)?\b|import\s+decky(?:_plugin)?\b)', re.MULTILINE)
JS_IMPORT = re.compile(r'''(?:from\s*|import\s*|require\s*\()\s*['"](?:@decky/(?:api|ui)|decky-frontend-lib)['"]''')


def dependencies(package):
    return [name for name in DECKY_PACKAGES if any(
        isinstance(package.get(group), dict) and isinstance(package[group].get(name), str)
        for group in ('dependencies', 'devDependencies', 'peerDependencies', 'optionalDependencies')
    )]


def probe_repository(raw_repo, fetch):
    repo = parse_github_input(raw_repo) if isinstance(raw_repo, str) else None
    if not repo:
        raise RuntimeError('GitHub 链接无效')
    base = f'https://api.github.com/repos/{repo}/git/'
    tree = fetch(base + 'trees/HEAD?recursive=1', accept='application/vnd.github+json', max_bytes=8 * 1024 * 1024)
    if not isinstance(tree, dict) or not isinstance(tree.get('tree'), list) or not isinstance(tree.get('sha'), str) or not SHA.fullmatch(tree['sha']):
        raise RuntimeError('GitHub 仓库目录响应格式异常')
    if tree.get('truncated') is not False or len(tree['tree']) > MAX_TREE_ENTRIES:
        raise RuntimeError('仓库目录过大，无法完成完整探查，请使用范围较小的仓库')
    files = {}
    for item in tree['tree']:
        if not isinstance(item, dict) or not isinstance(item.get('path'), str):
            raise RuntimeError('GitHub 仓库目录响应格式异常')
        path = item['path']
        if path.startswith('/') or '\\' in path or '\x00' in path or any(part in ('', '.', '..') for part in path.split('/')):
            raise RuntimeError('GitHub 仓库包含不安全路径')
        if item.get('type') == 'blob' and item.get('mode') in ('100644', '100755'):
            if path in files:
                raise RuntimeError('GitHub 仓库目录包含重复路径')
            files[path] = item
    paths = sorted(str(PurePosixPath(p).parent) for p in files if PurePosixPath(p).name == 'plugin.json')
    if len(paths) > MAX_PROJECTS:
        raise RuntimeError('仓库中 plugin.json 超过 10 个，无法在探查预算内确认所有插件')
    contents = {}
    started = time.monotonic()
    requests = 0

    def read(path):
        nonlocal requests
        item = files[path]
        size, sha = item.get('size'), item.get('sha')
        if type(size) is not int or not 0 <= size <= MAX_FILE or not isinstance(sha, str) or not SHA.fullmatch(sha):
            raise RuntimeError(f'仓库元数据或入口过大/无效：{path}')
        if sha not in contents:
            if requests >= 40 or time.monotonic() - started > 120:
                raise RuntimeError('仓库探查超过请求或时间预算，无法确认完整结果')
            requests += 1
            blob = fetch(base + 'blobs/' + sha, accept='application/vnd.github+json', max_bytes=1024 * 1024)
            if not isinstance(blob, dict) or blob.get('encoding') != 'base64' or not isinstance(blob.get('content'), str):
                raise RuntimeError(f'GitHub 文件响应格式异常：{path}')
            try:
                data = base64.b64decode(''.join(blob['content'].split()), validate=True)
                if len(data) != size or len(data) > MAX_FILE:
                    raise ValueError('size mismatch')
                contents[sha] = data.decode('utf-8-sig')
            except (ValueError, binascii.Error, UnicodeError) as error:
                raise RuntimeError(f'GitHub 文件内容无效：{path}') from error
        return contents[sha]

    def metadata(path):
        try:
            value = json.loads(read(path))
        except json.JSONDecodeError as error:
            raise RuntimeError(f'仓库元数据不是合法 JSON：{path}') from error
        if not isinstance(value, dict):
            raise RuntimeError(f'仓库元数据格式无效：{path}')
        return value

    plugins = []
    for directory in paths:
        prefix = '' if directory == '.' else directory + '/'
        if prefix + 'package.json' not in files:
            continue
        plugin, package = metadata(prefix + 'plugin.json'), metadata(prefix + 'package.json')
        name, package_name, version = plugin.get('name'), package.get('name'), package.get('version')
        if not isinstance(name, str) or not name.strip() or len(name) > 128 or any(ord(c) < 32 or c in '/\\' for c in name):
            continue
        if not isinstance(package_name, str) or not package_name.strip() or len(package_name) > 214 or any(ord(c) < 32 for c in package_name):
            continue
        if not isinstance(version, str) or not version.strip() or len(version) > 128:
            continue
        evidence = ['依赖 ' + dep for dep in dependencies(package)]
        if not evidence and prefix + 'main.py' in files and PYTHON_IMPORT.search(read(prefix + 'main.py')):
            evidence = ['后端 main.py']
        if not evidence:
            for entry in ('src/index.tsx', 'src/index.ts', 'src/index.jsx', 'src/index.js', 'dist/index.js'):
                if prefix + entry in files and JS_IMPORT.search(read(prefix + entry)):
                    evidence = ['源码 ' + entry]
                    break
        if evidence:
            plugins.append(dict(path='' if directory == '.' else directory, name=name, package_name=package_name, version=version, evidence=evidence))
    return dict(repo=repo, tree_sha=tree['sha'], plugins=plugins)
