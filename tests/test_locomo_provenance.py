from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from benchmarks.locomo.provenance import (
    expected_session_count,
    inspect_memory_provenance,
    write_memory_provenance,
)
from benchmarks.locomo.memory_scope import (
    ExcludingMemoryFilesClient,
    SessionPrefixMemoryClient,
)
from backends.memory_types import SearchResult


class _MemoryClient:
    def __init__(self, session_ids: list[str]):
        self.session_ids = session_ids

    def fs_glob(self, pattern: str, **_kwargs):
        filename = pattern.rsplit("/", 1)[1]
        return [
            {
                "uri": (
                    f"echo://sessions/{session_id}/current/{filename}"
                )
            }
            for session_id in self.session_ids
        ]


class LocomoProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.plans = [{
            "session_batches": [
                {"session_key": "session_1", "messages": [{"content": "a"}]},
                {"session_key": "session_2", "messages": [{"content": "b"}]},
            ]
        }]

    def test_expected_count_follows_session_mode(self):
        self.assertEqual(
            2,
            expected_session_count(
                self.plans,
                session_mode="locomo",
                max_sessions=0,
            ),
        )
        self.assertEqual(
            1,
            expected_session_count(
                self.plans,
                session_mode="single",
                max_sessions=0,
            ),
        )

    def test_inspection_records_dataset_and_detects_mismatch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            dataset = Path(temp_dir) / "locomo.json"
            dataset.write_text("[]", encoding="utf-8")
            provenance = inspect_memory_provenance(
                _MemoryClient(["session-1", "session-2", "unexpected"]),
                dataset_path=dataset,
                plans=self.plans,
                session_mode="locomo",
                max_sessions=0,
            )
            output = write_memory_provenance(temp_dir, provenance)

        self.assertEqual("mismatch", provenance["status"])
        self.assertEqual(2, provenance["expected_session_count"])
        self.assertEqual(3, provenance["actual_session_count"])
        self.assertEqual(
            hashlib.sha256(b"[]").hexdigest(),
            provenance["dataset_sha256"],
        )
        self.assertTrue(output.name == "memory_provenance.json")

    def test_session_prefix_scope_filters_search_and_filesystem(self):
        class Client:
            def search(self, *_args, **_kwargs):
                return [
                    SearchResult(
                        "echo://tenant/sessions/keep-s1", 1.0, "kept"
                    ),
                    SearchResult(
                        "echo://tenant/sessions/drop-s1", 0.9, "dropped"
                    ),
                    SearchResult("graph://entity:Jon", 0.8, "graph"),
                ]

            def fs_glob(self, *_args, **_kwargs):
                return [
                    {"uri": "echo://tenant/sessions/keep-s1/overview.md"},
                    {"uri": "echo://tenant/sessions/drop-s1/overview.md"},
                ]

            def fs_list(self, *_args, **_kwargs):
                return self.fs_glob()

            def fs_read(self, uri, **_kwargs):
                return uri

        scoped = SessionPrefixMemoryClient(Client(), "keep-")

        self.assertEqual(
            ["echo://tenant/sessions/keep-s1", "graph://entity:Jon"],
            [item.uri for item in scoped.search("question")],
        )
        self.assertEqual(1, len(scoped.fs_glob("*")))
        self.assertEqual(1, len(scoped.fs_list("*")))
        with self.assertRaisesRegex(ValueError, "outside configured prefix"):
            scoped.fs_read("echo://tenant/sessions/drop-s1/overview.md")

    def test_excluding_files_hides_and_rejects_excluded_leaf(self):
        class Client:
            def fs_glob(self, *_args, **_kwargs):
                return [
                    {"uri": "echo://engine/sessions/s1/overview.md"},
                    {"uri": "echo://engine/sessions/s1/messages.jsonl"},
                ]

            def fs_list(self, *_args, **_kwargs):
                return self.fs_glob()

            def fs_read(self, uri, **_kwargs):
                return uri

        filtered = ExcludingMemoryFilesClient(
            Client(),
            ["messages.jsonl"],
        )

        self.assertEqual(1, len(filtered.fs_glob("*")))
        self.assertEqual(1, len(filtered.fs_list("*")))
        self.assertEqual(
            "echo://engine/sessions/s1/overview.md",
            filtered.fs_read("echo://engine/sessions/s1/overview.md"),
        )
        with self.assertRaisesRegex(ValueError, "excluded by access policy"):
            filtered.fs_read(
                "echo://engine/sessions/s1/messages.jsonl"
            )
