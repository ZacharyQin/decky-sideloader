from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import time
from sideload.github_transport import _Budget, GitHubTransport, DOWNLOAD_RESPONSE_SECONDS, allowed_url, content_length, network_error, retryable, read_chunk
from pathlib import Path, PurePosixPath
from zipfile import ZIP_STORED, ZipFile

MAX_DOWNLOAD = 128 * 1024 * 1024
MAX_EXPANDED = 512 * 1024 * 1024
MAX_BACKUP = 512 * 1024 * 1024
MAX_ENTRIES = 10000
_FOLDER = re.compile(r"^[A-Za-z0-9_-](?:[A-Za-z0-9_. -]{0,126}[A-Za-z0-9_-])?$")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _allowed_download(url: str, initial: bool = False) -> bool:
    return allowed_url(url, "download", initial=initial)


def download_asset(url: str, target: Path, context, *, transport=None, progress=None, cancel=None, timeout_seconds: int = 180, max_bytes: int = MAX_DOWNLOAD) -> None:
    if not _allowed_download(url, initial=True):
        raise RuntimeError("安装包必须来自 GitHub Release 的 HTTPS 下载地址")
    transport = transport or GitHubTransport(context)
    limit = timeout_seconds if type(timeout_seconds) is int and timeout_seconds in (180, 300, 600, 900, 1800) else 180
    deadline_message = f"下载超过 {limit // 60} 分钟"
    clock = [time.monotonic()]
    snapshot = {"received": 0, "total": None, "status": "connecting", "route": "direct", "attempt": 1}

    def stopped():
        if cancel is not None and cancel.is_set():
            raise RuntimeError("下载已停止，尚未交给 Loader")
        if time.monotonic() - clock[0] > limit:
            raise TimeoutError(deadline_message)

    def report(**changes):
        snapshot.update(changes)
        if progress:
            progress(dict(snapshot))

    # Exclusive creation preserves pre-existing user files. Retrying truncates only this open file.
    # Quick failures share the total deadline, but each route owns its sockets and watchdog.
    routes = transport.routes(url)
    with target.open("xb") as output:
        os.chmod(target, 0o600)
        for index, route in enumerate(routes):
            with _Budget(clock[0] + limit, cancel, timeout=DOWNLOAD_RESPONSE_SECONDS, message=deadline_message) as budget:
                size, last_report = 0, 0
                try:
                    stopped()
                    output.seek(0)
                    output.truncate()
                    report(received=0, total=None, status="connecting", route=route, attempt=index + 1)
                    with transport.open(url, route, kind="download", accept="application/octet-stream", timeout=DOWNLOAD_RESPONSE_SECONDS, budget=budget) as response:
                        total = content_length(response.headers, max_bytes)
                        report(total=total, status="downloading")
                        while True:
                            stopped()
                            data = read_chunk(response, 64 * 1024)
                            stopped()
                            if not data:
                                break
                            size += len(data)
                            snapshot["received"] = size
                            if size > max_bytes or total is not None and size > total:
                                raise RuntimeError("安装包过大或超过声明长度")
                            output.write(data)
                            now = time.monotonic()
                            if now - last_report >= 0.25:
                                report()
                                last_report = now
                        if total is not None and size != total:
                            raise RuntimeError("安装包下载不完整，与 Content-Length 不符")
                        report(status="complete")
                        return
                except Exception as error:
                    report(status="failed")
                    if hasattr(error, "close"):
                        error.close()
                    cancelled = cancel is not None and cancel.is_set()
                    deadline = isinstance(error, TimeoutError) and str(error).startswith("下载超过")
                    if index + 1 < len(routes) and retryable(error) and not cancelled:
                        if deadline:
                            clock[0] = time.monotonic()
                        if deadline or time.monotonic() - clock[0] < limit:
                            continue
                    raise network_error(error) from error


def inspect_archive(path: Path, existing: dict | None, installed: list[dict], self_name: str, self_folder: str, target: dict | None = None) -> dict:
    with ZipFile(path) as archive:
        entries = archive.infolist()
        if not entries or len(entries) > MAX_ENTRIES or sum(e.file_size for e in entries) > MAX_EXPANDED:
            raise RuntimeError("安装包为空、文件过多或解压大小超过 512 MiB")
        seen: set[str] = set()
        folders: set[str] = set()
        for entry in entries:
            raw = entry.filename
            parts = PurePosixPath(raw).parts
            if not parts or raw.startswith("/") or "\\" in raw or "\x00" in raw or any(p in (".", "..", "") for p in raw.rstrip("/").split("/")):
                raise RuntimeError("安装包包含不安全路径")
            if not _FOLDER.fullmatch(parts[0]):
                raise RuntimeError("插件目录名无效")
            if len(parts) == 1 and not entry.is_dir():
                raise RuntimeError("安装包必须只有一个插件顶层目录")
            kind = stat.S_IFMT(entry.external_attr >> 16)
            if kind not in (0, stat.S_IFREG, stat.S_IFDIR) or entry.flag_bits & 1:
                raise RuntimeError("安装包包含符号链接、特殊文件或加密内容")
            key = raw.rstrip("/")
            if key in seen:
                raise RuntimeError("安装包包含重复路径")
            seen.add(key)
            folders.add(parts[0])
        if len(folders) != 1:
            raise RuntimeError("安装包包含多个插件目录")
        folder = folders.pop()
        for entry in entries:
            for parent in PurePosixPath(entry.filename).parents:
                if str(parent) in seen and str(parent) != ".":
                    try:
                        if not archive.getinfo(str(parent)).is_dir():
                            raise RuntimeError("安装包的文件与目录路径冲突")
                    except KeyError:
                        pass  # Explicit directory entries end in '/'.
        def metadata(name: str) -> dict:
            info = archive.getinfo(f"{folder}/{name}")
            if info.file_size > 1024 * 1024:
                raise RuntimeError("插件元数据过大")
            result = json.loads(archive.read(info))
            if not isinstance(result, dict):
                raise RuntimeError("插件元数据格式错误")
            return result
        plugin, package = metadata("plugin.json"), metadata("package.json")
        name, version = plugin.get("name"), package.get("version")
        if not isinstance(name, str) or not name.strip() or len(name) > 128 or any(ord(c) < 32 or c in "/\\" for c in name):
            raise RuntimeError("plugin.json 缺少合法插件名")
        if not isinstance(version, str) or not version.strip() or len(version) > 128:
            raise RuntimeError("package.json 缺少版本")
        if f"{folder}/dist/index.js" not in seen or archive.getinfo(f"{folder}/dist/index.js").is_dir():
            raise RuntimeError("安装包缺少 dist/index.js")
        if target and (name != target["name"] or package.get("name") != target["package_name"]):
            raise RuntimeError("安装包身份与所选仓库插件不一致，请核对 monorepo 的插件、版本和 ZIP")
        warnings = []
        if f"{folder}/LICENSE" not in seen:
            warnings.append("安装包未附带 LICENSE，请自行核对作者的分发许可。")
        if "root" in (plugin.get("flags") or []):
            warnings.append("此插件声明了 root 权限，请仅安装可信作者的发布。")
        if name == self_name or folder == self_folder:
            raise RuntimeError("不能通过本插件替换 decky-sideloader 自身")
        collision = next((p for p in installed if p["name"] == name or p["folder"] == folder), None)
        if existing:
            if name != existing["name"] or folder != existing["folder"]:
                raise RuntimeError("新包名称或目录与旧插件不一致，已阻止替换以保护数据")
        elif collision:
            raise RuntimeError(f"{collision['name']} 已安装，请从插件列表进入版本管理")
        hashes = {}
        for entry in entries:
            if entry.is_dir():
                continue
            digest = hashlib.sha256()
            with archive.open(entry) as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            hashes[str(PurePosixPath(entry.filename).relative_to(folder))] = digest.hexdigest()
    return {"name": name, "folder": folder, "version": version, "hashes": hashes, "sha256": file_sha256(path), "warnings": warnings}


def plugin_stamp(home: Path, folder: str) -> list | None:
    root = home / "plugins" / folder
    if root.is_symlink():
        raise RuntimeError("插件目录变成了符号链接")
    try:
        (root / "package.json").stat()
    except FileNotFoundError:
        return None
    stamp = []
    for relative in ("package.json", "plugin.json", "dist/index.js", "main.py"):
        file = root / relative
        if file.is_symlink() or not file.resolve().is_relative_to(root.resolve()):
            raise RuntimeError("插件关键文件存在符号链接")
        try:
            result = file.stat()
            stamp.append([relative, result.st_ino, result.st_mtime_ns, result.st_ctime_ns, result.st_size, file_sha256(file)])
        except FileNotFoundError:
            stamp.append([relative, None])
    return stamp


def backup_data(home: Path, folder: str, destination: Path) -> dict:
    if not _FOLDER.fullmatch(folder):
        raise RuntimeError("插件目录名无效")
    paths = []
    files: list[tuple[Path, str, os.stat_result]] = []
    total = 0
    for category in ("settings", "data"):
        source = home / category / folder
        if source.is_symlink() or (home / category).is_symlink():
            raise RuntimeError("备份目录是符号链接，无法保证范围")
        try:
            source_info = source.lstat()
        except FileNotFoundError:
            continue
        if not stat.S_ISDIR(source_info.st_mode):
            raise RuntimeError("备份源不是目录")
        paths.append(str(source))
        # os.walk's default ignores access errors; fail closed instead.
        def failed(error):
            raise error
        for root, dirs, names in os.walk(source, followlinks=False, onerror=failed):
            for name in dirs + names:
                file = Path(root) / name
                info = file.lstat()
                if stat.S_ISLNK(info.st_mode) or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                    raise RuntimeError(f"备份源包含符号链接或特殊文件：{file}")
                if stat.S_ISREG(info.st_mode):
                    total += info.st_size
                    files.append((file, str(file.relative_to(home)), info))
                    if total > MAX_BACKUP or len(files) > MAX_ENTRIES:
                        raise RuntimeError("备份超过 512 MiB 或 10000 个文件，已停止替换")
    manifest = {"paths": paths, "files": len(files), "bytes": total, "archive": str(destination), "scope": "仅 Decky 标准 settings 和 data 目录，不含插件自行写入的其他位置。备份时插件仍可能运行。"}
    with destination.open("xb") as out:
        os.chmod(destination, 0o600)
        with ZipFile(out, "w", compression=ZIP_STORED) as archive:
            for file, relative, before in files:
                flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                with os.fdopen(os.open(file, flags), "rb") as source:
                    after = os.fstat(source.fileno())
                    if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
                        raise RuntimeError("备份期间数据发生变化，请停止插件活动后重试")
                    with archive.open(relative, "w") as output:
                        copied = 0
                        while chunk := source.read(1024 * 1024):
                            copied += len(chunk)
                            if copied > before.st_size:
                                raise RuntimeError("备份期间数据发生变化")
                            output.write(chunk)
                    final = file.stat()
                    if copied != before.st_size or (final.st_ino, final.st_size, final.st_mtime_ns) != (before.st_ino, before.st_size, before.st_mtime_ns):
                        raise RuntimeError("备份期间数据发生变化")
            archive.writestr("backup-manifest.json", json.dumps(manifest, ensure_ascii=False))
    with ZipFile(destination) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("备份完整性检查失败")
    manifest["sha256"] = file_sha256(destination)
    return manifest


def verify_installed(home: Path, package: dict, previous_stamp: list | None) -> bool:
    try:
        if plugin_stamp(home, package["folder"]) in (None, previous_stamp):
            return False
    except (OSError, RuntimeError):
        return False
    root = home / "plugins" / package["folder"]
    if root.is_symlink():
        return False
    for relative, expected in package["hashes"].items():
        file = root / relative
        try:
            if file.is_symlink() or not file.resolve().is_relative_to(root.resolve()) or file_sha256(file) != expected:
                return False
        except (OSError, ValueError):
            return False
    return True
