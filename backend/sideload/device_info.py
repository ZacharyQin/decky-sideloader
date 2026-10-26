"""Read non-identifying device metadata only while building a diagnostic export."""
import os
from pathlib import Path
import stat
import sys

from sideload.diagnostic_snapshot import sanitize_snapshot

OS_RELEASE_PATHS = (Path("/etc/os-release"), Path("/usr/lib/os-release"))
DMI_DIRECTORY = Path("/sys/devices/virtual/dmi/id")


def _read_text(path, maximum=8192):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                return None
            with os.fdopen(fd, "rb", closefd=False) as stream:
                data = stream.read(maximum + 1)
            return data.decode("utf-8").strip() if len(data) <= maximum else None
        finally:
            os.close(fd)
    except (OSError, UnicodeError):
        return None


def environment_snapshot(user_home):
    result = {"system": sys.platform, "python": ".".join(map(str, sys.version_info[:3])), "isSteamDeck": None}
    if hasattr(os, "uname"):
        host = os.uname()
        result.update(machine=host.machine, kernelVersion=host.release)
    if sys.platform == "linux":
        fields = {"ID": "osId", "NAME": "osName", "VERSION_ID": "osVersion", "BUILD_ID": "osBuild", "VARIANT_ID": "osVariant"}
        for path in OS_RELEASE_PATHS:
            release = _read_text(path)
            if release is not None:
                for line in release.splitlines():
                    key, separator, value = line.partition("=")
                    if separator and key in fields:
                        result[fields[key]] = value.strip().strip("\"'")
                break
        for source, field in (("sys_vendor", "deviceVendor"), ("product_name", "deviceModel"), ("board_name", "boardName"), ("product_version", "productVersion")):
            value = _read_text(DMI_DIRECTORY / source, 128)
            if value:
                result[field] = value
        result = sanitize_snapshot(result)
        vendor = result.get("deviceVendor", "").casefold()
        model = result.get("deviceModel", "").casefold()
        if vendor in {"valve", "valve corporation"}:
            if model in {"jupiter", "galileo"}:
                result.update(isSteamDeck=True, steamDeckModel="lcd" if model == "jupiter" else "oled")
        elif vendor:
            result["isSteamDeck"] = False
    if user_home is not None:
        try:
            usage = os.statvfs(user_home)
            result["freeBytes"] = usage.f_bavail * usage.f_frsize
        except OSError:
            pass
    return sanitize_snapshot(result)
