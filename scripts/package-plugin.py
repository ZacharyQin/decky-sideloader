"""Package the Rust core and Python Decky adapter in a verified runtime ZIP."""
import hashlib
import json
import os
from pathlib import Path
import stat
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from backend_build import source_fingerprint
from map_data import read_mapping


def main():
    root = Path(__file__).resolve().parents[1]
    read_mapping(root / "data/repositories.json")
    version = json.loads((root / "package.json").read_text())["version"]
    ref = os.environ.get("GITHUB_REF", "")
    if ref.startswith("refs/tags/") and ref != f"refs/tags/v{version}":
        raise SystemExit(f"{ref} does not match package.json version v{version}")
    members = {name: root / name for name in (
        "plugin.json", "package.json", "LICENSE", "data/repositories.json",
        "dist/index.js", "dist/notices.json", "dist/build.json", "dist/backend.json",
        "bin/sideloader-core",
    )}
    members["main.py"] = root / "backend/main.py"
    missing = [str(path.relative_to(root)) for path in members.values() if not path.is_file() or path.is_symlink()]
    if missing:
        raise SystemExit("Missing or unsafe runtime files: " + ", ".join(missing))
    frontend = json.loads((root / "dist/build.json").read_text())
    if frontend.get("version") != version or frontend.get("bundle") != hashlib.sha256((root / "dist/index.js").read_bytes()).hexdigest():
        raise SystemExit("Frontend build identity is stale; run pnpm build before packaging")
    backend = json.loads((root / "dist/backend.json").read_text())
    binary = (root / "bin/sideloader-core").read_bytes()
    if (backend.get("version") != version or backend.get("protocol") != 1 or backend.get("binary") != "sideloader-core"
            or backend.get("fingerprint") != source_fingerprint() or backend.get("sha256") != hashlib.sha256(binary).hexdigest()
            or binary[:5] != b"\x7fELF\x02" or int.from_bytes(binary[18:20], "little") != 62
            or backend.get("target") not in ("x86_64-unknown-linux-gnu", "x86_64-unknown-linux-musl")):
        raise SystemExit("Rust backend identity is stale or incompatible; run pnpm build before packaging")
    notices = json.loads((root / "dist/notices.json").read_text())
    entries, texts = notices.get("entries"), notices.get("texts")
    if not isinstance(entries, list) or not isinstance(texts, dict) or not any(row.get("id", "").startswith("rust:") for row in entries):
        raise SystemExit("Rust crate license notices are missing")
    for ref, text in texts.items():
        if not isinstance(text, str) or not text.strip() or hashlib.sha256(text.encode("utf-8")).hexdigest() != ref:
            raise SystemExit("Invalid license text pool")
    for entry in entries:
        if not entry.get("documents") or any(doc.get("ref") not in texts for doc in entry["documents"]):
            raise SystemExit("Missing license document reference: " + entry.get("id", ""))
    for source in (root / "licenses").rglob("*"):
        if source.is_file() and source.read_text(encoding="utf-8") not in texts.values():
            raise SystemExit("Runtime license source is not represented in notices: " + str(source))
    destination = Path(os.environ.get("SIDELOADER_PACKAGE_OUTPUT", root / "dist" / f"decky-sideloader-{version}.zip"))
    with ZipFile(destination, "w", ZIP_DEFLATED) as archive:
        for name, source in sorted(members.items()):
            info = ZipInfo(f"decky-sideloader/{name}", (2026, 1, 1, 0, 0, 0))
            info.external_attr = (stat.S_IFREG | (0o755 if name == "bin/sideloader-core" else 0o644)) << 16
            info.compress_type = ZIP_DEFLATED
            archive.writestr(info, source.read_bytes(), compresslevel=9)
    print(json.dumps({"path": str(destination), "version": version, "files": len(members),
        "sha256": hashlib.sha256(destination.read_bytes()).hexdigest()}))


if __name__ == "__main__":
    main()
