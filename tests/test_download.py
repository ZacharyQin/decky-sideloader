import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from sideload import install_ops
from sideload.github_transport import GitHubTransport
from test_http import Response
from test_transport import ZIP


class Stream(Response):
    def __init__(self, chunks, headers=None, cancel=None):
        super().__init__(url=ZIP, headers=headers)
        self.chunks, self.cancel = iter(chunks), cancel

    def read1(self, size):
        value = next(self.chunks, b"")
        if isinstance(value, Exception):
            raise value
        if self.cancel:
            self.cancel.set()
        return value


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.target = Path(self.temp.name) / "asset.zip"
        self.transport = GitHubTransport(None, "auto")
        self.events = []
        self.clock = 0

    def tick(self):
        self.clock += 0.15
        return self.clock

    def run_download(self, streams, **kwargs):
        with patch.object(self.transport, "open", side_effect=streams) as opened, patch.object(install_ops.time, "monotonic", side_effect=self.tick):
            install_ops.download_asset(ZIP, self.target, None, transport=self.transport, progress=self.events.append, **kwargs)
        return opened

    def test_known_length_has_multiple_actual_updates_and_complete_is_download_only(self):
        opened = self.run_download([Stream([b"a" * 65536] * 4, {"Content-Length": str(4 * 65536)})])
        updates = [e for e in self.events if e["status"] == "downloading" and e["received"]]
        self.assertGreaterEqual(len(updates), 3)
        self.assertEqual(sorted(e["received"] for e in updates), [e["received"] for e in updates])
        self.assertEqual(self.events[-1], {"received": 4 * 65536, "total": 4 * 65536, "status": "complete", "route": "direct", "attempt": 1})
        self.assertEqual(opened.call_count, 1)

    def test_unknown_length_keeps_total_null_and_actual_bytes(self):
        self.run_download([Stream([b"abc", b"def"])])
        self.assertTrue(all(e["total"] is None for e in self.events))
        self.assertEqual(self.events[-1]["received"], 6)
        self.assertEqual(self.target.read_bytes(), b"abcdef")

    def test_partial_network_failure_restarts_zero_and_never_mixes_files(self):
        opened = self.run_download([Stream([b"old", TimeoutError("read timeout")]), Stream([b"new data"], {"Content-Length": "8"})])
        self.assertEqual(self.target.read_bytes(), b"new data")
        restart = [e for e in self.events if e["attempt"] == 2][0]
        self.assertEqual((restart["received"], restart["route"], restart["status"]), (0, "gh-proxy", "connecting"))
        self.assertEqual([c.args[1] for c in opened.call_args_list], ["direct", "gh-proxy"])

    def test_second_partial_failure_uses_ghfast_without_mixing_bytes(self):
        opened = self.run_download([Stream([b"first", TimeoutError("direct")]),
                                   Stream([b"second", TimeoutError("proxy")]),
                                   Stream([b"complete"], {"Content-Length": "8"})])
        self.assertEqual(self.target.read_bytes(), b"complete")
        self.assertEqual([c.args[1] for c in opened.call_args_list], ["direct", "gh-proxy", "ghfast"])
        third = [e for e in self.events if e["attempt"] == 3]
        self.assertEqual(third[0], {"received": 0, "total": None, "status": "connecting", "route": "ghfast", "attempt": 3})
        self.assertEqual(third[-1], {"received": 8, "total": 8, "status": "complete", "route": "ghfast", "attempt": 3})

    def test_short_read_and_invalid_or_excessive_length_fail_without_proxy_retry(self):
        for length in ("4", "-1", "abc", "1.2", "128000000000", "", "1"):
            with self.subTest(length=length):
                target = self.target.with_name("case-" + str(len(self.events)))
                with patch.object(self.transport, "open", return_value=Stream([b"abc"], {"Content-Length": length})) as opened:
                    with self.assertRaises(RuntimeError):
                        install_ops.download_asset(ZIP, target, None, transport=self.transport, progress=self.events.append)
                self.assertEqual(opened.call_count, 1)
                self.assertEqual(self.events[-1]["status"], "failed")

    def test_stream_limit_failure_keeps_last_written_bytes_and_stops(self):
        with self.assertRaises(RuntimeError):
            self.run_download([Stream([b"abc", b"def"])], max_bytes=4)
        self.assertEqual(self.events[-1]["received"], 6)
        self.assertEqual(self.events[-1]["status"], "failed")
        self.assertEqual(self.target.read_bytes(), b"abc")

    def test_preexisting_file_is_never_truncated_or_overwritten(self):
        self.target.write_bytes(b"user file")
        with patch.object(self.transport, "open") as opened:
            with self.assertRaises(FileExistsError):
                install_ops.download_asset(ZIP, self.target, None, transport=self.transport)
        self.assertEqual(self.target.read_bytes(), b"user file")
        opened.assert_not_called()

    def test_cancellation_after_read_prevents_writing_or_fallback(self):
        cancel = threading.Event()
        with self.assertRaisesRegex(RuntimeError, "停止"):
            self.run_download([Stream([b"abc"], cancel=cancel)], cancel=cancel)
        self.assertEqual(self.target.read_bytes(), b"")
        self.assertEqual(self.events[-1]["status"], "failed")
        self.assertEqual(self.events[-1]["received"], 0)

