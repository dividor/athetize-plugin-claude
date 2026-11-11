"""Tests for pl_http: only http and https are fetched, sizes are capped, errors are mapped.

A throwaway server on 127.0.0.1 stands in for the internet, so nothing leaves the machine.
"""

import http.server
import json
import socket
import threading
import unittest
from pathlib import Path

import pl_http

ROOT = Path(__file__).resolve().parent.parent


class _Handler(http.server.BaseHTTPRequestHandler):
    def _send(self, code, body=b"", ctype="text/plain", location=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if location:
            self.send_header("Location", location)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # the name is fixed by BaseHTTPRequestHandler
        routes = {
            "/ok": lambda: self._send(200, b"hello", "text/plain; charset=utf-8"),
            "/big": lambda: self._send(200, b"x" * 5000),
            "/missing": lambda: self._send(404, b"nope"),
            "/redirect-ok": lambda: self._send(302, location="/ok"),
            "/redirect-file": lambda: self._send(302, location="file:///etc/hosts"),
            "/headers": lambda: self._send(
                200,
                json.dumps(
                    {"ua": self.headers.get("User-Agent"), "accept": self.headers.get("Accept")}
                ).encode(),
                "application/json",
            ),
        }
        routes.get(self.path, lambda: self._send(500, b"unexpected path"))()

    def log_message(self, format, *args):  # keep test output quiet
        return


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def test_get_returns_body_type_and_final_url(self):
        data, ctype, final = pl_http.http_get(self.base + "/ok")
        self.assertEqual(data, b"hello")
        self.assertEqual(ctype, "text/plain; charset=utf-8")
        self.assertEqual(final, self.base + "/ok")

    def test_redirect_to_http_is_followed(self):
        data, _, final = pl_http.http_get(self.base + "/redirect-ok")
        self.assertEqual(data, b"hello")
        self.assertEqual(final, self.base + "/ok")

    def test_redirect_to_file_scheme_is_refused(self):
        with self.assertRaises(pl_http.HttpError):
            pl_http.http_get(self.base + "/redirect-file")

    def test_non_http_schemes_are_refused_before_any_request(self):
        for url in ("file:///etc/hosts", "ftp://example.org/x", "data:text/plain,hi", "/etc/hosts"):
            with self.subTest(url=url):
                with self.assertRaises(pl_http.HttpError) as ctx:
                    pl_http.http_get(url)
                self.assertIn("unsupported URL scheme", str(ctx.exception))

    def test_http_status_errors_are_mapped(self):
        with self.assertRaises(pl_http.HttpError) as ctx:
            pl_http.http_get(self.base + "/missing")
        self.assertEqual(str(ctx.exception), f"HTTP 404 for {self.base[len('http://'):]}")

    def test_oversized_response_is_refused(self):
        with self.assertRaises(pl_http.HttpError) as ctx:
            pl_http.http_get(self.base + "/big", max_bytes=100)
        self.assertEqual(str(ctx.exception), "response too large")
        data, _, _ = pl_http.http_get(self.base + "/big", max_bytes=5000)
        self.assertEqual(len(data), 5000)

    def test_connection_failure_is_a_network_error(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            closed_port = s.getsockname()[1]
        with self.assertRaises(pl_http.HttpError) as ctx:
            pl_http.http_get(f"http://127.0.0.1:{closed_port}/ok", timeout=5)
        self.assertIn("network error for 127.0.0.1", str(ctx.exception))

    def test_user_agent_and_accept_headers(self):
        seen = pl_http.http_json(self.base + "/headers", email="me@example.org")
        self.assertEqual(seen["ua"], f"athetize/{pl_http.VERSION} (mailto:me@example.org)")
        self.assertEqual(seen["accept"], "application/json")
        seen = pl_http.http_json(self.base + "/headers")
        self.assertEqual(seen["ua"], f"athetize/{pl_http.VERSION}")

    def test_version_comes_from_the_plugin_manifest(self):
        manifest = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text("utf-8"))
        self.assertEqual(pl_http.VERSION, manifest["version"])
        self.assertEqual(pl_http.HOMEPAGE, manifest["homepage"])
        self.assertTrue(pl_http.HOMEPAGE.startswith("https://github.com/"))


if __name__ == "__main__":
    unittest.main()
