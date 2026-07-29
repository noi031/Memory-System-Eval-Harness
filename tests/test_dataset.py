from __future__ import annotations

import unittest

from unittest.mock import patch

from benchmarks.locomo.dataset import session_batches
from benchmarks.longmemeval.dataset import load_dataset as load_longmemeval
from shared.dataset_io import resolve_dataset_path


class LocomoSessionBatchTests(unittest.TestCase):
    def test_preserves_sessions_roles_and_timestamps(self) -> None:
        sample = {
            "conversation": {
                "session_2": [{"speaker": "Jon", "dia_id": "d2", "text": "Later"}],
                "session_2_date_time": "2:30 PM on 20 January, 2023",
                "session_1": [
                    {"speaker": "Jon", "dia_id": "d0", "text": "Hello"},
                    {
                        "speaker": "assistant",
                        "dia_id": "d1",
                        "text": "Hi",
                        "blip_caption": "a bank",
                    },
                ],
                "session_1_date_time": "1:30 PM on 19 January, 2023",
            }
        }

        batches = session_batches(sample)

        self.assertEqual(["session_1", "session_2"], [row["session_key"] for row in batches])
        self.assertEqual("user", batches[0]["messages"][0]["role"])
        self.assertEqual("assistant", batches[0]["messages"][1]["role"])
        self.assertEqual("Jon", batches[0]["messages"][0]["role_id"])
        self.assertEqual("2023-01-19T13:30:00", batches[0]["messages"][0]["created_at"])
        self.assertIn("image description: a bank", batches[0]["messages"][1]["content"])


class LongMemEvalDateTests(unittest.TestCase):
    def test_normalizes_iso_query_time_and_preserves_turn_order(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        row = {
            "question_id": "q1",
            "question": "When?",
            "answer": "January 2",
            "question_type": "temporal-reasoning",
            "question_date": "2023-01-03T10:00:00Z",
            "haystack_dates": ["2023/01/02 (Mon) 09:00"],
            "haystack_session_ids": ["s1"],
            "haystack_sessions": [[
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi"},
            ]],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "longmemeval.json"
            path.write_text(json.dumps([row]), encoding="utf-8")
            jobs, plans = load_longmemeval(path)

        self.assertEqual("2023-01-03", jobs[0].query_time)
        messages = plans[0]["session_batches"][0]["messages"]
        self.assertEqual("2023-01-02T09:00:00", messages[0]["created_at"])
        self.assertEqual("2023-01-02T09:00:01", messages[1]["created_at"])


class DatasetDownloadTests(unittest.TestCase):
    def test_failed_download_does_not_leave_partial_dataset(self) -> None:
        import io
        import tempfile
        from pathlib import Path

        class _Response(io.BytesIO):
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.close()

        with tempfile.TemporaryDirectory() as directory:
            local_path = Path(directory) / "locomo10.json"
            source = {"filename": str(local_path), "url": "https://example.test/data.json"}
            with (
                patch("shared.dataset_io.DATASET_SOURCES", {"locomo": source}),
                patch(
                    "shared.dataset_io.urllib.request.urlopen",
                    return_value=_Response(b"{broken"),
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "无法自动获取"):
                    resolve_dataset_path("locomo")

            self.assertFalse(local_path.exists())
            self.assertFalse(local_path.with_suffix(".json.part").exists())


if __name__ == "__main__":
    unittest.main()
