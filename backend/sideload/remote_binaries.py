from __future__ import annotations


import copy
import hashlib
import json
import os
import re
import stat
import tempfile
from pathlib import Path
from zipfile import ZipFile, ZipInfo

from sideload.github_transport import GitHubTransport
from sideload.install_ops import MAX_ENTRIES, MAX_EXPANDED, _allowed_download, download_asset

_NAME = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9_.-]{0,254}\Z")
_SHA256 = re.compile(r"[A-Fa-f0-9]{64}\Z")
_CHUNK = 1024 * 1024


def prepare_remote_binaries(archive: Path, package: dict, context, *, transport=None,
                            progress=None, cancel=None, timeout_seconds: int = 180) -> dict | None:
    """Embed Loader v3.3 remote_binary entries before handing the ZIP to Loader.

    The caller first inspects the source ZIP and reinspects the replaced runtime
    ZIP. source.zip retains the unmodified input for retry and diagnosis.
    """
    current = "remote_binary"
    latest_download = None

    def stopped():
        if cancel is not None and cancel.is_set():
            raise RuntimeError("准备已停止，尚未交给 Loader")

    def transfer(source, destination, digest=None):
        while True:
            stopped()
            chunk = source.read(_CHUNK)
            stopped()
            if not chunk:
                return
            destination.write(chunk)
            if digest is not None:
                digest.update(chunk)

    try:
        stopped()
        with ZipFile(archive) as original:
            entries = original.infolist()
            expanded = sum(entry.file_size for entry in entries)
            if not entries or len(entries) > MAX_ENTRIES or expanded > MAX_EXPANDED:
                raise RuntimeError("安装包为空、文件过多或解压大小超过 512 MiB")
            folder = package["folder"]
            metadata_path = f"{folder}/package.json"
            metadata_info = original.getinfo(metadata_path)
            if metadata_info.file_size > 1024 * 1024:
                raise RuntimeError("package.json 超过 1 MiB")
            metadata = json.loads(original.read(metadata_info))
            if not isinstance(metadata, dict):
                raise RuntimeError("package.json 格式错误")
            if "remote_binary" not in metadata:
                return None
            dependencies = metadata["remote_binary"]
            if not isinstance(dependencies, list):
                raise RuntimeError("必须是包含 name、url、sha256hash 的数组")
            if not dependencies:
                return {"count": 0, "names": []}
            if len(dependencies) > MAX_ENTRIES:
                raise RuntimeError("依赖文件过多")

            paths = {entry.filename.rstrip("/"): entry for entry in entries}
            names = set()
            targets = set()
            for index, dependency in enumerate(dependencies, 1):
                current = f"remote_binary[{index}]"
                if not isinstance(dependency, dict):
                    raise RuntimeError("依赖声明必须是对象")
                name = dependency.get("name")
                if isinstance(name, str):
                    current = name
                if not isinstance(name, str) or not _NAME.fullmatch(name):
                    raise RuntimeError("name 必须是安全的单个文件名")
                if name in names:
                    raise RuntimeError("依赖文件名重复")
                names.add(name)
                digest = dependency.get("sha256hash")
                if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
                    raise RuntimeError("sha256hash 必须是 64 位十六进制 SHA-256")
                url = dependency.get("url")
                if not isinstance(url, str) or not _allowed_download(url, initial=True):
                    raise RuntimeError("不支持的依赖来源：仅支持 GitHub Release HTTPS 下载地址")
                target = f"{folder}/bin/{name}"
                for parent in (folder, f"{folder}/bin"):
                    entry = paths.get(parent)
                    if entry is not None and (not entry.is_dir() or stat.S_IFMT(entry.external_attr >> 16) not in (0, stat.S_IFDIR)):
                        raise RuntimeError(f"路径冲突：{parent} 不是目录")
                entry = paths.get(target)
                if entry is not None and (entry.is_dir() or stat.S_IFMT(entry.external_attr >> 16) not in (0, stat.S_IFREG)):
                    raise RuntimeError(f"路径冲突：{target} 不是普通文件")
                if any(path.startswith(target + "/") for path in paths):
                    raise RuntimeError(f"路径冲突：{target} 已被用作目录")
                targets.add(target)
            current = ", ".join(dependency["name"] for dependency in dependencies)
            if len(entries) + sum(target not in paths for target in targets) > MAX_ENTRIES:
                raise RuntimeError("合并后文件数超过 10000")
            runtime_metadata = dict(metadata)
            del runtime_metadata["remote_binary"]
            encoded_metadata = json.dumps(runtime_metadata, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            if len(encoded_metadata) > 1024 * 1024:
                raise RuntimeError("合并后 package.json 超过 1 MiB")
            # Count every original byte, even a regular file being replaced.
            expanded += max(0, len(encoded_metadata) - metadata_info.file_size)
            if expanded > MAX_EXPANDED:
                raise RuntimeError("合并后解压大小超过 512 MiB")
            source_archive = archive.with_name("source.zip")
            if source_archive == archive or source_archive.is_symlink():
                raise RuntimeError("source.zip 原始包保留路径不安全")
            transport = transport or GitHubTransport(context)
            with tempfile.TemporaryDirectory(prefix=".remote-binaries-", dir=archive.parent) as directory:
                temporary = Path(directory)
                runtime_path = temporary / "runtime.zip"
                with ZipFile(runtime_path, "w") as runtime:
                    runtime.comment = original.comment
                    for entry in entries:
                        stopped()
                        if entry.filename in targets:
                            continue
                        if entry.filename == metadata_path:
                            runtime.writestr(copy.copy(entry), encoded_metadata)
                        else:
                            with original.open(entry) as source, runtime.open(copy.copy(entry), "w") as destination:
                                transfer(source, destination)
                    for index, dependency in enumerate(dependencies, 1):
                        current = dependency["name"]
                        latest_download = {"received": 0, "total": None, "status": "connecting", "route": "direct", "attempt": 1,
                                           "name": current, "index": index, "count": len(dependencies)}
                        stopped()
                        remaining = MAX_EXPANDED - expanded
                        if remaining <= 0:
                            raise RuntimeError("合并后解压大小超过 512 MiB")
                        def report(snapshot):
                            nonlocal latest_download
                            latest_download = {**snapshot, "name": dependency["name"], "index": index, "count": len(dependencies)}
                            if progress:
                                progress(dict(latest_download))
                        binary = temporary / f"dependency-{index}"
                        download_asset(dependency["url"], binary, context, transport=transport,
                                       progress=report, cancel=cancel, timeout_seconds=timeout_seconds,
                                       max_bytes=remaining)
                        expanded += binary.stat().st_size
                        if expanded > MAX_EXPANDED:
                            raise RuntimeError("合并后解压大小超过 512 MiB")
                        info = ZipInfo(f"{folder}/bin/{dependency['name']}")
                        info.create_system = 3
                        info.external_attr = (stat.S_IFREG | 0o755) << 16
                        info.file_size = binary.stat().st_size
                        digest = hashlib.sha256()
                        with binary.open("rb") as source, runtime.open(info, "w") as destination:
                            transfer(source, destination, digest)
                        if digest.hexdigest() != dependency["sha256hash"].lower():
                            raise RuntimeError("SHA-256 校验失败")
                        binary.unlink()
                current = ", ".join(dependency["name"] for dependency in dependencies)
                preserved = temporary / "source.zip"
                with archive.open("rb") as source, preserved.open("xb") as destination:
                    transfer(source, destination)
                os.chmod(preserved, 0o600)
                os.chmod(runtime_path, 0o600)
                stopped()
                os.replace(preserved, source_archive)
                stopped()
                os.replace(runtime_path, archive)
            return {"count": len(dependencies), "names": [dependency["name"] for dependency in dependencies]}
    except Exception as error:
        if progress and latest_download is not None:
            progress({**latest_download, "status": "failed"})
        raise RuntimeError(f"依赖 {current}：{error}") from error
