"""fault_injection probe primitive unit tests.

``run_control`` is the low-level fault-control executor consumed in-process
by the fault_suite probe (fault plans). Without a real control surface it
must answer INCONCLUSIVE — it never claims a fault was applied when no
control exists.
"""

from __future__ import annotations

import http.server
import json
import threading
import unittest

from performance.targets.echomem.probes.fault_injection import run_control


class FaultInjectionTests(unittest.TestCase):
    def test_control_without_real_surface_is_inconclusive(self):
        result = run_control({"command": "", "endpoint": "", "container": "",
                              "action": "", "signal": "KILL", "timeout_s": 1})
        self.assertEqual("INCONCLUSIVE", result["status"])
        self.assertIn("no real fault control", result["reason"])

    def test_command_control_runs_and_reports_returncode(self):
        result = run_control({"command": "echo ok", "action": "enable",
                              "timeout_s": 10})
        self.assertEqual("PASS", result["status"])
        self.assertEqual(0, result["returncode"])
        self.assertEqual("command", result["control"])

    def test_command_control_failure_is_fail(self):
        result = run_control({"command": "exit 7", "action": "disable",
                              "timeout_s": 10})
        self.assertEqual("FAIL", result["status"])
        self.assertEqual(7, result["returncode"])

    def test_endpoint_control_posts_action_and_keeps_body(self):
        received = {}

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                received["path"] = self.path
                received["body"] = json.loads(self.rfile.read(length) or b"{}")
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            endpoint = f"http://127.0.0.1:{server.server_port}/fault"
            result = run_control({"endpoint": endpoint, "action": "enable",
                                  "timeout_s": 5})
        finally:
            server.shutdown()
        self.assertEqual("PASS", result["status"])
        self.assertEqual("http", result["control"])
        self.assertEqual("/fault", received["path"])
        self.assertEqual({"action": "enable"}, received["body"])


if __name__ == "__main__":
    unittest.main()
