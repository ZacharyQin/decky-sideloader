import importlib.util
import json
import subprocess
import tempfile
from unittest.mock import patch
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('backend_build', ROOT / 'scripts/backend_build.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class LicenseGraphTests(unittest.TestCase):
    def test_runtime_graph_uses_active_tree_not_optional_metadata_edges(self):
        names = ['root', 'runtime', 'weak_optional', 'macro']
        packages = [{'id': n, 'name': n, 'version': '1.0.0'} for n in names]
        metadata = {'packages': packages, 'resolve': {'root': 'root', 'nodes': [
            {'id': 'root', 'deps': [{'pkg': 'weak_optional', 'dep_kinds': [{'kind': None}]}]}]}}
        self.assertEqual(module.runtime_packages(metadata, 'root v1.0.0\nruntime v1.0.0\n'), {'root', 'runtime'})
        with self.assertRaises(ValueError): module.runtime_packages(metadata, 'root v1.0.0\nunknown v1.0.0\n')
        packages.append({'id': 'other', 'name': 'runtime', 'version': '1.0.0'})
        with self.assertRaises(ValueError): module.runtime_packages(metadata, 'root v1.0.0\nruntime v1.0.0\n')

    def test_actual_target_graph_keeps_normal_dependencies(self):
        manifest = str(ROOT / 'backend-rs/Cargo.toml')
        result = subprocess.run(['cargo', 'metadata', '--offline', '--locked', '--format-version', '1',
            '--filter-platform', 'x86_64-unknown-linux-gnu', '--manifest-path', manifest], capture_output=True, text=True, check=True)
        metadata = json.loads(result.stdout)
        tree = subprocess.check_output(['cargo', 'tree', '--offline', '--locked', '--target', 'x86_64-unknown-linux-gnu',
            '--edges', 'normal,no-proc-macro', '--prefix', 'none', '--format', '{p}', '--manifest-path', manifest], text=True)
        ids = module.runtime_packages(metadata, tree)
        selected = {p['name'] for p in metadata['packages'] if p['id'] in ids}
        self.assertTrue({'sideloader-core', 'serde', 'reqwest', 'tokio'}.issubset(selected))
        self.assertTrue({'cc', 'serde_derive', 'tokio-macros', 'quinn', 'zlib-rs'}.isdisjoint(selected))

    def test_license_selection_retains_notice_and_and_expressions(self):
        docs = [{'title': 'LICENSE-MIT', 'text': 'MIT'}, {'title': 'COPYING', 'text': 'alternatives'}, {'title': 'NOTICE', 'text': 'notice'}]
        pkg = {'name': 'memchr', 'version': '2.8.3', 'license': 'Unlicense OR MIT'}
        license_name, chosen = module.select_license_documents(pkg, docs)
        self.assertEqual(license_name, 'MIT')
        self.assertEqual([d['title'] for d in chosen], ['LICENSE-MIT', 'NOTICE'])
        with self.assertRaises(ValueError): module.select_license_documents(pkg, docs[1:])
        license_name, selected = module.select_license_documents(
            {'name': 'adler2', 'version': '2.0.1', 'license': '0BSD OR MIT OR Apache-2.0'},
            [{'title': 'LICENSE-0BSD', 'text': 'Copyright (C) Jonas Schievink\nPermission'}, *docs])
        self.assertEqual(license_name, '0BSD')
        self.assertIn('Jonas Schievink', selected[0]['text'])
        for name, expression in [('ring', 'Apache-2.0 AND ISC'), ('moka', '(MIT OR Apache-2.0) AND Apache-2.0')]:
            result = module.select_license_documents({'name': name, 'version': '1', 'license': expression}, docs)
            self.assertEqual(result, (expression, docs))

    def test_generated_macro_notice_uses_resolved_version_and_original_text(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'LICENSE-MIT').write_text('Original macro license\n')
            macro = {'name': 'serde_derive', 'version': '1.2.3', 'license': 'MIT OR Apache-2.0',
                     'manifest_path': str(root / 'Cargo.toml')}
            node = {'deps': [{'pkg': 'macro', 'dep_kinds': [{'kind': None}]}]}
            documents = module.generated_macro_documents({'name': 'serde'}, node, {'macro': macro})
            self.assertEqual(documents[0]['text'], 'Original macro license\n')
            self.assertIn('serde_derive 1.2.3', documents[0]['title'])
            self.assertIn('https://crates.io/crates/serde_derive/1.2.3', documents[0]['title'])
            self.assertEqual(module.generated_macro_documents({'name': 'tokio'}, node, {'macro': macro}), [])
            (root / 'LICENSE-MIT').unlink()
            with self.assertRaises(FileNotFoundError): module.generated_macro_documents({'name': 'serde'}, node, {'macro': macro})

    def test_standard_library_override_is_version_bound_and_retains_original(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'COPYRIGHT-library.html'
            html = ('<title>Copyright notices for The Rust Standard Library</title>'
                    '<p><b>License:</b> Apache-2.0 OR MIT</p>'
                    '<p><b>License:</b> Unicode-3.0</p>'
                    '<p>Copyright: 1991-2024 Unicode, Inc</p>')
            path.write_text(html)
            env = {'SIDELOADER_RUST_STD_COPYRIGHT': str(path), 'SIDELOADER_RUST_STD_VERSION': '2.3.4'}
            def command(args, **kwargs):
                return 'rustc 2.3.4 (example)' if args[-1] == '--version' else '/fixture/sysroot'
            with patch.object(module.subprocess, 'check_output', side_effect=command):
                result = module.rust_standard_notice(env)
                self.assertEqual(result['version'], '2.3.4')
                self.assertEqual(result['documents'][1]['text'], html)
                self.assertIn('1991-2024 Unicode, Inc', result['documents'][0]['text'])
                self.assertEqual(result['noticeEvidence']['source'], 'explicit-versioned-override')
                with self.assertRaises(SystemExit):
                    module.rust_standard_notice({**env, 'SIDELOADER_RUST_STD_VERSION': '1.0.0'})
                path.write_text(html.replace('Unicode-3.0', 'Unknown-9.0'))
                with self.assertRaises(SystemExit): module.rust_standard_notice(env)
                path.unlink()
                with self.assertRaises(SystemExit): module.rust_standard_notice(env)

    def test_copyright_attribution_does_not_include_license_prose(self):
        docs = [{'text': 'Copyright (c) 2024 Example Person\nCopyright (c) [yyyy] [name of copyright owner]\nCopyright notice in the NOTICE file\nCopyright © 1991-2024 Unicode, Inc.\n'}]
        self.assertEqual(module.copyright_notices(docs), ['Copyright (c) 2024 Example Person', 'Copyright © 1991-2024 Unicode, Inc.'])

if __name__ == '__main__': unittest.main()
