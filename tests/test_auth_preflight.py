import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from performance.probes.auth_preflight import key_fingerprint, run


class AuthPreflightTests(unittest.TestCase):
    def test_key_fingerprint_is_fixed_length_and_not_plaintext(self):
        value = "test-secret-key"
        fingerprint = key_fingerprint(value)
        self.assertEqual(12, len(fingerprint))
        self.assertNotIn(value, fingerprint)

    def test_auth_preflight_records_real_http_status_without_secret(self):
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                status = 200 if self.headers.get("X-Auth-Key") == "valid-key" else 401
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(
                    json.dumps(
                        {"status": "ok" if status == 200 else "invalid"}
                    ).encode()
                )

            def log_message(self, *_args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        config = Path(os.getenv("TMPDIR", "/tmp")) / (
            f"auth-preflight-test-{os.getpid()}.json"
        )
        try:
            config.write_text(
                json.dumps(
                    {
                        "tenants": [
                            {"tenant_id": "ok", "auth_key_env": "OK_KEY"},
                            {"tenant_id": "bad", "auth_key_env": "BAD_KEY"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            old_values = {name: os.environ.get(name) for name in ("OK_KEY", "BAD_KEY")}
            os.environ["OK_KEY"] = "valid-key"
            os.environ["BAD_KEY"] = "invalid-key"
            try:
                result = run(
                    f"http://127.0.0.1:{server.server_port}",
                    config,
                    timeout_s=1,
                )
            finally:
                for name, value in old_values.items():
                    if value is None:
                        os.environ.pop(name, None)
                    else:
                        os.environ[name] = value
            self.assertEqual("ENVIRONMENT_ERROR", result["status"])
            self.assertEqual(1, result["passed"])
            self.assertEqual(1, result["failed"])
            encoded = json.dumps(result)
            self.assertNotIn("valid-key", encoded)
            self.assertNotIn("invalid-key", encoded)
        finally:
            config.unlink(missing_ok=True)
            server.shutdown()
