#!/usr/bin/env python3
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from memory.plugins.echomemory.tasks import build_echomemory_qa_command
from memory.runs import list_runs


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="echomemory-locomo-command-") as temp_dir:
        temp = Path(temp_dir)
        dataset = temp / "locomo.json"
        dataset.write_text("[]\n", encoding="utf-8")
        spec = build_echomemory_qa_command(
            {
                "data": str(dataset),
                "sample": "conv-30",
                "workspace": str(temp / "workspace"),
                "echomem_root": str(temp / "echomem"),
                "echomem_transport": "http",
                "echomem_base_url": "http://127.0.0.1:8015",
                "questions": "conv-30_qa0",
            },
            temp / "run",
            temp / "openviking.json",
            ROOT,
            dataset,
            {
                "account": "default",
                "answer_model": "deepseek-v4-flash",
                "judge_model": "deepseek-v4-flash",
                "judge_base_url": "https://provider.example/v1",
            },
            lambda value: Path(value),
            lambda payload, config: "test-token",
        )
        help_result = subprocess.run(
            [sys.executable, str(ROOT / "benchmark/locomo/echomemory/run_eval.py"), "--help"],
            check=True,
            capture_output=True,
            text=True,
        )
        supported = set(re.findall(r"--[a-z0-9][a-z0-9-]*", help_result.stdout))
        generated_options = [arg for arg in spec.command if arg.startswith("--")]
        unsupported = sorted(set(generated_options) - supported)
        assert not unsupported, f"unsupported EchoMemory LoCoMo options: {unsupported}"
        assert "--score-threshold" not in generated_options
        assert "--tool-min-score" not in generated_options
        assert spec.metadata["initial_score_threshold"] is None
        assert spec.metadata["tool_min_score"] is None
        assert spec.metadata["top_k"] == 25
        assert spec.metadata["tool_search_limit"] == 25
        assert spec.metadata["qa_parallelism"] == 4
        assert spec.metadata["answer_temperature"] == 0.7
        assert spec.metadata["answer_thinking_mode"] == "disabled"
        assert spec.metadata["tool_set"] == "search_read"
        assert spec.metadata["identity_mode"] == "fixed"
        assert spec.metadata["memory_tool_loop_enabled"] is True
        assert "--vikingboat-tool-loop" in spec.command
        temperature_index = spec.command.index("--answer-temperature")
        assert spec.command[temperature_index + 1] == "0.7"
        thinking_index = spec.command.index("--answer-thinking-mode")
        assert spec.command[thinking_index + 1] == "disabled"

        disabled_spec = build_echomemory_qa_command(
            {
                "data": str(dataset),
                "sample": "conv-30",
                "workspace": str(temp / "workspace"),
                "echomem_root": str(temp / "echomem"),
                "echomem_transport": "http",
                "echomem_base_url": "http://127.0.0.1:8015",
                "questions": "conv-30_qa0",
                "vikingboat_tool_loop": False,
            },
            temp / "run-disabled",
            temp / "openviking.json",
            ROOT,
            dataset,
            {
                "account": "default",
                "answer_model": "deepseek-v4-flash",
                "judge_model": "deepseek-v4-flash",
                "judge_base_url": "https://provider.example/v1",
            },
            lambda value: Path(value),
            lambda payload, config: "test-token",
        )
        assert disabled_spec.metadata["memory_tool_loop_enabled"] is False
        assert "--no-vikingboat-tool-loop" in disabled_spec.command
        assert "--evaluation-profile" not in disabled_spec.command

        run_dir = temp / "runs" / "echomemory_qa_timestamp_workspace"
        run_dir.mkdir(parents=True)
        (run_dir / "manifest.json").write_text(
            """{
  "id": "echomemory_qa_timestamp_workspace",
  "kind": "echomemory_qa",
  "dataset_format": "locomo",
  "status": "succeeded",
  "config": {"dataset_format": "locomo"},
  "summary": {"rows": 1, "summary_json": {"dataset_format": "locomo"}}
}
""",
            encoding="utf-8",
        )
        runs = list_runs(temp / "runs", dataset_format="locomo")
        assert [run["id"] for run in runs] == ["echomemory_qa_timestamp_workspace"]
    print("EchoMemory LoCoMo command smoke passed")


if __name__ == "__main__":
    main()
