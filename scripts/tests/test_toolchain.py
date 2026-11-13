"""Offline regression tests for the tracked native-toolchain bootstrap."""
import hashlib
import importlib.util
import io
import json
import os
import pathlib
import subprocess
import tarfile
import tempfile
import unittest
import zipfile
from unittest.mock import patch

SOURCE = pathlib.Path(__file__).resolve().parents[1] / 'toolchain' / 'bootstrap.py'


class ToolchainTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SOURCE.is_file(), 'Tracked native-toolchain bootstrap is missing')
        spec = importlib.util.spec_from_file_location('aivd_toolchain', SOURCE)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = pathlib.Path(self.temporary.name)

    def test_verified_download_replaces_corrupt_cache_only_after_checksum_matches(self):
        contents = b'verified vendor archive'
        artifact = {'name': 'payload.zip', 'url': 'https://download.visualstudio.microsoft.com/a/payload.zip',
                    'sha256': hashlib.sha256(contents).hexdigest(), 'size': len(contents)}
        cache = self.root / 'downloads'
        cache.mkdir()
        (cache / artifact['name']).write_bytes(b'broken')
        response = io.BytesIO(contents)
        response.geturl = lambda: artifact['url']
        with patch.object(self.module.urllib.request, 'urlopen', return_value=response):
            path = self.module.ensure_artifact(artifact, cache)
        self.assertEqual(path.read_bytes(), contents)
        self.assertEqual(sorted(p.name for p in cache.iterdir()), ['payload.zip'])
        with patch.object(self.module.urllib.request, 'urlopen', side_effect=AssertionError('no network')):
            self.assertEqual(self.module.ensure_artifact(artifact, cache, offline=True), path)

    def test_offline_bad_cache_and_nonvendor_url_fail_without_network(self):
        cache = self.root / 'downloads'
        cache.mkdir()
        item = {'name': 'payload.zip', 'url': 'https://download.visualstudio.microsoft.com/payload.zip',
                'sha256': '0' * 64, 'size': 10}
        (cache / item['name']).write_bytes(b'partial')
        with patch.object(self.module.urllib.request, 'urlopen', side_effect=AssertionError('no network')):
            with self.assertRaisesRegex(RuntimeError, 'offline|Offline'):
                self.module.ensure_artifact(item, cache, offline=True)
        item['url'] = 'https://example.org/payload.zip'
        with self.assertRaisesRegex(ValueError, 'host|vendor|HTTPS'):
            self.module.ensure_artifact(item, cache)
        self.assertEqual((cache / item['name']).read_bytes(), b'partial')

    def test_vsix_extraction_decodes_paths_and_rejects_traversal_or_links(self):
        good = self.root / 'good.vsix'
        with zipfile.ZipFile(good, 'w') as archive:
            archive.writestr('Contents/VC/Tools/my%20tool/cl.exe', b'compiler')
            archive.writestr('manifest.json', b'not copied')
        output = self.root / 'out'
        self.module.extract_vsix(good, output)
        self.assertEqual((output / 'VC/Tools/my tool/cl.exe').read_bytes(), b'compiler')
        self.assertFalse((output / 'manifest.json').exists())
        for name in ('Contents/%2e%2e/escape', 'Contents/C:/escape', 'Contents/../escape'):
            with self.subTest(name=name):
                bad = self.root / 'bad.vsix'
                with zipfile.ZipFile(bad, 'w') as archive:
                    archive.writestr(name, b'bad')
                with self.assertRaises(ValueError):
                    self.module.extract_vsix(bad, output)
        bad = self.root / 'link.vsix'
        with zipfile.ZipFile(bad, 'w') as archive:
            link = zipfile.ZipInfo('Contents/link')
            link.create_system = 3
            link.external_attr = 0o120777 << 16
            archive.writestr(link, '../escape')
        with self.assertRaisesRegex(ValueError, 'link|Unsupported'):
            self.module.extract_vsix(bad, output)

    def test_rust_components_merge_the_correct_subtree_without_installer_files(self):
        output = self.root / 'rust'
        for component, subdir, filename in [('cargo', 'cargo', 'bin/cargo.exe'),
                                            ('rustc', 'rustc', 'bin/rustc.exe'),
                                            ('rust-std', 'rust-std-x86_64-pc-windows-msvc', 'lib/rustlib/std.rlib')]:
            artifact = self.root / f'{component}.tar.xz'
            with tarfile.open(artifact, 'w:xz') as archive:
                data = component.encode()
                entry = tarfile.TarInfo(f'package/{subdir}/{filename}')
                entry.size = len(data)
                archive.addfile(entry, io.BytesIO(data))
                ignored = tarfile.TarInfo('package/install.sh')
                ignored.size = 1
                archive.addfile(ignored, io.BytesIO(b'x'))
            self.module.extract_rust(artifact, output, subdir)
            self.assertEqual((output / filename).read_bytes(), component.encode())
        self.assertFalse((output / 'install.sh').exists())
        bad = self.root / 'bad.tar.xz'
        with tarfile.open(bad, 'w:xz') as archive:
            entry = tarfile.TarInfo('package/rustc/link')
            entry.type = tarfile.SYMTYPE
            entry.linkname = '../../escape'
            archive.addfile(entry)
        with self.assertRaisesRegex(ValueError, 'link|Unsupported'):
            self.module.extract_rust(bad, output, 'rustc')

    def test_sdk_msi_table_mapping_selects_x64_headers_libs_and_resource_compiler(self):
        tables = {
            'Directory': [('TARGETDIR', '', 'SourceDir'), ('kits', 'TARGETDIR', 'Windows Kits'),
                          ('ten', 'kits', '10'), ('inc', 'ten', 'Include'), ('lib', 'ten', 'Lib'),
                          ('lib64', 'lib', 'x64'), ('lib86', 'lib', 'x86'), ('bin', 'ten', 'bin'),
                          ('bin64', 'bin', 'x64')],
            'Component': [('h', 'inc'), ('l64', 'lib64'), ('l86', 'lib86'), ('b64', 'bin64')],
            'Media': [(10, 'headers.cab'), (20, 'libraries.cab'), (30, 'tools.cab')],
            'File': [('h1', 'h', 'tiny.h', 5, 1), ('l1', 'l64', 'kernel32.lib', 8, 11),
                     ('l2', 'l86', 'kernel32.lib', 9, 12), ('b1', 'b64', 'rc.exe', 10, 21),
                     ('b2', 'b64', 'unneeded.exe', 10, 22)],
        }
        selected = self.module.select_sdk_files(tables)
        self.assertEqual({entry['file_id'] for entry in selected}, {'h1', 'l1', 'b1'})
        self.assertEqual({entry['cabinet'] for entry in selected}, {'headers.cab', 'libraries.cab', 'tools.cab'})
        tables['Directory'].append(('bad', 'ten', '../escape'))
        tables['Component'].append(('bad', 'bad'))
        tables['File'].append(('bad', 'bad', 'test.h', 1, 2))
        with self.assertRaises(ValueError):
            self.module.select_sdk_files(tables)

    def test_component_swap_failure_restores_all_existing_tools(self):
        install = self.root / 'toolchain'
        stage = install / '.stage'
        for component in ('rust-local', 'msvc'):
            (install / component).mkdir(parents=True)
            (install / component / 'old.txt').write_text('old')
            (stage / component).mkdir(parents=True)
            (stage / component / 'new.txt').write_text('new')
        rename = pathlib.Path.rename

        def fail_second(source, destination):
            if source == stage / 'msvc':
                raise OSError('simulated promotion failure')
            return rename(source, destination)

        with patch.object(pathlib.Path, 'rename', fail_second):
            with self.assertRaisesRegex(OSError, 'promotion'):
                self.module.promote(stage, install)
        for component in ('rust-local', 'msvc'):
            self.assertEqual((install / component / 'old.txt').read_text(), 'old')
            self.assertFalse((install / component / 'new.txt').exists())

    def test_missing_local_tools_are_not_replaced_with_path_tools(self):
        manifest = self.module.load_manifest()
        with patch.object(self.module.subprocess, 'run', side_effect=AssertionError('No global tool probes')):
            result = self.module.status(self.root, manifest)
        self.assertFalse(result['ready'])
        self.assertTrue(any('rustc.exe' in entry for entry in result['missing']))

    def test_readiness_requires_shared_sdk_headers_and_oldnames_for_native_link(self):
        manifest = self.module.load_manifest()
        vc, sdk, version, _, _ = self.module.paths(self.root, manifest)
        missing = {vc / 'lib/x64/oldnames.lib', sdk / 'Include' / version / 'shared/winapifamily.h'}
        for path in self.module.required(self.root, manifest):
            if path not in missing:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'fixture')
        standard = self.root / 'rust-local/lib/rustlib' / manifest['target'] / 'lib/libstd-fixture.rlib'
        standard.parent.mkdir(parents=True, exist_ok=True)
        standard.write_bytes(b'standard library fixture')
        with patch.object(self.module.subprocess, 'run', side_effect=AssertionError('Do not probe incomplete tools')):
            result = self.module.status(self.root, manifest)
        self.assertFalse(result['ready'])
        self.assertTrue({str(path) for path in missing}.issubset(result['missing']))

    def test_readiness_requires_matching_link_proof_and_full_file_inventory(self):
        manifest = self.module.load_manifest()
        for path in self.module.required(self.root, manifest):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'fixture')
        standard = self.root / 'rust-local/lib/rustlib' / manifest['target'] / 'lib/libstd-fixture.rlib'
        standard.parent.mkdir(parents=True, exist_ok=True)
        standard.write_bytes(b'std')
        non_sentinel = self.root / 'msvc/VC/Tools/MSVC' / manifest['msvc_version'] / 'lib/x64/user32-extra.lib'
        non_sentinel.write_bytes(b'non-sentinel library')

        def version(args, *_args, **_kwargs):
            return {'rustc.exe': 'rustc 1.99.1 (fixture)', 'cargo.exe': 'cargo 1.99.1 (fixture)',
                    'cl.exe': 'Compiler Version 19.44.35230 for x64'}[pathlib.Path(args[0]).name]

        with patch.object(self.module, 'run', side_effect=version):
            self.assertFalse(self.module.status(self.root, manifest)['ready'], 'A legacy folder without bootstrap proof is not ready')
            self.module.write_receipt(self.root, manifest, {'cxx': True, 'rust': True})
            self.assertTrue(self.module.status(self.root, manifest)['ready'])
            non_sentinel.unlink()
            self.assertFalse(self.module.status(self.root, manifest)['ready'], 'A missing non-sentinel library invalidates installation')
            non_sentinel.write_bytes(b'non-sentinel library')
            changed = json.loads(json.dumps(manifest))
            changed['rust'][0]['sha256'] = 'f' * 64
            self.assertFalse(self.module.status(self.root, changed)['ready'])
            receipt = json.loads((self.root / 'receipt.json').read_text())
            for broken in ([], dict(receipt, inventory=[None]), dict(receipt, inventory=[{'path': None, 'size': 1}])):
                (self.root / 'receipt.json').write_text(json.dumps(broken))
                self.assertFalse(self.module.status(self.root, manifest)['ready'])
            receipt['native_link_verified'] = False
            (self.root / 'receipt.json').write_text(json.dumps(receipt))
            self.assertFalse(self.module.status(self.root, manifest)['ready'])

    def test_containment_rejects_parent_escape_and_existing_links(self):
        with self.assertRaises(ValueError):
            self.module.inside(self.root, self.root / '..' / 'escape')
        linked = self.root / 'linked'
        other = tempfile.TemporaryDirectory()
        self.addCleanup(other.cleanup)
        outside = pathlib.Path(other.name)
        sentinel = outside / 'keep.txt'
        sentinel.write_text('outside fixture')
        try:
            linked.symlink_to(outside, target_is_directory=True)
        except OSError:
            if os.name != 'nt':
                self.skipTest('Creating symlink is not permitted by this test host')
            command = pathlib.Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/cmd.exe'
            result = subprocess.run([str(command), '/d', '/c', 'mklink', '/J', str(linked), str(outside)], capture_output=True)
            if result.returncode:
                self.skipTest('Creating symlink/junction is not permitted by this test host')
        with self.assertRaises(ValueError):
            self.module.inside(self.root, linked / 'escape')
        self.temporary.cleanup()
        self.assertEqual(sentinel.read_text(), 'outside fixture')


if __name__ == '__main__':
    unittest.main()
