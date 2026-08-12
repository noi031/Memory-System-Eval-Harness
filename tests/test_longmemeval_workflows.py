from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from benchmarks.longmemeval.evaluate import evaluate_longmemeval
from benchmarks.longmemeval.import_memory import import_longmemeval_memory
from benchmarks.longmemeval.parallel import (
    build_shard_commands,
    partition_question_ids,
)
from benchmarks.longmemeval.recovery import (
    merge_recovered_rows,
    merge_shard_artifacts,
    recovery_question_ids,
)
from benchmarks.longmemeval.selection import select_jobs_and_plans
from shared.eval_base import EvalConfig
from shared.llm_client import LLMResponse
from shared.qa import QAResult


class _Log:
    def info(self, *_args):
        return None

    def error(self, *_args):
        return None


class _JudgeLLM:
    """Fake judge LLM; repeats the last response when exhausted."""

    def __init__(self, responses):
        self._responses = list(responses)
        self._index = 0

    def chat(
        self,
        _messages,
        *,
        temperature=None,
        response_format=False,
        thinking_disabled=False,
        omit_max_tokens=False,
    ):
        content = self._responses[min(self._index, len(self._responses) - 1)]
        self._index += 1
        return LLMResponse(
            content=content,
            prompt_tokens=0,
            completion_tokens=0,
            elapsed_s=0.0,
        )


class LongMemEvalWorkflowTests(unittest.TestCase):
    def test_official_metrics_include_task_average_and_abstention(self):
        qa_results = [
            QAResult("q1", "Q1", "A1", "A1"),
            QAResult("q2_abs", "Q2", "unknown", "I do not know"),
            QAResult("q3", "Q3", "A3", "wrong"),
        ]
        jobs = [
            SimpleNamespace(category="single-session-user"),
            SimpleNamespace(category="single-session-user"),
            SimpleNamespace(category="knowledge-update"),
        ]

        with tempfile.TemporaryDirectory() as directory:
            report = evaluate_longmemeval(
                qa_results,
                jobs,
                _JudgeLLM(["yes", "yes", "no"]),
                Path(directory),
                _Log(),
            )

            self.assertEqual(2, report.correct)
            self.assertEqual(3, report.graded)
            self.assertAlmostEqual(2 / 3, report.overall_accuracy)
            self.assertEqual(0.5, report.task_averaged_accuracy)
            self.assertEqual(1.0, report.abstention_accuracy)
            self.assertEqual(1, report.abstention_count)

    def test_random_selection_is_seeded(self):
        jobs = [
            SimpleNamespace(
                question_id=f"q{index}",
                native_question_id="",
                sample_id=f"s{index}",
            )
            for index in range(10)
        ]
        plans = [{"index": index} for index in range(10)]

        first_jobs, _ = select_jobs_and_plans(
            jobs,
            plans,
            random_count=3,
            random_seed=30,
        )
        second_jobs, _ = select_jobs_and_plans(
            jobs,
            plans,
            random_count=3,
            random_seed=30,
        )

        self.assertEqual(
            [job.question_id for job in first_jobs],
            [job.question_id for job in second_jobs],
        )

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
                {"question_id": "q3", "response": "", "retrieval_error": "empty"},
            ],
        )

        self.assertEqual("recovered", merged[1]["response"])
        self.assertEqual(1, stats["recovered"])
        self.assertEqual(["q3"], stats["retry_failures"])

    def test_parallel_partition_and_commands_are_stable(self):
        shards = partition_question_ids(["q1", "q2", "q3", "q4", "q5"], 2)

        self.assertEqual([["q1", "q3", "q5"], ["q2", "q4"]], shards)
        with tempfile.TemporaryDirectory() as directory:
            commands = build_shard_commands(
                [
                    "--dataset",
                    "dataset.json",
                    "--out-dir",
                    "old",
                    "--parallel-shards",
                    "2",
                    "--questions",
                    "5",
                ],
                shards,
                Path(directory),
            )

        self.assertNotIn("old", commands[0]["command"])
        self.assertIn("q1,q3,q5", commands[0]["command"])
        self.assertEqual(2, len(commands))

    def test_merges_shard_artifacts_and_recomputes_accuracy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_one = root / "one"
            run_two = root / "two"
            output = root / "merged"
            for run in (run_one, run_two):
                run.mkdir()
                (run / "summary.json").write_text(
                    '{"status":"completed"}',
                    encoding="utf-8",
                )
                (run / "import_results.csv").write_text(
                    "question_id,status\n",
                    encoding="utf-8",
                )
            (run_one / "qa_results.csv").write_text(
                "question_id,response,llm_error,retrieval_error,prompt_tokens,completion_tokens\n"
                "q1,answer,,,10,2\n",
                encoding="utf-8",
            )
            (run_two / "qa_results.csv").write_text(
                "question_id,response,llm_error,retrieval_error,prompt_tokens,completion_tokens\n"
                "q2,wrong,,,12,3\n",
                encoding="utf-8",
            )
            (run_one / "eval_results.csv").write_text(
                "question_id,correct,judge_error\nq1,True,\n",
                encoding="utf-8",
            )
            (run_two / "eval_results.csv").write_text(
                "question_id,correct,judge_error\nq2,False,\n",
                encoding="utf-8",
            )

            summary = merge_shard_artifacts([run_one, run_two], output)

            self.assertEqual("completed", summary["status"])
            self.assertEqual(2, summary["total_questions"])
            self.assertEqual(0.5, summary["overall_accuracy"])
            self.assertEqual(22, summary["total_prompt_tokens"])


if __name__ == "__main__":
    unittest.main()
