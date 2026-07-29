from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from shared.csv_io import read_dict_rows


class CsvIoTests(unittest.TestCase):
    def test_reads_fields_larger_than_python_csv_default(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "large.csv"
            value = "x" * (256 * 1024)
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=["question_id", "trace"],
                )
                writer.writeheader()
                writer.writerow({"question_id": "q1", "trace": value})

            rows = read_dict_rows(path)

        self.assertEqual("q1", rows[0]["question_id"])
        self.assertEqual(value, rows[0]["trace"])

    def test_missing_ok_returns_empty_rows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "missing.csv"
            self.assertEqual([], read_dict_rows(path, missing_ok=True))
