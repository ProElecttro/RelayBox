
import http.server
import os
import socket
import ssl
import subprocess
import tempfile
import threading
import unittest

from probe import probe_url


class PathLensHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/final")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        if self.path == "/final":
            body = b"PathLens local test passed"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path == "/unavailable":
            body = b"Service temporarily unavailable"
            self.send_response(503)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, format, *args):
        pass


class PathLensTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.cert_path = os.path.join(cls.temp_dir.name, "cert.pem")
        key_path = os.path.join(cls.temp_dir.name, "key.pem")

        subprocess.run(
            [
                "openssl", "req", "-x509", "-newkey", "rsa:2048",
                "-nodes", "-keyout", key_path,
                "-out", cls.cert_path,
                "-days", "1",
                "-subj", "/CN=localhost",
                "-addext", "subjectAltName=DNS:localhost",
            ],
            check=True,
            capture_output=True,
        )

        cls.server = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0),
            PathLensHandler,
        )

        server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_context.load_cert_chain(cls.cert_path, key_path)
        cls.server.socket = server_context.wrap_socket(
            cls.server.socket,
            server_side=True,
        )

        cls.server_thread = threading.Thread(
            target=cls.server.serve_forever,
            daemon=True,
        )
        cls.server_thread.start()

        cls.port = cls.server.server_port
        cls.client_context = ssl.create_default_context(
            cafile=cls.cert_path,
        )

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.server_thread.join(timeout=2)
        cls.temp_dir.cleanup()

    def probe_path(self, path):
        url = f"https://localhost:{self.port}{path}"
        return probe_url(url, self.client_context, 2, False)

    def get_http_result(self, hop):
        return next(
            result
            for result in hop["results"]
            if result["stage"] == "HTTP_REQUEST"
        )

    def test_successful_http_response(self):
        hop, redirect_url = self.probe_path("/final")

        self.assertEqual(hop["outcome"], "SUCCESS")
        self.assertEqual(hop["status_code"], 200)
        self.assertEqual(
            self.get_http_result(hop)["http_health"],
            "HEALTHY",
        )
        self.assertEqual(hop["connected_ip"], "127.0.0.1")
        self.assertIsNone(redirect_url)

    def test_redirect_destination_is_resolved(self):
        hop, redirect_url = self.probe_path("/redirect")

        self.assertEqual(hop["status_code"], 302)
        self.assertEqual(
            self.get_http_result(hop)["http_health"],
            "REDIRECT",
        )
        self.assertEqual(
            redirect_url,
            f"https://localhost:{self.port}/final",
        )

    def test_http_404_is_client_error(self):
        hop, redirect_url = self.probe_path("/missing")

        self.assertEqual(hop["outcome"], "SUCCESS")
        self.assertEqual(hop["status_code"], 404)
        self.assertEqual(
            self.get_http_result(hop)["http_health"],
            "CLIENT_ERROR",
        )
        self.assertIsNone(redirect_url)

    def test_http_503_is_server_error(self):
        hop, redirect_url = self.probe_path("/unavailable")

        self.assertEqual(hop["outcome"], "SUCCESS")
        self.assertEqual(hop["status_code"], 503)
        self.assertEqual(
            self.get_http_result(hop)["http_health"],
            "SERVER_ERROR",
        )
        self.assertIsNone(redirect_url)

    def test_pinned_ip_preserves_hostname_for_tls(self):
        url = f"https://localhost:{self.port}/final"

        hop, redirect_url = probe_url(
            url,
            self.client_context,
            2,
            False,
            ip_override="127.0.0.1",
        )

        self.assertEqual(hop["outcome"], "SUCCESS")
        self.assertEqual(hop["connected_ip"], "127.0.0.1")
        self.assertEqual(hop["status_code"], 200)
        self.assertEqual(hop["results"][0]["stage"], "IP_SELECT")
        self.assertIsNone(redirect_url)


if __name__ == "__main__":
    unittest.main()

