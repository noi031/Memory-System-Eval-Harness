#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import mimetypes
import urllib.error
import urllib.parse
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from memory.strict_blackbox import merge_strict_blackbox_snapshot


ROOT = Path(__file__).resolve().parent


def read_json_file(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def merge_hotpotqa_summary(summary: dict[str, Any], csv_path: Path) -> dict[str, Any]:
    summary_path = csv_path.with_name("hotpotqa_answer_summary.json")
    if not summary_path.exists():
        return summary
    try:
        official = read_json_file(summary_path)
    except Exception:
        return summary
    if not isinstance(official, dict):
        return summary
    merged = dict(summary)
    official_metric = "joint_f1" if official.get("joint_f1") is not None else "answer_f1"
    for key, value in {
        "official_metric": official_metric,
        "official_score": official.get(official_metric),
        "official_answer_em": official.get("answer_em"),
        "official_answer_f1": official.get("answer_f1"),
        "official_supporting_facts_em": official.get("supporting_facts_em"),
        "official_supporting_facts_f1": official.get("supporting_facts_f1"),
        "official_joint_em": official.get("joint_em"),
        "official_joint_f1": official.get("joint_f1"),
        "official_metric_scope": official.get("metric_scope"),
        "official_metric_note": official.get("official_metric_note"),
        "hotpotqa_answer_summary_path": str(summary_path),
    }.items():
        if value is not None:
            merged[key] = value
    summary_json = dict(merged.get("summary_json") or {})
    official_eval = dict(summary_json.get("official_eval") or {})
    official_eval.update({
        "enabled": True,
        "summary": official,
        "summary_path": str(summary_path),
    })
    summary_json["official_eval"] = official_eval
    merged["summary_json"] = summary_json
    return merged


def merge_longmemeval_summary(summary: dict[str, Any], csv_path: Path) -> dict[str, Any]:
    summary_path = csv_path.with_name("longmemeval_official_summary.json")
    if not summary_path.exists():
        return summary
    try:
        official = read_json_file(summary_path)
    except Exception:
        return summary
    if not isinstance(official, dict):
        return summary
    merged = dict(summary)
    for key, value in {
        "official_metric": "overall_accuracy",
        "official_score": official.get("overall_accuracy"),
        "official_overall_accuracy": official.get("overall_accuracy"),
        "official_task_averaged_accuracy": official.get("task_averaged_accuracy"),
        "official_graded": official.get("graded"),
        "official_correct": official.get("correct"),
        "official_wrong": official.get("wrong"),
        "official_metric_note": "overall_accuracy with task_averaged_accuracy from LongMemEval official-style evaluator",
        "longmemeval_official_summary_path": str(summary_path),
    }.items():
        if value is not None:
            merged[key] = value
    summary_json = dict(merged.get("summary_json") or {})
    official_eval = dict(summary_json.get("official_eval") or {})
    official_eval.update({
        "enabled": True,
        "summary": official,
        "summary_path": str(summary_path),
    })
    summary_json["official_eval"] = official_eval
    merged["summary_json"] = summary_json
    return merged


def merge_strict_blackbox_summary(summary: dict[str, Any], csv_path: Path) -> dict[str, Any]:
    return merge_strict_blackbox_snapshot(summary, csv_path)


def maybe_patch_results_payload(raw_payload: bytes, parsed_url: urllib.parse.ParseResult) -> bytes:
    if parsed_url.path != "/api/results":
        return raw_payload
    query = urllib.parse.parse_qs(parsed_url.query or "")
    path_value = str((query.get("path") or [""])[0] or "").strip()
    if not path_value:
        return raw_payload
    csv_path = Path(path_value).expanduser()
    try:
        data = json.loads(raw_payload.decode("utf-8"))
    except Exception:
        return raw_payload
    if not isinstance(data, dict):
        return raw_payload
    summary = data.get("summary")
    if not isinstance(summary, dict):
        return raw_payload
    patched = merge_hotpotqa_summary(summary, csv_path)
    patched = merge_longmemeval_summary(patched, csv_path)
    patched = merge_strict_blackbox_summary(patched, csv_path)
    if patched == summary:
        return raw_payload
    merged = dict(data)
    merged["summary"] = patched
    return json.dumps(merged, ensure_ascii=False).encode("utf-8")


def guess_content_type(path: Path) -> str:
    content_type, _ = mimetypes.guess_type(str(path))
    return content_type or "application/octet-stream"


class BenchmarkConsoleV2Handler(BaseHTTPRequestHandler):
    server_version = "BenchmarkConsoleV2/1.0"

    @property
    def api_base(self) -> str:
        return self.server.api_base.rstrip("/")  # type: ignore[attr-defined]

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path.startswith("/api/"):
            self.proxy_request("GET")
            return
        self.serve_static(parsed.path)

    def do_HEAD(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path.startswith("/api/"):
            self.send_error(HTTPStatus.METHOD_NOT_ALLOWED, "HEAD not supported for proxied API")
            return
        self.serve_static(parsed.path, send_body=False)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path.startswith("/api/"):
            self.proxy_request("POST")
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Unsupported POST target")

    def serve_static(self, raw_path: str, send_body: bool = True) -> None:
        path = raw_path or "/"
        if path == "/":
            target = ROOT / "index.html"
        else:
            target = (ROOT / path.lstrip("/")).resolve()
        if not str(target).startswith(str(ROOT.resolve())):
            self.send_error(HTTPStatus.FORBIDDEN, "Forbidden path")
            return
        if target.is_dir():
            target = target / "index.html"
        if not target.exists() or not target.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return
        body = target.read_bytes()
        if target == ROOT / "index.html":
            injected = (
                '<meta name="benchmark-console-reference-url" '
                f'content="{self.api_base}">'
            ).encode("utf-8")
            body = body.replace(b"</head>", injected + b"\n</head>")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{guess_content_type(target)}; charset=utf-8" if target.suffix in {".html", ".css", ".js", ".json"} else guess_content_type(target))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.end_headers()
        if send_body:
            self.wfile.write(body)

    def proxy_request(self, method: str) -> None:
        parsed = urllib.parse.urlparse(self.path)
        target = f"{self.api_base}{parsed.path}"
        if parsed.query:
            target = f"{target}?{parsed.query}"
        body = None
        headers = {}
        content_length = int(self.headers.get("Content-Length", "0") or "0")
        if content_length > 0:
            body = self.rfile.read(content_length)
            content_type = self.headers.get("Content-Type")
            if content_type:
                headers["Content-Type"] = content_type
        request = urllib.request.Request(target, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                payload = response.read()
                payload = maybe_patch_results_payload(payload, parsed)
                self.send_response(response.status)
                for key, value in response.getheaders():
                    lowered = key.lower()
                    if lowered in {"connection", "transfer-encoding", "content-encoding", "content-length"}:
                        continue
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
        except urllib.error.HTTPError as error:
            payload = error.read()
            self.send_response(error.code)
            for key, value in error.headers.items():
                lowered = key.lower()
                if lowered in {"connection", "transfer-encoding", "content-encoding", "content-length"}:
                    continue
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except urllib.error.URLError as error:
            message = f"API proxy failed: {error.reason}\n".encode("utf-8")
            self.send_response(HTTPStatus.BAD_GATEWAY)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(message)))
            self.end_headers()
            self.wfile.write(message)

    def log_message(self, format: str, *args) -> None:
        return


def main() -> int:
    parser = argparse.ArgumentParser(description="Standalone dev server for benchmark-console-v2")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4173)
    parser.add_argument("--api-base", default="http://127.0.0.1:19181")
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), BenchmarkConsoleV2Handler)
    server.api_base = args.api_base  # type: ignore[attr-defined]
    print(f"benchmark-console-v2 listening on http://{args.host}:{args.port} -> {args.api_base}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
