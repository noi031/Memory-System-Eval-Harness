from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from backends.memory_types import CommitResult
from benchmarks.hotpotqa.evaluate import (
    answer_metrics,
    evaluate_hotpotqa,
    predict_supporting_facts,
)
from benchmarks.hotpotqa.import_memory import import_hotpotqa_memory
from benchmarks.hotpotqa.recovery import (
    merge_recovered_rows,
    recovery_question_ids,
)
from benchmarks.hotpotqa.selection import select_jobs_and_plans
from shared.eval_base import EvalConfig
from shared.qa import QAResult


class _Log:
    def error(self, *_args):
        return None


class _NoWriteClient:
    def __getattr__(self, name):
        raise AssertionError(f"reuse mode attempted backend call: {name}")


class _RecordingClient:
    def __init__(self):
        self.opened = []
        self.messages = []

    def open_session(self, title=""):
        self.opened.append(title)
        return "shared-session"

    def add_message(self, session_id, role, text, created_at=""):
        self.messages.append((session_id, role, text, created_at))

    def commit_session(self, session_id):
        return f"archive-{session_id}"

    def poll_commit(self, session_id, archive_id, **_kwargs):
        return CommitResult(
            session_id=session_id,
            archive_id=archive_id,
            status="completed",
            elapsed_s=0.25,
            polls=1,
        )


class HotpotQAWorkflowTests(unittest.TestCase):
    def test_reuse_mode_skips_all_backend_writes(self):
        jobs = [SimpleNamespace(question_id="q1")]
        plans = [{"events": [{"text": "fact"}]}]

        with tempfile.TemporaryDirectory() as directory:
            report = import_hotpotqa_memory(
                jobs,
                plans,
                _NoWriteClient(),
                EvalConfig(),
                Path(directory),
                _Log(),
                import_mode="per_question",
                reuse_existing_memory=True,
            )

            self.assertEqual(0, report.total)
            self.assertEqual("reused", report.rows[0]["status"])
            self.assertEqual({}, report.question_to_session)

    def test_global_mode_maps_every_question_to_shared_session(self):
        jobs = [
            SimpleNamespace(question_id="q1"),
            SimpleNamespace(question_id="q2"),
        ]
        plans = [
            {"events": [{"text": "fact one"}]},
            {"events": [{"text": "fact two"}]},
        ]
        client = _RecordingClient()

        with tempfile.TemporaryDirectory() as directory:
            report = import_hotpotqa_memory(
                jobs,
                plans,
                client,
                EvalConfig(),
                Path(directory),
                _Log(),
                import_mode="global",
                reuse_existing_memory=False,
            )

            self.assertEqual(["hotpotqa_global"], client.opened)
            self.assertEqual(2, len(client.messages))
            self.assertEqual(
                {"q1": "shared-session", "q2": "shared-session"},
                report.question_to_session,
            )
            self.assertEqual(1, report.completed)

    def test_answer_metrics_match_hotpot_normalization(self):
        metrics = answer_metrics("The Eiffel Tower.", "Eiffel Tower")

        self.assertEqual(1.0, metrics["em"])
        self.assertEqual(1.0, metrics["f1"])
        self.assertEqual(0.0, answer_metrics("yes", "no")["f1"])

    def test_supporting_fact_and_joint_metrics_use_retrieval_content(self):
        result = QAResult(
            question_id="q1",
            question="Where is the tower?",
            answer="Paris",
            response="Paris",
            retrieval_items=[{
                "uri": "memory://tower",
                "score": 1.0,
                "content": (
                    "title: Eiffel Tower\n"
                    "The Eiffel Tower is in Paris."
                ),
                "type": "memory",
            }],
        )
        references = {
            "q1": {
                "_id": "q1",
                "context": [[
                    "Eiffel Tower",
                    [
                        "The Eiffel Tower is in Paris.",
                        "It opened in 1889.",
                    ],
                ]],
                "supporting_facts": [["Eiffel Tower", 0]],
            }
        }

        with tempfile.TemporaryDirectory() as directory:
            report = evaluate_hotpotqa(
                [result],
                references,
                Path(directory),
            )

            self.assertEqual(1.0, report.answer_em)
            self.assertEqual(1.0, report.supporting_facts_em)
            self.assertEqual(1.0, report.supporting_facts_f1)
            self.assertEqual(1.0, report.joint_f1)

    def test_explicit_supporting_fact_metadata_is_preserved(self):
        predicted = predict_supporting_facts(
            [{
                "content": "opaque evidence",
                "hotpotqa_title": "Doc",
                "hotpotqa_sent_id": 1,
            }],
            {
                "supporting_facts": [["Doc", 1]],
                "context": [["Doc", ["zero", "one"]]],
            },
        )

        self.assertEqual({("Doc", 1)}, predicted)

    def test_explicit_selection_keeps_dataset_order(self):
        jobs = [
            SimpleNamespace(
                question_id="q1",
                native_question_id="native-1",
                sample_id="sample-1",
            ),
            SimpleNamespace(
                question_id="q2",
                native_question_id="native-2",
                sample_id="sample-2",
            ),
        ]
        plans = [{"id": 1}, {"id": 2}]

        selected_jobs, selected_plans = select_jobs_and_plans(
            jobs,
            plans,
            question_ids=["native-2", "q1"],
        )

        self.assertEqual(["q1", "q2"], [job.question_id for job in selected_jobs])
        self.assertEqual([1, 2], [plan["id"] for plan in selected_plans])

    def test_recovery_selects_failed_and_missing_questions(self):
        rows = [
            {"question_id": "q1", "response": "answer"},
            {"question_id": "q2", "response": "", "llm_error": "timeout"},
        ]

        self.assertEqual(
            ["q2", "q3"],
            recovery_question_ids(
                "failed-or-missing",
                rows,
                ["q1", "q2", "q3"],
            ),
        )

    def test_recovery_merge_keeps_only_successful_retries(self):
        merged, stats = merge_recovered_rows(
            [
                {"question_id": "q1", "response": "old"},
                {"question_id": "q2", "response": "", "llm_error": "timeout"},
            ],
            [
                {"question_id": "q2", "response": "recovered"},
                {
                    "question_id": "q3",
                    "response": "",
                    "retrieval_error": "empty",
                },
            ],
        )

        self.assertEqual("recovered", merged[1]["response"])
        self.assertEqual(1, stats["recovered"])
        self.assertEqual(["q3"], stats["retry_failures"])


if __name__ == "__main__":
    unittest.main()
