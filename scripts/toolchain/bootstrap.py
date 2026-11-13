"""Provision a pinned, project-local Windows x64 native toolchain.

Only verified vendor archives are downloaded. SDK MSI databases are read, never
executed. No installer, registry, persistent PATH or security-policy changes run.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.parse
import urllib.request
import uuid
import zipfile

MANIFEST = Path(__file__).with_name('manifest.json')
HOSTS = {'static.rust-lang.org', 'download.visualstudio.microsoft.com'}
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def log(message):
    print(message, file=sys.stderr, flush=True)


def plain(path):
    path = Path(path).absolute()
    for current in (path, *path.parents):
        if current.is_symlink() or (current.exists() and getattr(current.stat(), 'st_file_attributes', 0) & 0x400):
            raise ValueError(f'Toolchain path contains a link or junction: {current}')
    return path


def inside(root, path):
    root, path = plain(root), plain(path)
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f'Toolchain path escapes its directory: {path}')
    return path


def member_path(name):
    name = urllib.parse.unquote(name).replace('\\', '/')
    parts = PurePosixPath(name).parts
    if not parts or name.startswith('/') or any(part in ('..', '') or ':' in part or '\x00' in part for part in parts):
        raise ValueError(f'Unsafe archive path: {name!r}')
    return parts


def vendor_url(url):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != 'https' or parsed.hostname not in HOSTS or parsed.username or parsed.password or parsed.fragment:
        raise ValueError(f'Download must use a permitted HTTPS vendor host: {url}')


def sha256(path):
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def load_manifest(path=MANIFEST):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if data.get('schema_version') != 1 or data.get('target') != 'x86_64-pc-windows-msvc':
        raise ValueError('Unsupported toolchain manifest schema/target')
    names = set()
    for item in artifacts(data):
        vendor_url(item['url'])
        parts = member_path(item['name'])
        if len(parts) != 1 or item['name'].casefold() in names or not re.fullmatch('[a-fA-F0-9]{64}', item['sha256']):
            raise ValueError('Invalid or duplicate toolchain artifact metadata')
        names.add(item['name'].casefold())
    return data


def artifacts(manifest):
    return [item for group in ('rust', 'msvc', 'sdk_msi', 'sdk_cab') for item in manifest[group]]


def valid_artifact(path, item):
    return path.is_file() and (not item.get('size') or path.stat().st_size == item['size']) and sha256(path) == item['sha256'].lower()


def ensure_artifact(item, cache, offline=False):
    vendor_url(item['url'])
    if len(member_path(item['name'])) != 1:
        raise ValueError('Artifact cache names must be plain filenames')
    cache = plain(cache)
    target = inside(cache, cache / item['name'])
    if valid_artifact(target, item):
        return target
    if offline:
        raise RuntimeError(f'Offline cache is missing or corrupt: {target.name}')
    cache.mkdir(parents=True, exist_ok=True)
    for attempt in range(3):
        pending = None
        try:
            log(f'Downloading verified native tool: {target.name}')
            with urllib.request.urlopen(item['url'], timeout=90) as response:
                vendor_url(response.geturl())
                with tempfile.NamedTemporaryFile(dir=cache, prefix='download-', suffix='.part', delete=False) as output:
                    pending = Path(output.name)
                    shutil.copyfileobj(response, output, length=1024 * 1024)
            if not valid_artifact(pending, item):
                raise RuntimeError(f'Checksum/size mismatch for {target.name}')
            pending.replace(target)
            return target
        except Exception:
            if attempt == 2:
                raise
        finally:
            if pending and pending.exists():
                try:
                    pending.unlink()
                except OSError as error:
                    log(f'Could not remove this incomplete download: {pending}: {error}')


def extract_vsix(archive, output):
    output = plain(output)
    with zipfile.ZipFile(archive) as package:
        selected = []
        for item in package.infolist():
            parts = member_path(item.filename)
            if stat.S_ISLNK(item.external_attr >> 16):
                raise ValueError(f'Unsupported archive link: {item.filename}')
            if parts[0] != 'Contents' or item.is_dir():
                continue
            if len(parts) < 2:
                raise ValueError('Empty VSIX content path')
            selected.append((item, inside(output, output.joinpath(*parts[1:]))))
        if sum(item.file_size for item, _ in selected) > 4 * 1024 ** 3:
            raise ValueError('VSIX expanded size exceeds the native-tool limit')
        for item, destination in selected:
            destination.parent.mkdir(parents=True, exist_ok=True)
            with package.open(item) as source, destination.open('wb') as target:
                shutil.copyfileobj(source, target)


def extract_rust(archive, output, subdirectory):
    output = plain(output)
    with tarfile.open(archive, 'r:xz') as package:
        selected = []
        for item in package.getmembers():
            parts = member_path(item.name)
            if not item.isfile() and not item.isdir():
                raise ValueError(f'Unsupported Rust archive link/member: {item.name}')
            if len(parts) < 3 or parts[1] != subdirectory or item.isdir():
                continue
            selected.append((item, inside(output, output.joinpath(*parts[2:]))))
        if not selected or sum(item.size for item, _ in selected) > 4 * 1024 ** 3:
            raise ValueError('Rust component is empty or its expanded size is invalid')
        for item, destination in selected:
            destination.parent.mkdir(parents=True, exist_ok=True)
            with package.extractfile(item) as source, destination.open('wb') as target:
                shutil.copyfileobj(source, target)


def msi_rows(path, query, types):
    import _msi
    database = _msi.OpenDatabase(str(path), 0)
    view = database.OpenView(query)
    view.Execute(None)
    result = []
    while (record := view.Fetch()) is not None:
        result.append(tuple(record.GetInteger(index) if kind is int else record.GetString(index)
                            for index, kind in enumerate(types, 1)))
    view.Close()
    return result


def select_sdk_files(tables):
    directories = {key: (parent, name.split(':')[0].split('|')[-1]) for key, parent, name in tables['Directory']}
    components = dict(tables['Component'])
    media = sorted(tables['Media'])

    def directory(key, parents=()):
        if not key or key == 'TARGETDIR':
            return PurePosixPath()
        if key in parents or key not in directories:
            raise ValueError(f'Invalid SDK directory reference: {key}')
        parent, name = directories[key]
        if name != '.':
            member_path(name)
        return directory(parent, (*parents, key)) / (name if name != '.' else '')

    selected = []
    for key, component, name, size, sequence in tables['File']:
        if len(member_path(key)) != 1:
            raise ValueError('Invalid SDK cabinet file identifier')
        name = name.split('|')[-1]
        if len(member_path(name)) != 1:
            raise ValueError('Invalid SDK filename')
        relative = directory(components[component]) / name
        member_path(relative.as_posix())
        lowered = '/' + relative.as_posix().lower()
        wanted = '/include/' in lowered or ('/lib/' in lowered and '/x64/' in lowered)
        wanted |= '/bin/' in lowered and '/x64/' in lowered and name.lower() in ('rc.exe', 'rcdll.dll')
        if not wanted:
            continue
        cabinet = next((cab for last, cab in media if sequence <= last and cab), None)
        if not cabinet or cabinet.startswith('#') or len(member_path(cabinet)) != 1:
            raise ValueError(f'Unsupported or missing SDK cabinet: {cabinet}')
        selected.append({'file_id': key, 'relative': relative.as_posix(), 'cabinet': cabinet, 'size': size})
    return selected


def sdk_plan(paths):
    selected = []
    definitions = {
        'Directory': ('Directory,Directory_Parent,DefaultDir', (str, str, str)),
        'Component': ('Component,Directory_', (str, str)),
        'Media': ('LastSequence,Cabinet', (int, str)),
        'File': ('File,Component_,FileName,FileSize,Sequence', (str, str, str, int, int)),
    }
    for path in paths:
        tables = {table: msi_rows(path, f'SELECT {columns} FROM {table}', types)
                  for table, (columns, types) in definitions.items()}
        selected.extend(select_sdk_files(tables))
    return selected


def run(args, environment=None, cwd=None, timeout=120):
    result = subprocess.run([str(arg) for arg in args], env=environment, cwd=cwd, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout, creationflags=NO_WINDOW)
    text = result.stdout.decode('utf-8', errors='replace')
    if result.returncode:
        raise RuntimeError(f'{Path(args[0]).name} failed ({result.returncode}): {text[-4000:]}')
    return text.strip()


def paths(root, manifest):
    vc = root / 'msvc/VC/Tools/MSVC' / manifest['msvc_version']
    sdk = root / 'msvc/Windows Kits/10'
    version = manifest['sdk_version']
    return vc, sdk, version, vc / 'bin/Hostx64/x64', sdk / 'bin' / version / 'x64'


def environment(root, manifest):
    vc, sdk, version, compiler, resources = paths(root, manifest)
    env = dict(os.environ)
    windows = Path(env.get('SystemRoot', r'C:\Windows')) / 'System32'
    env['PATH'] = os.pathsep.join(map(str, [root / 'rust-local/bin', compiler, resources, windows]))
    env['LIB'] = ';'.join(map(str, [vc / 'lib/x64', sdk / 'Lib' / version / 'um/x64', sdk / 'Lib' / version / 'ucrt/x64']))
    env['INCLUDE'] = ';'.join(map(str, [vc / 'include', *(sdk / 'Include' / version / part for part in ('ucrt', 'shared', 'um', 'winrt'))]))
    for key in ('LIBPATH', 'CL', '_CL_', 'LINK', 'RUSTC_WRAPPER', 'RUSTC_WORKSPACE_WRAPPER', 'RUSTFLAGS', 'CARGO_ENCODED_RUSTFLAGS'):
        env.pop(key, None)
    return env


def required(root, manifest):
    vc, sdk, version, compiler, resources = paths(root, manifest)
    return [root / 'rust-local/bin/rustc.exe', root / 'rust-local/bin/cargo.exe',
            compiler / 'cl.exe', compiler / 'link.exe', compiler / 'lib.exe', compiler / 'c1xx.dll', compiler / 'c2.dll',
            compiler / 'vcruntime140.dll', compiler / 'msvcp140.dll', compiler / 'vcruntime140_1.dll',
            vc / 'include/vcruntime.h', vc / 'lib/x64/libcmt.lib', vc / 'lib/x64/oldnames.lib', vc / 'lib/x64/msvcprt.lib',
            sdk / 'Include' / version / 'um/Windows.h', sdk / 'Include' / version / 'shared/winapifamily.h',
            sdk / 'Include' / version / 'ucrt/stdio.h',
            sdk / 'Lib' / version / 'um/x64/kernel32.lib', sdk / 'Lib' / version / 'ucrt/x64/libucrt.lib',
            resources / 'rc.exe', resources / 'rcdll.dll']


def component_status(root, manifest):
    root = plain(root)
    expected = [inside(root, path) for path in required(root, manifest)]
    missing = [str(path) for path in expected if not path.is_file() or not path.stat().st_size]
    standard = inside(root, root / 'rust-local/lib/rustlib' / manifest['target'] / 'lib')
    standard_libraries = [inside(root, library) for library in standard.glob('libstd-*.rlib')]
    if not standard_libraries:
        missing.append(str(root / 'rust-local/lib/rustlib' / manifest['target'] / 'lib/libstd-*.rlib'))
    result = {'ready': False, 'root': str(root), 'missing': missing, 'versions': {}}
    if missing:
        return result
    try:
        env = environment(root, manifest)
        for binary in ('rustc', 'cargo'):
            version = run([root / 'rust-local/bin' / f'{binary}.exe', '--version'], env, timeout=20)
            if not version.startswith(f'{binary} {manifest["rust_version"]} '):
                raise RuntimeError(f'Unexpected {binary} version: {version}')
            result['versions'][binary] = version
        compiler = run([paths(root, manifest)[3] / 'cl.exe'], env, timeout=20)
        if '19.44.' not in compiler:
            raise RuntimeError(f'Unexpected MSVC compiler: {compiler}')
        result['versions']['msvc'] = manifest['msvc_version']
        result['versions']['sdk'] = manifest['sdk_version']
        result['ready'] = True
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        result['error'] = str(error)
    return result


def manifest_fingerprint(manifest):
    contents = json.dumps(manifest, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(contents).hexdigest()


def write_receipt(root, manifest, proof):
    if proof != {'cxx': True, 'rust': True}:
        raise ValueError('Both native compile/link/run checks must succeed before recording readiness')
    entries = []
    for component in ('rust-local', 'msvc'):
        start = inside(root, root / component)
        for folder, directories, files in os.walk(start, followlinks=False):
            for name in (*directories, *files):
                path = Path(folder) / name
                metadata = path.lstat()
                if path.is_symlink() or getattr(metadata, 'st_file_attributes', 0) & 0x400:
                    raise ValueError(f'Linked toolchain content cannot be recorded: {path}')
                if stat.S_ISREG(metadata.st_mode):
                    entries.append({'path': path.relative_to(root).as_posix(), 'size': metadata.st_size})
    receipt = {'schema_version': 1, 'manifest_sha256': manifest_fingerprint(manifest),
               'native_link_verified': True, 'native_checks': proof,
               'inventory': sorted(entries, key=lambda item: item['path'])}
    destination = inside(root, root / 'receipt.json')
    pending = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=root, prefix='receipt-', suffix='.pending', delete=False) as output:
            pending = Path(output.name)
            json.dump(receipt, output, ensure_ascii=False)
        pending.replace(destination)
    finally:
        if pending and pending.exists():
            pending.unlink()


def validate_receipt(root, manifest):
    path = inside(root, root / 'receipt.json')
    if not path.is_file() or path.stat().st_size > 16 * 1024 ** 2:
        raise ValueError('Native bootstrap receipt is missing; run setup.cmd to provision and verify the toolchain')
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        raise ValueError('Native bootstrap receipt must contain a JSON object')
    if (data.get('schema_version') != 1 or data.get('manifest_sha256') != manifest_fingerprint(manifest)
            or data.get('native_link_verified') is not True or data.get('native_checks') != {'cxx': True, 'rust': True}):
        raise ValueError('Native bootstrap receipt does not match the pinned manifest and compile/link proof')
    inventory = data.get('inventory')
    if not isinstance(inventory, list) or not inventory or len(inventory) > 50000:
        raise ValueError('Native bootstrap file inventory is missing or invalid')
    seen = set()
    for item in inventory:
        if not isinstance(item, dict) or not isinstance(item.get('path'), str):
            raise ValueError('Native bootstrap inventory entries must contain a relative file path')
        parts = member_path(item['path'])
        key = item['path'].casefold()
        if (parts[0] not in ('rust-local', 'msvc') or key in seen
                or type(item.get('size')) is not int or item['size'] < 0):
            raise ValueError('Native bootstrap inventory contains an invalid/duplicate entry')
        seen.add(key)
        installed = inside(root, root / Path(item['path']))
        if not installed.is_file() or installed.stat().st_size != item['size']:
            raise ValueError(f'Installed toolchain file is missing or changed: {installed}')
    if any(path.relative_to(root).as_posix().casefold() not in seen for path in required(root, manifest)):
        raise ValueError('Native bootstrap inventory omits a required compiler/SDK file')


def status(root, manifest):
    result = component_status(root, manifest)
    if result['ready']:
        try:
            validate_receipt(Path(root), manifest)
            result['native_link_verified'] = True
        except (OSError, ValueError, KeyError, TypeError) as error:
            result['ready'] = False
            result['error'] = str(error)
    return result


def verify_link(root, manifest):
    env = environment(root, manifest)
    compiler = paths(root, manifest)[3]
    with tempfile.TemporaryDirectory(prefix='native-smoke-', dir=root) as temporary:
        work = Path(temporary)
        source = work / 'native.cpp'
        source.write_text('#include <windows.h>\n#include <stdio.h>\nint main(){ puts("native-toolchain-ok"); return 0; }\n', encoding='utf-8')
        output = work / 'native.exe'
        run([compiler / 'cl.exe', '/nologo', '/MT', str(source), f'/Fo{work / "native.obj"}', f'/Fe{output}'], env, work)
        if 'native-toolchain-ok' not in run([output], env, work):
            raise RuntimeError('The local C++/Windows SDK smoke executable did not run')
        rust = work / 'native.rs'
        rust.write_text('fn main() { println!("rust-toolchain-ok"); }\n', encoding='utf-8')
        executable = work / 'rust-native.exe'
        run([root / 'rust-local/bin/rustc.exe', str(rust), '-o', str(executable), '-C', f'linker={compiler / "link.exe"}'], env, work)
        if 'rust-toolchain-ok' not in run([executable], env, work):
            raise RuntimeError('The local Rust/MSVC smoke executable did not run')
    return {'cxx': True, 'rust': True}


def provision(stage, manifest, cache, offline=False):
    for artifact in manifest['rust']:
        log(f'Preparing Rust component: {artifact["component"]}')
        extract_rust(ensure_artifact(artifact, cache, offline), stage / 'rust-local', artifact['subdirectory'])
    for artifact in manifest['msvc']:
        log(f'Preparing MSVC package: {artifact["name"]}')
        extract_vsix(ensure_artifact(artifact, cache, offline), stage / 'msvc')
    msis = [ensure_artifact(artifact, cache, offline) for artifact in manifest['sdk_msi']]
    selected = sdk_plan(msis)
    wanted = {item['cabinet'] for item in selected}
    cabinets = {item['name']: item for item in manifest['sdk_cab']}
    if wanted - cabinets.keys():
        raise RuntimeError(f'SDK cabinets missing from the pinned metadata: {sorted(wanted - cabinets.keys())}')
    expand = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/expand.exe'
    for name in sorted(wanted):
        log(f'Preparing Windows SDK cabinet: {name}')
        archive = ensure_artifact(cabinets[name], cache, offline)
        expanded = inside(stage, stage / '_expanded' / Path(name).stem)
        expanded.mkdir(parents=True, exist_ok=True)
        run([expand, '-F:*', archive, expanded], timeout=180)
        for item in (entry for entry in selected if entry['cabinet'] == name):
            source = inside(expanded, expanded / item['file_id'])
            destination = inside(stage / 'msvc', stage / 'msvc' / Path(item['relative']))
            if not source.is_file() or source.stat().st_size != item['size']:
                raise RuntimeError(f'SDK payload is missing/truncated: {item["relative"]}')
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
    check = component_status(stage, manifest)
    if not check['ready']:
        raise RuntimeError(f'The extracted native toolchain is incomplete: {json.dumps(check)}')
    proof = verify_link(stage, manifest)
    write_receipt(stage, manifest, proof)


def promote(stage, root):
    moved, installed = {}, []
    components = ('rust-local', 'msvc', 'receipt.json') if (stage / 'receipt.json').exists() else ('rust-local', 'msvc')
    try:
        for name in components:
            destination = inside(root, root / name)
            if destination.exists():
                backup = inside(root, root / f'.{name}.backup-{uuid.uuid4().hex}')
                destination.rename(backup)
                moved[name] = backup
        for name in components:
            (stage / name).rename(root / name)
            installed.append(name)
    except OSError:
        for name in reversed(installed):
            (root / name).rename(stage / name)
        for name, backup in moved.items():
            backup.rename(root / name)
        raise
    return {name: str(path) for name, path in moved.items()}


def install(repo, manifest, cache, offline=False):
    repo = plain(repo)
    root = inside(repo, repo / '.verification/toolchain')
    cache = inside(repo, cache)
    current = status(root, manifest)
    if current['ready']:
        log('The pinned local native toolchain is already ready.')
        return current
    root.mkdir(parents=True, exist_ok=True)
    stage = inside(root, root / f'.stage-{uuid.uuid4().hex}')
    stage.mkdir()
    try:
        provision(stage, manifest, cache, offline)
        backups = promote(stage, root)
        source_env = Path(__file__).with_name('env.ps1')
        shutil.copyfile(plain(source_env), inside(root, root / 'env.ps1'))
        result = status(root, manifest)
        if not result['ready']:
            raise RuntimeError('Promoted native toolchain failed its final check')
        result['backups'] = backups
        result['manifest_sha256'] = manifest_fingerprint(manifest)
        return result
    finally:
        # This is exclusively our generated stage; existing tools/cache/backups are never removed.
        if stage.exists():
            inside(root, stage)
            try:
                shutil.rmtree(stage)
            except OSError as error:
                log(f'Could not clean this generated staging directory: {stage}: {error}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo-root', type=Path, required=True)
    parser.add_argument('--cache-dir', type=Path)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--offline', action='store_true')
    options = parser.parse_args()
    if not options.repo_root.is_absolute():
        parser.error('--repo-root must be an absolute checkout path')
    manifest = load_manifest()
    repo = plain(options.repo_root)
    root = inside(repo, repo / '.verification/toolchain')
    try:
        if options.check:
            result = status(root, manifest)
        else:
            if os.name != 'nt' or sys.maxsize <= 2 ** 32:
                raise RuntimeError('The native bootstrap requires Windows x64 and 64-bit Python 3.11+')
            result = install(repo, manifest, options.cache_dir or root / 'downloads', options.offline)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return 0 if result['ready'] else 1
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
        print(json.dumps({'ready': False, 'root': str(root), 'error': str(error)}, ensure_ascii=False), flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
