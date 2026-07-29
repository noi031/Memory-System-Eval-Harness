from __future__ import annotations

import unittest

from scripts.backend_doctor import build_report
from shared.evidence import validate_evidence_rows


class EvidenceValidationTests(unittest.TestCase):
    def test_accepts_valid_retrieval_items_json(self) -> None:
        report = validate_evidence_rows([{
            "question_id": "q1",
            "retrieval_items_json": (
                '[{"content":"fact","uri":"mem://1","score":0.9}]'
            ),
        }])

        self.assertEqual("ok", report["status"])
        self.assertEqual(1, report["valid_items"])

    def test_rejects_malformed_json(self) -> None:
        report = validate_evidence_rows([{
            "question_id": "q1",
            "retrieval_items_json": "{not-json",
        }])

        self.assertEqual("fail", report["status"])
        self.assertEqual(1, report["parse_error_rows"])

    def test_fails_when_all_retrieval_rows_are_empty(self) -> None:
        report = validate_evidence_rows([{
            "question_id": "q1",
            "retrieval_items_json": "[]",
        }])

        self.assertEqual("fail", report["status"])
        self.assertEqual(1, report["empty_rows"])

    def test_warns_when_only_some_rows_are_empty(self) -> None:
        report = validate_evidence_rows([
            {
                "question_id": "q1",
                "retrieval_items_json": (
                    '[{"content":"fact","uri":"mem://1","score":0.9}]'
                ),
            },
            {
                "question_id": "q2",
                "retrieval_items_json": "[]",
            },
        ])

        self.assertEqual("warn", report["status"])


class BackendDoctorTests(unittest.TestCase):
    def test_reports_only_echomemory_with_valid_contract(self) -> None:
        report = build_report()

        self.assertEqual("ok", report["status"])
        self.assertEqual(["echomemory"], report["registered_backends"])
        self.assertEqual([], report["failed_backends"])


if __name__ == "__main__":
    unittest.main()
