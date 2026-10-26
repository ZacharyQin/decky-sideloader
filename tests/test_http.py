import io
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
MODULES = ROOT / "backend"
for path in (ROOT, MODULES):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
sys.modules.setdefault("decky", types.ModuleType("decky"))
from sideload import http_client
import sideload
from sideload import github_transport


class Response(io.BytesIO):
    def __init__(self, data=b"[]", url="https://api.github.com/repos/acme/plugin/releases", headers=None):
        super().__init__(data)
        self.url, self.headers = url, headers or {}

    def geturl(self):
        return self.url


class HttpTests(unittest.TestCase):
    def test_ssl_context_prefers_existing_system_ca(self):
        context = object()
        with patch.object(sideload.http_client.os.path, "isfile", side_effect=lambda p: p == "/etc/ssl/cert.pem"), patch.object(sideload.http_client.ssl, "create_default_context", return_value=context) as create:
            self.assertIs(sideload.http_client._ssl_context(), context)
        create.assert_called_once_with(cafile="/etc/ssl/cert.pem")

    def test_ssl_context_falls_back_to_default_paths(self):
        context = object()
        with patch.object(sideload.http_client.os.path, "isfile", return_value=False), patch.object(sideload.http_client.ssl, "create_default_context", return_value=context) as create:
            self.assertIs(sideload.http_client._ssl_context(), context)
        create.assert_called_once_with()

    def test_fetch_json_uses_store_accept_by_default(self):
        response = Response(b'{"ok":true}', sideload.http_client.STORE_URL)
        opener = Mock(open=Mock(return_value=response))
        with patch.object(sideload.http_client, "_ssl_context", return_value=None), patch.object(sideload.http_client.urllib.request, "build_opener", return_value=opener):
            self.assertEqual(sideload.http_client._fetch_json(sideload.http_client.STORE_URL), {"ok": True})
        request = opener.open.call_args.args[0]
        self.assertEqual(request.get_header("Accept"), "application/json")
        self.assertEqual(request.get_header("User-agent"), sideload.http_client.USER_AGENT)

    def test_fetch_json_accept_can_be_overridden_for_github(self):
        opener = Mock(open=Mock(return_value=Response()))
        with patch.object(sideload.http_client, "_ssl_context", return_value=None), patch.object(github_transport.urllib.request, "build_opener", return_value=opener):
            self.assertEqual(sideload.http_client._fetch_json("https://api.github.com/repos/acme/plugin/releases", accept="application/vnd.github+json"), [])
        self.assertEqual(opener.open.call_args.args[0].get_header("Accept"), "application/vnd.github+json")

    def test_probe_http_response_size_is_bounded(self):
        opener = Mock(open=Mock(return_value=Response(b'{"too":"large"}')))
        with patch.object(sideload.http_client, "_ssl_context", return_value=None), patch.object(github_transport.urllib.request, "build_opener", return_value=opener):
            with self.assertRaisesRegex(RuntimeError, "过大"):
                sideload.http_client._fetch_json("https://api.github.com/repos/a/b/git/trees/HEAD", max_bytes=4)
            opener.open.return_value = Response(b'{}')
            self.assertEqual(sideload.http_client._fetch_json("https://api.github.com/repos/a/b/git/trees/HEAD", max_bytes=4), {})

    def rate_error(self, code, headers):
        url = "https://api.github.com/repos/acme/plugin/releases"
        error = sideload.http_client.urllib.error.HTTPError(url, code, "Forbidden", headers, None)
        opener = Mock(open=Mock(side_effect=error))
        with patch.object(sideload.http_client, "_ssl_context", return_value=None), patch.object(github_transport.urllib.request, "build_opener", return_value=opener):
            sideload.http_client._fetch_json(url)

    def test_github_rate_limit_explains_when_to_retry(self):
        with self.assertRaisesRegex(RuntimeError, r"GitHub 请求已限流.*\d{2}:\d{2}:\d{2}.*后重试"):
            self.rate_error(403, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1791916847"})

    def test_github_rate_limit_tolerates_invalid_reset(self):
        with self.assertRaisesRegex(RuntimeError, "GitHub 请求已限流.*稍后重试"):
            self.rate_error(429, {"X-RateLimit-Reset": "invalid"})

    def test_other_forbidden_responses_are_not_called_rate_limits(self):
        with self.assertRaisesRegex(RuntimeError, "HTTP 403"):
            self.rate_error(403, {})

    def test_store_rejects_short_read_and_redirect_before_following(self):
        opener = Mock(open=Mock(return_value=Response(b'[]', sideload.http_client.STORE_URL, {"Content-Length": "10"})))
        with patch.object(sideload.http_client, "_ssl_context", return_value=None), patch.object(sideload.http_client.urllib.request, "build_opener", return_value=opener):
            with self.assertRaisesRegex(RuntimeError, "不完整"):
                sideload.http_client._fetch_json(sideload.http_client.STORE_URL)
        with self.assertRaises(RuntimeError):
            sideload.http_client._StoreRedirect().redirect_request(None, None, 302, "", {}, "https://evil.test/")


if __name__ == "__main__":
    unittest.main()
