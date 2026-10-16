import base64
import hashlib
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'py_modules'))
import repo_probe
from repo_probe import probe_repository


def repository(files, *, truncated=False):
    tree, blobs = [], {}
    for path, content in files.items():
        data = content.encode() if isinstance(content, str) else json.dumps(content).encode()
        sha = hashlib.sha1(data).hexdigest()
        tree.append(dict(path=path, type='blob', mode='100644', size=len(data), sha=sha))
        blobs[sha] = dict(encoding='base64', content=base64.b64encode(data).decode(), size=len(data))
    def fetch(url, **kwargs):
        if '/git/trees/HEAD?' in url:
            return dict(sha='a' * 40, truncated=truncated, tree=tree)
        return blobs[url.rsplit('/', 1)[1]]
    return Mock(side_effect=fetch), tree


def plugin_files(path='', name='Example', package_name='example', *, dependencies=True):
    prefix = path + '/' if path else ''
    package = dict(name=package_name, version='1.0.0')
    if dependencies:
        package['dependencies'] = {'@decky/api': '^1'}
    return {prefix + 'plugin.json': dict(name=name, author='Acme', flags=[]), prefix + 'package.json': package}


class RepositoryProbeTests(unittest.TestCase):
    def test_root_frontend_only_plugin_and_normalized_url(self):
        fetch, _ = repository(plugin_files())
        result = probe_repository('https://github.com/acme/example', fetch)
        self.assertEqual(result['repo'], 'acme/example')
        self.assertEqual(result['tree_sha'], 'a' * 40)
        self.assertEqual(result['plugins'], [dict(path='', name='Example', package_name='example', version='1.0.0', evidence=['依赖 @decky/api'])])
        self.assertTrue(all(c.args[0].startswith('https://api.github.com/repos/acme/example/git/') for c in fetch.call_args_list))
        self.assertTrue(all(c.kwargs['max_bytes'] > 0 for c in fetch.call_args_list))

    def test_monorepo_finds_siblings_without_mixing_their_identity(self):
        files = {'package.json': {'name': 'workspace', 'private': True}}
        files.update(plugin_files('apps/first', 'First', 'first'))
        files.update(plugin_files('plugins/second', 'Second', 'second'))
        fetch, _ = repository(files)
        result = probe_repository('acme/mono', fetch)
        self.assertEqual([(p['path'], p['name'], p['package_name']) for p in result['plugins']], [('apps/first', 'First', 'first'), ('plugins/second', 'Second', 'second')])

    def test_workspace_hoisted_dependencies_and_backend_legacy_marker(self):
        files = {'package.json': dict(workspaces=['plugins/*'], devDependencies={'@decky/ui': '^4'})}
        files.update(plugin_files('plugins/first', dependencies=False))
        files['plugins/first/src/index.tsx'] = 'import { definePlugin } from "@decky/api";'
        files.update(plugin_files('standalone', 'Legacy', 'legacy', dependencies=False))
        files['standalone/main.py'] = 'import decky_plugin\nclass Plugin: pass\n'
        fetch, _ = repository(files)
        result = probe_repository('acme/mono', fetch)
        self.assertEqual(len(result['plugins']), 2)
        self.assertIn('源码 src/index.tsx', result['plugins'][0]['evidence'])

    def test_workspace_dependency_cannot_qualify_an_unrelated_sibling(self):
        files = {'package.json': dict(workspaces=['plugins/*'], dependencies={'@decky/ui': '^4'})}
        files.update(plugin_files('plugins/not-decky', dependencies=False))
        fetch, _ = repository(files)
        self.assertEqual(probe_repository('acme/workspace', fetch)['plugins'], [])

    def test_source_import_supports_hoisted_frontend_only_project(self):
        files = plugin_files('packages/ui', dependencies=False)
        files['packages/ui/src/index.tsx'] = 'import { definePlugin } from "@decky/api";'
        fetch, _ = repository(files)
        self.assertIn('源码 src/index.tsx', probe_repository('acme/mono', fetch)['plugins'][0]['evidence'])

    def test_generic_repository_and_metadata_without_decky_evidence_are_not_plugins(self):
        for files in ({'README.md': 'Decky plugin, trust me'}, plugin_files(dependencies=False), {'plugin.json': {'name': 'missing package'}}):
            fetch, _ = repository(files)
            self.assertEqual(probe_repository('acme/not-decky', fetch)['plugins'], [])

    def test_incomplete_tree_and_too_many_candidates_fail_closed(self):
        fetch, _ = repository(plugin_files(), truncated=True)
        with self.assertRaisesRegex(RuntimeError, '完整'):
            probe_repository('acme/huge', fetch)
        files = {}
        for i in range(11):
            files.update(plugin_files(f'plugins/{i}'))
        fetch, _ = repository(files)
        with self.assertRaisesRegex(RuntimeError, '10'):
            probe_repository('acme/huge', fetch)
        self.assertEqual(fetch.call_count, 1)

    def test_symbolic_links_and_submodules_are_not_followed(self):
        fetch, tree = repository(plugin_files())
        tree[0]['mode'] = '120000'
        self.assertEqual(probe_repository('acme/symlink', fetch)['plugins'], [])
        self.assertEqual(fetch.call_count, 1)

    def test_invalid_repository_does_not_fetch(self):
        fetch = Mock()
        for repo in ('https://evil.test/acme/plugin', '../plugin', None):
            with self.assertRaises(RuntimeError):
                probe_repository(repo, fetch)
        fetch.assert_not_called()

    def test_failed_blob_request_and_oversized_metadata_are_not_negative_results(self):
        fetch, _ = repository(plugin_files())
        tree = fetch.side_effect('https://api.github.com/repos/acme/x/git/trees/HEAD?recursive=1')
        fetch.side_effect = [tree, RuntimeError('GitHub 请求已限流')]
        with self.assertRaisesRegex(RuntimeError, '限流'):
            probe_repository('acme/plugin', fetch)
        fetch, entries = repository(plugin_files())
        entries[0]['size'] = 1024 * 1024
        with self.assertRaisesRegex(RuntimeError, '元数据'):
            probe_repository('acme/plugin', fetch)

    def test_legacy_backend_without_frontend_dependencies(self):
        files = plugin_files(dependencies=False)
        files['main.py'] = 'from decky_plugin import logger\n'
        fetch, _ = repository(files)
        self.assertEqual(probe_repository('acme/legacy', fetch)['plugins'][0]['evidence'], ['后端 main.py'])

    def test_request_and_elapsed_budgets_fail_closed(self):
        fetch, _ = repository(plugin_files())
        with patch.object(repo_probe.time, 'monotonic', side_effect=[0, 121]), self.assertRaisesRegex(RuntimeError, '预算'):
            probe_repository('acme/slow', fetch)
        self.assertEqual(fetch.call_count, 1)
        files = {}
        for i in range(10):
            path = f'plugins/example-{i}'
            files.update(plugin_files(path, f'Example{i}', f'example-{i}', dependencies=False))
            files[path + '/main.py'] = f'# ordinary backend {i}'
            for entry in ('src/index.tsx', 'src/index.ts', 'src/index.jsx', 'src/index.js', 'dist/index.js'):
                files[path + '/' + entry] = f'// ordinary entry {path}/{entry}'
        fetch, _ = repository(files)
        with self.assertRaisesRegex(RuntimeError, '预算'):
            probe_repository('acme/deep', fetch)
        self.assertEqual(fetch.call_count, 41)

    def test_malformed_responses_and_unsafe_tree_paths_fail(self):
        for value in ({}, [], dict(sha='a'*40, truncated=False, tree=None)):
            with self.assertRaises(RuntimeError):
                probe_repository('acme/plugin', Mock(return_value=value))
        fetch, entries = repository(plugin_files())
        entries[0]['path'] = '../plugin.json'
        with self.assertRaisesRegex(RuntimeError, '路径'):
            probe_repository('acme/plugin', fetch)


if __name__ == '__main__':
    unittest.main()
