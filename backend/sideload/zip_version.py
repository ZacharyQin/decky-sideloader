from __future__ import annotations


import json
import zlib

from sideload.github_meta import _version

_TAIL = 22 + 65535
_CD_MAX = 256 * 1024
_JSON_MAX = 64 * 1024
_LOCAL = b"PK\x03\x04"
_CENTRAL = b"PK\x01\x02"
_EOCD = b"PK\x05\x06"


class ZipUnreadable(RuntimeError):
    pass


def probe_release_zip(url, transport) -> tuple[str, str | None]:
    try:
        return package_version_from_zip(lambda start, end: transport.fetch_range(url, start, end))
    except Exception:
        return "unknown", None


def package_version_from_zip(fetch) -> tuple[str, str | None]:
    """Return semver/raw/absent. Raise ZipUnreadable when the zip cannot be parsed from ranges."""
    tail, tail_start, total = fetch(None, _TAIL)
    if not isinstance(tail, (bytes, bytearray)) or not isinstance(tail_start, int) or not isinstance(total, int):
        raise ZipUnreadable("安装包尾部不可读")
    if tail_start < 0 or total < len(tail) or tail_start + len(tail) != total or len(tail) < 22:
        raise ZipUnreadable("安装包尾部不可读")
    eocd = _eocd_index(bytes(tail))
    if eocd is None:
        raise ZipUnreadable("安装包不是可读的 zip")
    if int.from_bytes(tail[eocd + 4:eocd + 6], "little") or int.from_bytes(tail[eocd + 6:eocd + 8], "little"):
        raise ZipUnreadable("不读取分卷安装包")
    cd_size = int.from_bytes(tail[eocd + 12:eocd + 16], "little")
    cd_offset = int.from_bytes(tail[eocd + 16:eocd + 20], "little")
    if cd_size == 0xFFFFFFFF or cd_offset == 0xFFFFFFFF or cd_size > _CD_MAX or cd_offset + cd_size > total:
        raise ZipUnreadable("安装包目录超出读取范围")
    if cd_size == 0:
        return "absent", None
    if cd_offset >= tail_start and cd_offset + cd_size <= total:
        directory = bytes(tail[cd_offset - tail_start:cd_offset - tail_start + cd_size])
    else:
        directory, start, _ = fetch(cd_offset, cd_offset + cd_size - 1)
        if start != cd_offset or not isinstance(directory, (bytes, bytearray)) or len(directory) != cd_size:
            raise ZipUnreadable("安装包目录不可读")
        directory = bytes(directory)
    entry = _plugin_package(directory)
    if entry is None:
        return "absent", None
    return _read_version(fetch, entry)




def _eocd_index(data: bytes) -> int | None:
    start = max(0, len(data) - _TAIL)
    index = data.rfind(_EOCD, start)
    while index != -1:
        if index + 22 <= len(data) and index + 22 + int.from_bytes(data[index + 20:index + 22], "little") == len(data):
            return index
        index = data.rfind(_EOCD, start, index)
    return None


def _plugin_package(directory: bytes) -> dict | None:
    pos, folders, packages = 0, set(), []
    while pos + 46 <= len(directory):
        if directory[pos:pos + 4] != _CENTRAL:
            raise ZipUnreadable("安装包目录不可读")
        flags = int.from_bytes(directory[pos + 8:pos + 10], "little")
        method = int.from_bytes(directory[pos + 10:pos + 12], "little")
        compressed = int.from_bytes(directory[pos + 20:pos + 24], "little")
        uncompressed = int.from_bytes(directory[pos + 24:pos + 28], "little")
        name_len = int.from_bytes(directory[pos + 28:pos + 30], "little")
        extra_len = int.from_bytes(directory[pos + 30:pos + 32], "little")
        comment_len = int.from_bytes(directory[pos + 32:pos + 34], "little")
        local_offset = int.from_bytes(directory[pos + 42:pos + 46], "little")
        end = pos + 46 + name_len + extra_len + comment_len
        if end > len(directory) or flags & 1 or compressed == 0xFFFFFFFF or uncompressed == 0xFFFFFFFF or local_offset == 0xFFFFFFFF:
            raise ZipUnreadable("安装包目录不可读")
        try:
            name = directory[pos + 46:pos + 46 + name_len].decode("utf-8")
        except UnicodeDecodeError as error:
            raise ZipUnreadable("安装包目录不可读") from error
        pos = end
        if name.endswith("/") or not name or name.startswith("/") or "\\" in name or ".." in name.split("/"):
            continue
        parts = name.split("/")
        if len(parts) >= 2 and parts[0]:
            folders.add(parts[0])
        if len(parts) == 2 and parts[1] == "package.json":
            packages.append({"name": name, "method": method, "compressed": compressed, "uncompressed": uncompressed, "offset": local_offset})
    if pos != len(directory) or len(folders) != 1 or len(packages) != 1:
        return None
    return packages[0]


def _read_version(fetch, entry) -> tuple[str, str | None]:
    if entry["method"] not in (0, 8) or entry["compressed"] > _JSON_MAX or entry["uncompressed"] > _JSON_MAX:
        raise ZipUnreadable("package.json 超出读取范围")
    header, start, _ = fetch(entry["offset"], entry["offset"] + 29)
    if start != entry["offset"] or not isinstance(header, (bytes, bytearray)) or len(header) < 30 or bytes(header[:4]) != _LOCAL:
        raise ZipUnreadable("package.json 文件头不可读")
    name_len = int.from_bytes(header[26:28], "little")
    extra_len = int.from_bytes(header[28:30], "little")
    if name_len > 512 or extra_len > 4096 or int.from_bytes(header[6:8], "little") & 1:
        raise ZipUnreadable("package.json 文件头不可读")
    name, name_start, _ = fetch(entry["offset"] + 30, entry["offset"] + 29 + name_len)
    if name_start != entry["offset"] + 30 or bytes(name) != entry["name"].encode("utf-8"):
        raise ZipUnreadable("package.json 文件头不可读")
    data_offset = entry["offset"] + 30 + name_len + extra_len
    if entry["compressed"] == 0:
        payload = b""
    else:
        payload, payload_start, _ = fetch(data_offset, data_offset + entry["compressed"] - 1)
        if payload_start != data_offset or not isinstance(payload, (bytes, bytearray)) or len(payload) != entry["compressed"]:
            raise ZipUnreadable("package.json 不可读")
        payload = bytes(payload)
    raw = payload if entry["method"] == 0 else _inflate(payload)
    if len(raw) != entry["uncompressed"]:
        raise ZipUnreadable("package.json 不可读")
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return "absent", None
    version = parsed.get("version") if isinstance(parsed, dict) else None
    if not isinstance(version, str) or not version.strip() or len(version) > 128:
        return "absent", None
    version = version.strip()
    return ("semver", version) if _version(version) else ("raw", version)


def _inflate(payload: bytes) -> bytes:
    try:
        raw = zlib.decompressobj(-15).decompress(payload, _JSON_MAX + 1)
    except zlib.error as error:
        raise ZipUnreadable("package.json 不可读") from error
    if len(raw) > _JSON_MAX:
        raise ZipUnreadable("package.json 超出读取范围")
    return raw
