from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from stress.echomem.acceptance import (
    INCONCLUSIVE,
    NOT_IMPLEMENTED,
    build_model_analysis_input,
    evaluate_pr421_acceptance,
)
from stress.echomem.formal_data_report import render


class AcceptanceTests(unittest.TestCase):
    def test_missing_measurements_are_inconclusive_and_unavailable_are_explicit(self):
        result = evaluate_pr421_acceptance({"runs": []})
        self.assertEqual(INCONCLUSIVE, result["overall"])
        statuses = {item["status"] for item in result["checks"]}
        self.assertIn(INCONCLUSIVE, statuses)
        self.assertIn(NOT_IMPLEMENTED, statuses)

    def test_model_input_is_secret_free_and_preserves_acceptance(self):
        manifest = {
            "base_url": "http://127.0.0.1:8010",
            "scenarios": ["saturation"],
            "repeats": 1,
            "client_admission_enabled": False,
            "server_observation_mode": True,
            "runs": [],
        }
        acceptance = evaluate_pr421_acceptance(manifest)
        payload = build_model_analysis_input(manifest, acceptance)
        encoded = json.dumps(payload, ensure_ascii=False)
        self.assertIn("PR421", encoded)
        self.assertNotIn("api_key", encoded.lower())
        self.assertIn("NOT_IMPLEMENTED", encoded)

    def test_html_renders_acceptance_matrix(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = {
                "base_url": "http://127.0.0.1:8010",
                "repeats": 1,
                "runs": [],
                "acceptance": {
                    "overall": "INCONCLUSIVE",
                    "checks": [
                        {
                            "name": "B7 lane/fan-out metrics",
                            "status": "INCONCLUSIVE",
                            "target": "6 metric families",
                            "observed": {"missing": ["lane_exec"]},
                            "reason": "缺失服务端指标",
                            "evidence": "details.pr421_metric_coverage",
                        }
                    ],
                    "review": {
                        "reasonable_targets": ["分离成功延迟与超时率"],
                        "missing_or_weak_targets": ["需要游标对账"],
                    },
                },
            }
            path = root / "suite.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            output = root / "suite.html"
            render(path, output)
            document = output.read_text(encoding="utf-8")
            self.assertIn("PR421 验收矩阵", document)
            self.assertIn("需要游标对账", document)
            self.assertIn("INCONCLUSIVE", document)

    def test_saturation_without_rejections_does_not_claim_contract_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = root / "saturation" / "repeat-01" / "server-observe"
            run_dir.mkdir(parents=True)
            (run_dir / "search_results.csv").write_text(
                "status_code,end_to_end_s\n200,0.1\n200,0.2\n",
                encoding="utf-8",
            )
            result = evaluate_pr421_acceptance(
                {
                    "runs": [
                        {
                            "scenario": "saturation",
                            "output_dir": str(run_dir),
                            "summary": {},
                        }
                    ]
                }
            )
            check = next(
                item for item in result["checks"]
                if item["name"] == "Saturation rejection rate"
            )
            self.assertEqual(INCONCLUSIVE, check["status"])

    def test_saturation_rejection_requires_reason_code(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = root / "saturation" / "repeat-01" / "server-observe"
            run_dir.mkdir(parents=True)
            (run_dir / "search_results.csv").write_text(
                "status_code,end_to_end_s,retry_after_s,reason_code\n"
                "503,0.2,1,\n",
                encoding="utf-8",
            )
            result = evaluate_pr421_acceptance(
                {
                    "runs": [{
                        "scenario": "saturation",
                        "output_dir": str(run_dir),
                        "summary": {},
                    }]
                }
            )
            check = next(
                item for item in result["checks"]
                if item["name"] == "Saturation rejection rate"
            )
            self.assertEqual(INCONCLUSIVE, check["status"])

    def test_fairness_uses_commit_completion_throughput(self):
        result = evaluate_pr421_acceptance(
            {
                "runs": [{
                    "scenario": "tenant-skew",
                    "summary": {
                        "metrics": {
                            "fairness": {
                                "commit_completed_per_tenant": {
                                    "a": 10, "b": 10, "c": 10, "d": 10,
                                }
                            }
                        }
                    }
                }]
            }
        )
        check = next(
            item for item in result["checks"]
            if item["name"] == "Tenant fairness (Jain)"
        )
        self.assertEqual("PASS", check["status"])
        self.assertEqual(1.0, check["observed"])


if __name__ == "__main__":
    unittest.main()
