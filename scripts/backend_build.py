"""Build and identify the Linux Rust runtime, then collect locked crate notices."""
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tomllib

ROOT = Path(__file__).resolve().parents[1]
CRATE = ROOT / "backend-rs"


def source_fingerprint():
    files = [CRATE / name for name in ("Cargo.toml", "Cargo.lock", "build.rs")]
    files += [path for path in (CRATE / "src").rglob("*") if path.suffix in (".rs", ".json")]
    digest = hashlib.sha256()
    for path in sorted(files):
        digest.update(path.relative_to(CRATE).as_posix().encode() + b"\0")
        digest.update(path.read_bytes() + b"\0")
    return digest.hexdigest()


def runtime_packages(metadata, tree):
    packages = metadata["packages"]
    by_name_version = {}
    for package in packages:
        by_name_version.setdefault((package["name"], package["version"]), []).append(package["id"])
    selected = set()
    for line in tree.splitlines():
        match = re.match(r"^(\S+) v(\S+)(?:\s.*)?$", line.strip())
        if not match:
            raise ValueError("Unrecognized cargo tree entry: " + line)
        matches = by_name_version.get(match.groups(), [])
        if len(matches) != 1:
            raise ValueError("Cargo tree package identity is missing or ambiguous: " + line)
        selected.add(matches[0])
    if metadata["resolve"]["root"] not in selected:
        raise ValueError("Cargo tree did not include the root package")
    return selected


def copyright_notices(documents):
    # Only explicit year-and-holder notices, not Apache template text or authors metadata.
    pattern = re.compile(r"^\s*Copyright\s*(?:\(c\)|©)?\s*\d{4}(?:\s*[-–,]\s*\d{4})*\s+\S", re.I)
    return list(dict.fromkeys(line.strip() for doc in documents for line in doc["text"].splitlines()
                              if pattern.match(line) and not re.search(r"\[(?:yyyy|name of copyright owner)\]", line, re.I)))


class _NoticeText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.hidden += 1
        elif tag in ('p', 'h1', 'h2', 'li', 'div'):
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.hidden -= 1
        elif tag in ('p', 'h1', 'h2', 'li', 'div'):
            self.parts.append('\n')

    def handle_data(self, text):
        if not self.hidden:
            self.parts.append(text)


def rust_standard_notice(environment):
    rustc = environment.get('RUSTC', 'rustc')
    version = subprocess.check_output([rustc, '--version'], env=environment, text=True).split()[1]
    sysroot = Path(subprocess.check_output([rustc, '--print', 'sysroot'], env=environment, text=True).strip()).resolve()
    override = environment.get('SIDELOADER_RUST_STD_COPYRIGHT')
    if override:
        if environment.get('SIDELOADER_RUST_STD_VERSION') != version:
            raise SystemExit('Set SIDELOADER_RUST_STD_VERSION to the matching rustc version for the explicit standard-library notice')
        candidates = [Path(override)]
        source_kind = 'explicit-versioned-override'
    elif sysroot == Path('/usr'):
        try:
            package = subprocess.check_output(['pacman', '-Q', 'rust'], text=True).strip().split()[1]
        except (OSError, subprocess.CalledProcessError, IndexError) as error:
            raise SystemExit('Cannot verify system Rust package version; set SIDELOADER_RUST_STD_COPYRIGHT and SIDELOADER_RUST_STD_VERSION') from error
        if package.split(':')[-1].split('-')[0] != version:
            raise SystemExit('System Rust package version differs from rustc; provide version-matched standard-library notice override')
        candidates = [Path('/usr/share/licenses/rust/COPYRIGHT-library.html.rustc')]
        source_kind = 'arch-rust-package'
    else:
        candidates = [sysroot / 'share/doc/rust/COPYRIGHT-library.html', sysroot / 'share/doc/rust/html/COPYRIGHT-library.html']
        source_kind = 'rust-sysroot'
    path = next((p for p in candidates if p.is_file()), None)
    if path is None:
        raise SystemExit('Rust standard-library copyright notice missing; install matching toolchain docs or set SIDELOADER_RUST_STD_COPYRIGHT and SIDELOADER_RUST_STD_VERSION')
    raw = path.read_bytes()
    if len(raw) > 1024 * 1024:
        raise SystemExit('Rust standard-library notice exceeds 1 MiB')
    html = raw.decode('utf-8')
    if 'Copyright notices for The Rust Standard Library' not in html:
        raise SystemExit('Not a Rust standard-library copyright document')
    parser = _NoticeText()
    parser.feed(html)
    plain = '\n'.join(line.strip() for line in ''.join(parser.parts).splitlines() if line.strip())
    if 'Unicode-3.0' not in plain or 'Apache-2.0 OR MIT' not in plain or '1991-2024 Unicode, Inc' not in plain:
        raise SystemExit('Review Rust standard-library notices and Unicode license materials for this toolchain')
    found = {re.sub(r'\s+', ' ', item.strip()) for item in re.findall(r'<b>License:</b>\s*([^<]+)', html)}
    known = {'Apache-2.0 OR MIT', 'Unicode-3.0', 'BSD-2-Clause AND (Apache-2.0 OR MIT)'}
    if not found or found - known:
        raise SystemExit('Review unrecognized Rust standard-library licenses: ' + ', '.join(sorted(found - known)))
    documents = [{'title': 'COPYRIGHT-library (readable)', 'text': plain},
                 {'title': 'COPYRIGHT-library.html (original)', 'text': html}]
    for filename in ('rust-std-MIT.txt', 'rust-std-Apache-2.0.txt', 'rust-std-BSD-2-Clause.txt', 'rust-unicode-3.0.txt'):
        text = (ROOT / 'licenses' / filename).read_text(encoding='utf-8')
        if not text.strip():
            raise SystemExit('Missing Rust standard-library license text: ' + filename)
        documents.append({'title': filename, 'text': text})
    return {'id': 'rust-standard-library', 'name': 'Rust standard library', 'version': version,
            'group': 'rust', 'host': False, 'license': 'Apache-2.0 OR MIT; Unicode-3.0',
            'source': 'https://github.com/rust-lang/rust/tree/' + version + '/library',
            'attribution': 'The Rust Project Contributors', 'documents': documents,
            'noticeEvidence': {'source': source_kind, 'sha256': hashlib.sha256(raw).hexdigest()}}


def generated_macro_documents(package, node, packages):
    known = {'serde': ('serde_derive', 'LICENSE-MIT'), 'tokio': ('tokio-macros', 'LICENSE')}
    if package['name'] not in known:
        return []
    macro_name, filename = known[package['name']]
    documents = []
    for dependency in node['deps']:
        macro = packages[dependency['pkg']]
        if macro['name'] != macro_name or not any(kind.get('kind') is None for kind in dependency['dep_kinds']):
            continue
        if not re.search(r'\bMIT\b', macro.get('license') or ''):
            raise ValueError('Recheck generated-code macro license: ' + macro_name)
        text = (Path(macro['manifest_path']).parent / filename).read_text(encoding='utf-8')
        if not text.strip():
            raise ValueError('Missing generated-code macro license: ' + macro_name)
        source = f"https://crates.io/crates/{macro_name}/{macro['version']}"
        documents.append({'title': f"{macro_name} {macro['version']} {filename} ({source})", 'text': text})
    return documents


def select_license_documents(package, documents):
    selections = {
        ('adler2', '2.0.1'): ('0BSD', {'LICENSE-0BSD'}),
        ('aho-corasick', '1.1.5'): ('MIT', {'LICENSE-MIT'}),
        ('memchr', '2.8.3'): ('MIT', {'LICENSE-MIT'}),
        ('ryu', '1.0.23'): ('BSL-1.0', {'LICENSE-BOOST'}),
        ('tinyvec', '1.13.3'): ('Zlib', {'LICENSE-ZLIB.md'}),
    }
    choice = selections.get((package['name'], package['version']))
    if not choice:
        return package['license'], documents
    chosen, names = choice
    if chosen not in package['license'] or not names <= {doc['title'] for doc in documents}:
        raise ValueError('Recheck selected license materials: ' + package['name'])
    return chosen, [doc for doc in documents if doc['title'] in names or doc['title'].lower().startswith('notice')]


def crate_notices(environment, target):
    result = subprocess.run(["cargo", "metadata", "--locked", "--format-version", "1", "--filter-platform", target,
        "--manifest-path", str(CRATE / "Cargo.toml")], cwd=ROOT, env=environment, check=True, capture_output=True, text=True)
    metadata = json.loads(result.stdout)
    packages = {p["id"]: p for p in metadata["packages"]}
    nodes = {node['id']: node for node in metadata['resolve']['nodes']}
    tree = subprocess.check_output(["cargo", "tree", "--locked", "--target", target, "--edges", "normal,no-proc-macro",
        "--prefix", "none", "--format", "{p}", "--manifest-path", str(CRATE / "Cargo.toml")], cwd=ROOT, env=environment, text=True)
    reachable = runtime_packages(metadata, tree)
    entries = []
    for identifier in sorted(reachable, key=lambda key: (packages[key]["name"], packages[key]["version"])):
        package = packages[identifier]
        if package["name"] == "sideloader-core":
            continue
        directory = Path(package["manifest_path"]).parent
        candidates = [p for p in directory.iterdir() if p.is_file() and re.match(r"^(licen[cs]e|copying|notice)([._-]|$)", p.name, re.I)]
        if package.get("license_file"):
            candidates.append(directory / package["license_file"])
        for child in (directory / "LICENSES", directory / "licenses"):
            if child.is_dir():
                candidates.extend(p for p in child.rglob("*") if p.is_file() and p.stat().st_size < 256 * 1024)
        documents = []
        for path in sorted(set(candidates)):
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            if text.strip():
                documents.append({"title": path.relative_to(directory).as_posix(), "text": text})
        if not documents or not package.get("license"):
            raise SystemExit(f"Missing crate license materials: {package['name']} {package['version']}")
        chosen_license, documents = select_license_documents(package, documents)
        if package['name'] == 'ring' and package['version'] == '0.17.14':
            for name in ('src/polyfill/once_cell/LICENSE-MIT', 'src/polyfill/once_cell/LICENSE-APACHE', 'third_party/fiat/LICENSE'):
                text = (directory / name).read_text(encoding='utf-8')
                if not text.strip():
                    raise ValueError('Missing ring source license: ' + name)
                documents.append({'title': name, 'text': text})
        copyright_lines = copyright_notices(documents)
        documents.extend(generated_macro_documents(package, nodes[identifier], packages))
        entries.append({"id": f"rust:{package['name']}:{package['version']}", "name": package["name"],
            "version": package["version"], "group": "rust", "host": False, "license": chosen_license,
            "source": f"https://crates.io/crates/{package['name']}/{package['version']}",
            "attribution": "\n".join(copyright_lines), "documents": documents})
    return {"entries": entries, "standardLibrary": rust_standard_notice(environment)}


def main():
    version = json.loads((ROOT / "package.json").read_text())["version"]
    if tomllib.loads((CRATE / "Cargo.toml").read_text())["package"]["version"] != version:
        raise SystemExit("Rust and package.json versions differ")
    target = os.environ.get("SIDELOADER_RUST_TARGET", "x86_64-unknown-linux-gnu")
    if target not in ("x86_64-unknown-linux-gnu", "x86_64-unknown-linux-musl"):
        raise SystemExit(f"Unsupported Steam Deck runtime target: {target}")
    environment = os.environ.copy()
    environment.setdefault("CARGO_REGISTRIES_CRATES_IO_PROTOCOL", "sparse")
    command = ["cargo", "build", "--locked", "--release", "--manifest-path", str(CRATE / "Cargo.toml"),
        "--target", target, "--message-format=json-render-diagnostics"]
    process = subprocess.Popen(command, cwd=ROOT, env=environment, stdout=subprocess.PIPE, text=True)
    executable, build = None, None
    for line in process.stdout:
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if item.get("reason") == "compiler-message":
            rendered = item.get("message", {}).get("rendered")
            if rendered:
                print(rendered, end="")
        if item.get("reason") == "compiler-artifact" and item.get("target", {}).get("name") == "sideloader-core" and item.get("executable"):
            executable = Path(item["executable"])
        if item.get("reason") == "build-script-executed":
            candidate = Path(item["out_dir"]) / "backend-build.json"
            if candidate.is_file():
                build = json.loads(candidate.read_text())
    if process.wait() or executable is None or build is None:
        raise SystemExit("Rust backend build failed")
    if build["version"] != version or build["target"] != target or build["fingerprint"] != source_fingerprint():
        raise SystemExit("Rust build identity does not match current sources")
    binary = executable.read_bytes()
    if binary[:5] != b"\x7fELF\x02" or int.from_bytes(binary[18:20], "little") != 62:
        raise SystemExit("Expected an ELF64 x86-64 Linux executable")
    destination = ROOT / "bin/sideloader-core"
    destination.parent.mkdir(exist_ok=True)
    shutil.copyfile(executable, destination)
    destination.chmod(0o755)
    metadata = {**build, "binary": "sideloader-core", "sha256": hashlib.sha256(binary).hexdigest()}
    (ROOT / "bin/backend.json").write_text(json.dumps(metadata, indent=2) + "\n")
    notices = crate_notices(environment, target)
    (ROOT / "bin/rust-notices.json").write_text(json.dumps(notices, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"version": version, "target": target, "sha256": metadata["sha256"], "crate_notices": len(notices["entries"])}))


if __name__ == "__main__":
    main()
