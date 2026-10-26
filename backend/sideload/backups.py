"""Remove only completed-task backup archives, without following filesystem links."""

import os
from pathlib import Path
import re
import stat
import time

RETENTION_SECONDS = 7 * 24 * 60 * 60
_OPERATION = re.compile(r"[a-f0-9]{32}\Z")
_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def clean_backups(root: Path, protected: set[str], expired_only: bool = True, now: float | None = None) -> dict:
    cutoff = (time.time() if now is None else now) - RETENTION_SECONDS
    result = {"deleted": 0, "bytes": 0, "skipped": 0, "failed": 0}
    try:
        root_fd = os.open(root, _DIRECTORY_FLAGS)
    except FileNotFoundError:
        return result
    try:
        with os.scandir(root_fd) as entries:
            for entry in entries:
                if not _OPERATION.fullmatch(entry.name):
                    continue
                if entry.name in protected or not entry.is_dir(follow_symlinks=False):
                    result["skipped"] += 1
                    continue
                try:
                    directory_fd = os.open(entry.name, _DIRECTORY_FLAGS, dir_fd=root_fd)
                    try:
                        directory = os.fstat(directory_fd)
                        original = entry.stat(follow_symlinks=False)
                        if (directory.st_dev, directory.st_ino) != (original.st_dev, original.st_ino) or directory.st_uid != os.getuid():
                            result["skipped"] += 1
                            continue
                        try:
                            info = os.stat("data-backup.zip", dir_fd=directory_fd, follow_symlinks=False)
                        except FileNotFoundError:
                            continue
                        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid():
                            result["skipped"] += 1
                            continue
                        if expired_only and info.st_mtime >= cutoff:
                            continue
                        os.unlink("data-backup.zip", dir_fd=directory_fd)
                        result["deleted"] += 1
                        result["bytes"] += info.st_size
                    finally:
                        os.close(directory_fd)
                except FileNotFoundError:
                    continue
                except OSError:
                    result["failed"] += 1
    finally:
        os.close(root_fd)
    return result
