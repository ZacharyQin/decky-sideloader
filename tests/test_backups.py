import asyncio
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from sideload.backups import clean_backups, RETENTION_SECONDS
from sideload.install_flow import InstallFlow
from sideload import install_flow


class BackupRetentionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.runtime = self.home / "runtime"
        self.root = self.runtime / "installations"
        self.now = 1800000000

    def backup(self, number, age=0):
        directory = self.root / f"{number:032x}"
        directory.mkdir(parents=True)
        archive = directory / "data-backup.zip"
        archive.write_bytes(b"backup-data")
        stamp = self.now - age
        os.utime(archive, (stamp, stamp))
        return archive

    def flow(self):
        return InstallFlow(str(self.home), str(self.runtime), "self", "self", lambda *args: [], lambda: None)

    async def test_expiration_is_strictly_older_than_seven_days_and_only_backups(self):
        old = self.backup(1, RETENTION_SECONDS + 1)
        boundary = self.backup(2, RETENTION_SECONDS)
        recent = self.backup(3, 1)
        future = self.backup(4, -60)
        package = old.with_name("plugin.zip")
        package.write_bytes(b"installation-package")
        original = self.home / "data" / "example" / "settings.json"
        original.parent.mkdir(parents=True)
        original.write_bytes(b"original-plugin-data")
        result = clean_backups(self.root, set(), now=self.now)
        self.assertEqual(result, {"deleted": 1, "bytes": 11, "skipped": 0, "failed": 0})
        self.assertFalse(old.exists())
        for archive in (boundary, recent, future):
            self.assertEqual(archive.read_bytes(), b"backup-data")
        self.assertEqual(package.read_bytes(), b"installation-package")
        self.assertEqual(original.read_bytes(), b"original-plugin-data")

    async def test_manual_cleanup_includes_recent_but_protects_active_and_unknown_tasks(self):
        current = self.backup(1)
        flow = self.flow()
        for stage in ("ready", "backup", "installing", "unknown", "uninstalling"):
            flow.operation = {"id": current.parent.name, "stage": stage}
            result = await flow.clean_backups(expired_only=False)
            self.assertEqual(result["deleted"], 0)
            self.assertEqual(current.read_bytes(), b"backup-data")
        flow.operation = {"id": current.parent.name, "stage": "closed", "backup": {"archive": str(current)}}
        result = await flow.clean_backups(expired_only=False)
        self.assertEqual(result["deleted"], 1)
        self.assertFalse(current.exists())
        self.assertTrue(flow._public()["backup_deleted"])

    async def test_terminal_task_is_protected_until_its_real_worker_finishes(self):
        archive = self.backup(1)
        flow = self.flow()
        flow.operation = {"id": archive.parent.name, "stage": "cancelled"}
        release = asyncio.Event()
        flow.task = asyncio.create_task(release.wait())
        try:
            result = await flow.clean_backups(expired_only=False)
            self.assertEqual(result["deleted"], 0)
            self.assertTrue(archive.exists())
        finally:
            release.set()
            await flow.task
        self.assertEqual((await flow.clean_backups(expired_only=False))["deleted"], 1)

    async def test_links_and_unrecognized_directories_are_not_deleted(self):
        outside = self.home / "outside"
        outside.mkdir()
        target = outside / "data-backup.zip"
        target.write_bytes(b"do-not-delete")
        self.root.mkdir(parents=True)
        (self.root / f"{1:032x}").symlink_to(outside, target_is_directory=True)
        linked = self.root / f"{2:032x}"
        linked.mkdir()
        (linked / "data-backup.zip").symlink_to(target)
        hard = self.root / f"{3:032x}"
        hard.mkdir()
        os.link(target, hard / "data-backup.zip")
        unknown = self.root / "user-files"
        unknown.mkdir()
        (unknown / "data-backup.zip").write_bytes(b"unrecognized")
        result = clean_backups(self.root, set(), expired_only=False)
        self.assertEqual(result, {"deleted": 0, "bytes": 0, "skipped": 3, "failed": 0})
        self.assertEqual(target.read_bytes(), b"do-not-delete")
        self.assertEqual((unknown / "data-backup.zip").read_bytes(), b"unrecognized")
        with self.assertRaises(OSError):
            clean_backups(self.root / f"{1:032x}", set(), expired_only=False)

    async def test_partial_failure_is_reported_and_does_not_prevent_other_deletions(self):
        denied = self.backup(1)
        allowed = self.backup(2)
        unlink = os.unlink
        def remove(name, *, dir_fd):
            info = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
            if info.st_ino == denied.stat().st_ino:
                raise PermissionError("denied")
            return unlink(name, dir_fd=dir_fd)
        with patch("sideload.backups.os.unlink", side_effect=remove):
            result = clean_backups(self.root, set(), expired_only=False)
        self.assertEqual((result["deleted"], result["failed"]), (1, 1))
        self.assertTrue(denied.exists())
        self.assertFalse(allowed.exists())

    async def test_cancellation_drains_cleanup_before_an_install_can_reuse_the_directory(self):
        archive = self.backup(1)
        flow = self.flow()
        started, release = threading.Event(), threading.Event()
        original = install_flow.clean_backups
        def slow(*args):
            started.set()
            if not release.wait(5):
                raise TimeoutError("cleanup test worker not released")
            return original(*args)
        with patch.object(install_flow, "clean_backups", side_effect=slow):
            cleanup = asyncio.create_task(flow.clean_backups(expired_only=False))
            await asyncio.to_thread(started.wait, 5)
            self.assertTrue(started.is_set())
            cleanup.cancel()
            entered = asyncio.Event()
            async def next_install():
                async with flow.lock:
                    entered.set()
            waiting = asyncio.create_task(next_install())
            await asyncio.sleep(0)
            self.assertFalse(entered.is_set())
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await cleanup
            await waiting
        self.assertFalse(archive.exists())
        self.assertTrue(entered.is_set())


if __name__ == "__main__":
    unittest.main()
