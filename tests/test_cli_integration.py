from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


class _FakeServices(BaseHTTPRequestHandler):
    delete_requests = 0
    session_open_requests = 0
    tenant_create_requests = 0
    chat_requests = 0
    commit_status = "completed"

    def log_message(self, format, *args):
        return

    def _json(self, payload, status=200):
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/health":
            self._json({"status": "ok"})
            return
        if "/commits/" in self.path:
            self._json({"status": type(self).commit_status})
            return
        self._json({"error": "not_found"}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        if self.path == "/api/auth/tenants":
            type(self).tenant_create_requests += 1
            self._json({"tenant": {"tenant_id": "tenant_test"}})
        elif self.path == "/api/auth/tenants/tenant_test/users":
            self._json({"user": {"user_id": "user_test"}})
        elif self.path == "/api/auth/tenants/tenant_test/users/user_test/key":
            self._json({"auth_key": "ek_test_identity"})
        elif self.path == "/api/sessions/open":
            type(self).session_open_requests += 1
            self._json({"session_id": "session_test"})
        elif self.path.endswith("/messages"):
            self._json({"status": "accepted"})
        elif self.path.endswith("/commit"):
            self._json({"archive_id": "archive_test"})
        elif self.path == "/api/retrieval/search":
            self._json({
                "result": {
                    "items": [{
                        "uri": "echo://memory/test",
                        "score": 1.0,
                        "content": "The answer is answer.",
                        "memory_type": "atom",
                    }]
                }
            })
        elif self.path == "/fs/glob":
            pattern = str(body.get("pattern") or "")
            filename = pattern.rsplit("/", 1)[-1]
            self._json({
                "entries": [{
                    "uri": (
                        "echo://engine/echo0_plugin/sessions/"
                        f"session_test/{filename}"
                    )
                }]
            })
        elif self.path == "/v1/auth/login":
            self._json({
                "access_token": "login-token",
                "user": {"id": "echoagent-user"},
            })
        elif self.path == "/memory-engine":
            self._json({"result": {"authKey": "ek_echoagent_identity"}})
        elif self.path == "/api/auth/account/delete":
            type(self).delete_requests += 1
            self._json({"status": "deleted"})
        elif self.path == "/v1/chat/completions":
            type(self).chat_requests += 1
            prompt = " ".join(
                str(message.get("content") or "")
                for message in body.get("messages", [])
            ).lower()
            if "is_correct" in prompt and "correct" in prompt and "wrong" in prompt:
                content = json.dumps({"is_correct": "CORRECT", "reasoning": "matches"})
            elif "yes or no only" in prompt:
                content = "yes"
            else:
                content = "answer"
            self._json({
                "choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 1},
            })
        else:
            self._json({"error": "not_found"}, 404)


class StaticCliIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeServices)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def _run(
        self,
        benchmark: str,
        dataset: Path,
        output: Path,
        *extra_args: str,
    ) -> dict:
        deletes_before = _FakeServices.delete_requests
        command = [
            sys.executable,
            str(ROOT / "eval.py"),
            benchmark,
            "--env-file", str(output / "missing.env"),
            "--dataset", str(dataset),
            "--questions", "1",
            "--echomem-url", self.base_url,
            "--llm-base-url", f"{self.base_url}/v1",
            "--llm-api-key", "test-key",
            "--llm-model", "test-model",
            "--out-dir", str(output),
            "--commit-poll-interval-s", "0.01",
            "--concurrency", "1",
            *extra_args,
        ]
        completed = subprocess.run(
            command,
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=20,
        )
        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        self.assertEqual(deletes_before + 1, _FakeServices.delete_requests)
        summaries = list(output.glob("*/summary.json"))
        self.assertEqual(1, len(summaries))
        return json.loads(summaries[0].read_text(encoding="utf-8"))

    def test_root_help_does_not_require_benchmark(self):
        completed = subprocess.run(
            [sys.executable, str(ROOT / "eval.py"), "--help"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=10,
        )

        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        self.assertIn("One-command memory-system dataset evaluation", completed.stdout)
        self.assertIn("{dynamic,hotpotqa,locomo,longmemeval}", completed.stdout)

    def test_hotpotqa_full_cli_flow(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "hotpot.json"
            dataset.write_text(json.dumps([{
                "_id": "h1",
                "question": "What is the answer?",
                "answer": "answer",
                "context": [["Answer", ["The answer is answer."]]],
                "supporting_facts": [["Answer", 0]],
            }]), encoding="utf-8")

            summary = self._run("hotpotqa", dataset, root / "results")

            self.assertEqual("completed", summary["status"])
            self.assertEqual(1.0, summary["avg_f1"])
            self.assertEqual("isolated", summary["memory_identity"]["mode"])
            self.assertEqual("ephemeral", summary["memory_identity"]["retention"])

    def test_locomo_full_cli_flow(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "locomo.json"
            dataset.write_text(json.dumps([{
                "sample_id": "conv-test",
                "conversation": {
                    "speaker_a": "Alex",
                    "speaker_b": "Sam",
                    "session_1": [
                        {"speaker": "Alex", "dia_id": "d1", "text": "The answer is answer."},
                        {"speaker": "Sam", "dia_id": "d2", "text": "Got it."},
                    ],
                    "session_1_date_time": "9:00 AM on 2 January, 2023",
                },
                "qa": [{"question": "What is the answer?", "answer": "answer"}],
            }]), encoding="utf-8")

            summary = self._run(
                "locomo",
                dataset,
                root / "results",
                "--inject-memory",
            )

            self.assertEqual("completed", summary["status"])
            self.assertEqual(1.0, summary["accuracy"])
            self.assertEqual(0, summary["judge_errors"])
            self.assertEqual("injected", summary["memory_source"])

    def test_locomo_v2_head_profile_full_cli_flow(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "locomo.json"
            dataset.write_text(json.dumps([{
                "sample_id": "conv-test",
                "conversation": {
                    "speaker_a": "Alex",
                    "speaker_b": "Sam",
                    "session_1": [{
                        "speaker": "Alex",
                        "dia_id": "d1",
                        "text": "The answer is answer.",
                    }],
                    "session_1_date_time": "9:00 AM on 2 January, 2023",
                },
                "qa": [{
                    "question": "What is the answer?",
                    "answer": "answer",
                }],
            }]), encoding="utf-8")

            summary = self._run(
                "locomo",
                dataset,
                root / "results",
                "--inject-memory",
                "--qa-profile",
                "vikingbot-v2-head",
            )

            self.assertEqual("completed", summary["status"])
            self.assertEqual("vikingbot-v2-head", summary["qa_profile"])
            self.assertEqual(30, summary["top_k"])
            self.assertEqual(20, summary["tool_search_limit"])
            self.assertEqual(
                "vikingbot_native_safe",
                summary["tool_set"],
            )
            self.assertEqual(
                "a146a246c2fcce128229d19e05c87228affd829d",
                summary["qa_profile_source"]["commit"],
            )

    def test_locomo_reuses_existing_memory_without_import(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "locomo.json"
            dataset.write_text(json.dumps([{
                "sample_id": "conv-test",
                "conversation": {
                    "speaker_a": "Alex",
                    "speaker_b": "Sam",
                    "session_1": [
                        {"speaker": "Alex", "dia_id": "d1", "text": "The answer is answer."},
                    ],
                    "session_1_date_time": "9:00 AM on 2 January, 2023",
                },
                "qa": [{"question": "What is the answer?", "answer": "answer"}],
            }]), encoding="utf-8")
            output = root / "results"
            opens_before = _FakeServices.session_open_requests
            tenants_before = _FakeServices.tenant_create_requests
            deletes_before = _FakeServices.delete_requests
            command = [
                sys.executable,
                str(ROOT / "eval.py"),
                "locomo",
                "--env-file", str(root / "missing.env"),
                "--dataset", str(dataset),
                "--questions", "1",
                "--echomem-url", self.base_url,
                "--echomem-auth-key", "existing-key",
                "--account", "existing-account",
                "--user-id", "existing-user",
                "--reuse-memory-account",
                "--llm-base-url", f"{self.base_url}/v1",
                "--llm-api-key", "test-key",
                "--llm-model", "test-model",
                "--out-dir", str(output),
                "--concurrency", "1",
            ]

            completed = subprocess.run(
                command,
                cwd=ROOT,
                text=True,
                capture_output=True,
                timeout=20,
            )

            self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
            self.assertEqual(opens_before, _FakeServices.session_open_requests)
            self.assertEqual(tenants_before, _FakeServices.tenant_create_requests)
            self.assertEqual(deletes_before, _FakeServices.delete_requests)
            summaries = list(output.glob("*/summary.json"))
            self.assertEqual(1, len(summaries))
            summary = json.loads(summaries[0].read_text(encoding="utf-8"))
            self.assertEqual("existing", summary["memory_source"])
            self.assertEqual("reused", summary["memory_identity"]["mode"])
            self.assertEqual(0, summary["import_total"])

    def test_locomo_resume_reuses_qa_and_judge_without_model_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "locomo.json"
            dataset.write_text(json.dumps([{
                "sample_id": "conv-test",
                "conversation": {
                    "speaker_a": "Alex",
                    "speaker_b": "Sam",
                    "session_1": [{
                        "speaker": "Alex",
                        "dia_id": "d1",
                        "text": "The answer is answer.",
                    }],
                    "session_1_date_time": "9:00 AM on 2 January, 2023",
                },
                "qa": [{
                    "question": "What is the answer?",
                    "answer": "answer",
                }],
            }]), encoding="utf-8")
            common = [
                sys.executable,
                str(ROOT / "eval.py"),
                "locomo",
                "--env-file", str(root / "missing.env"),
                "--dataset", str(dataset),
                "--questions", "1",
                "--echomem-url", self.base_url,
                "--echomem-auth-key", "existing-key",
                "--account", "existing-account",
                "--user-id", "existing-user",
                "--reuse-memory-account",
                "--llm-base-url", f"{self.base_url}/v1",
                "--llm-api-key", "test-key",
                "--llm-model", "test-model",
                "--concurrency", "1",
                "--judge-concurrency", "1",
            ]
            first_output = root / "first"
            first = subprocess.run(
                [*common, "--out-dir", str(first_output)],
                cwd=ROOT,
                text=True,
                capture_output=True,
                timeout=20,
            )
            self.assertEqual(0, first.returncode, first.stdout + first.stderr)
            source_runs = list(first_output.glob("*/summary.json"))
            self.assertEqual(1, len(source_runs))
            source_dir = source_runs[0].parent
            calls_before_resume = _FakeServices.chat_requests

            second_output = root / "second"
            second = subprocess.run(
                [
                    *common,
                    "--out-dir", str(second_output),
                    "--resume-qa", str(source_dir),
                    "--resume-judge", str(source_dir),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                timeout=20,
            )

            self.assertEqual(
                0,
                second.returncode,
                second.stdout + second.stderr,
            )
            self.assertEqual(
                calls_before_resume,
                _FakeServices.chat_requests,
            )
            resumed_summaries = list(
                second_output.glob("*/summary.json")
            )
            self.assertEqual(1, len(resumed_summaries))
            summary = json.loads(
                resumed_summaries[0].read_text(encoding="utf-8")
            )
            self.assertEqual(1, summary["qa_resume"]["reused"])
            self.assertTrue(summary["judge_resume"]["enabled"])
            self.assertEqual(1.0, summary["accuracy"])

    def test_longmemeval_full_cli_flow(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "longmemeval.json"
            dataset.write_text(json.dumps([{
                "question_id": "q1",
                "question": "What is the answer?",
                "answer": "answer",
                "question_type": "single-session-user",
                "question_date": "2023-01-03T10:00:00Z",
                "haystack_dates": ["2023/01/02 (Mon) 09:00"],
                "haystack_session_ids": ["s1"],
                "haystack_sessions": [[{"role": "user", "content": "The answer is answer."}]],
            }]), encoding="utf-8")

            summary = self._run("longmemeval", dataset, root / "results")

            self.assertEqual("completed", summary["status"])
            self.assertEqual(1.0, summary["accuracy"])
            self.assertEqual(0, summary["judge_errors"])

    def test_dynamic_check_only_runs_preflight(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            command = [
                sys.executable,
                str(ROOT / "eval.py"),
                "dynamic",
                "--env-file", str(root / "missing.env"),
                "--check",
                "--echoagent-url", self.base_url,
                "--memory-engine-endpoint", f"{self.base_url}/memory-engine",
                "--echomem-url", self.base_url,
                "--username", "test-user",
                "--password", "password",
                "--llm-base-url", f"{self.base_url}/v1",
                "--llm-api-key", "test-key",
                "--out-dir", str(root / "results"),
            ]
            completed = subprocess.run(
                command,
                cwd=ROOT,
                text=True,
                capture_output=True,
                timeout=20,
            )

            self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
            self.assertIn("[check] OK benchmark=dynamic", completed.stdout)
            self.assertFalse((root / "results").exists())

    def test_incomplete_import_diagnostic_is_not_a_formal_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "hotpot.json"
            output = root / "results"
            dataset.write_text(json.dumps([{
                "_id": "h1",
                "question": "What is the answer?",
                "answer": "answer",
                "context": [["Answer", ["The answer is answer."]]],
                "supporting_facts": [["Answer", 0]],
            }]), encoding="utf-8")
            command = [
                sys.executable,
                str(ROOT / "eval.py"),
                "hotpotqa",
                "--env-file", str(root / "missing.env"),
                "--dataset", str(dataset),
                "--questions", "1",
                "--echomem-url", self.base_url,
                "--llm-base-url", f"{self.base_url}/v1",
                "--llm-api-key", "test-key",
                "--llm-model", "test-model",
                "--out-dir", str(output),
                "--commit-poll-interval-s", "0.01",
                "--allow-incomplete-imports",
            ]
            _FakeServices.commit_status = "failed"
            try:
                completed = subprocess.run(
                    command,
                    cwd=ROOT,
                    text=True,
                    capture_output=True,
                    timeout=20,
                )
            finally:
                _FakeServices.commit_status = "completed"

            self.assertEqual(2, completed.returncode, completed.stdout + completed.stderr)
            summary_path = next(output.glob("*/summary.json"))
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertEqual("failed", summary["status"])
            self.assertEqual(1, summary["incomplete_imports"])


if __name__ == "__main__":
    unittest.main()
