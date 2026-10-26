"""Build the Decky runtime ZIP from the backend source layout."""

import hashlib
import json
import os
from pathlib import Path
import stat
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    version = json.loads((root / "package.json").read_text())["version"]
    ref = os.environ.get("GITHUB_REF", "")
    if ref.startswith("refs/tags/") and ref != f"refs/tags/v{version}":
        raise SystemExit(f"{ref} does not match package.json version v{version}")
    members = {name: root / name for name in (
        "plugin.json", "package.json", "LICENSE", "README.md", "CHANGELOG.md",
        "dist/index.js", "dist/index.js.map", "dist/notices.json", "dist/build.json",
        "docs/readme/README.zh-CN.md", "docs/readme/README.pt.md",
        "docs/readme/README.es.md", "docs/readme/README.ja.md", "docs/readme/README.ko.md",
    )}
    members["main.py"] = root / "backend/main.py"
    for source in sorted((root / "backend/sideload").rglob("*.py")):
        members[f"py_modules/sideload/{source.relative_to(root / 'backend/sideload').as_posix()}"] = source
    for directory in ("licenses", "assets/readme"):
        for source in sorted((root / directory).rglob("*")):
            if source.is_file():
                members[source.relative_to(root).as_posix()] = source
    missing = [str(path.relative_to(root)) for path in members.values() if not path.is_file()]
    if missing:
        raise SystemExit("Missing runtime files: " + ", ".join(missing))
    metadata = json.loads((root / "dist/build.json").read_text())
    if metadata.get("version") != version or metadata.get("bundle") != hashlib.sha256((root / "dist/index.js").read_bytes()).hexdigest():
        raise SystemExit("Frontend build identity is stale; run pnpm build before packaging")
    destination = root / "dist" / f"decky-sideloader-{version}.zip"
    with ZipFile(destination, "w", ZIP_DEFLATED) as archive:
        for name, source in sorted(members.items()):
            info = ZipInfo(f"decky-sideloader/{name}", (2026, 1, 1, 0, 0, 0))
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            info.compress_type = ZIP_DEFLATED
            archive.writestr(info, source.read_bytes())
    print(json.dumps({"path": str(destination), "version": version, "files": len(members),
                      "sha256": hashlib.sha256(destination.read_bytes()).hexdigest()}))


if __name__ == "__main__":
    main()
