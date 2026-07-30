#!/usr/bin/env python3
"""动态评测脚本: 仿真 EchoAgent+EchoMem 线上真实效果。

两种模式:
  - generate: LLM 生成背景记忆 -> 注入 EchoMem -> 逐轮 QA 测试端到端召回+TTFT
  - replay: 回放数据集对话, 直接注入 EchoMem -> 新会话 QA 测试跨 session 召回

两种模式的注入阶段都直连 EchoMem (open_session -> add_message -> commit -> poll),
不经 EchoAgent, 不触发 LLM 生成。QA 阶段走 EchoAgent 完整管线 (含 prefill/TTFT)。

所有 EchoAgent API 调用都有容错: 接口不存在 (404) 时回退。
例如 prefill/tick 返回 404 则跳过打字模拟, 直接发消息。

用法见 docs/usage.md
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from plugins import load_agent_plugin
from dynamic.artifacts import build_v2_quality_report as _build_v2_quality_report
from dynamic.workflows import run_generate_mode, run_replay_mode
from shared.eval_base import (
    EvalConfig,
    EvalRun,
    add_agent_plugin_args,
    add_eval_args,
    results_root_for,
)
from shared.llm_client import LLMClient

_CONFIGS_DIR = Path(__file__).resolve().parent / "configs"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="动态评测: 仿真 EchoAgent+EchoMem 线上效果")
    parser.add_argument("--check", action="store_true", help=argparse.SUPPRESS)

    # 模式选择
    g = parser.add_argument_group("模式")
    g.add_argument("--dataset", default="", help="数据集路径 (指定则进入 replay 模式; 不指定则 generate 模式)")
    g.add_argument("--dataset-sample", default="all")
    g.add_argument("--dataset-limit", type=int, default=0)

    # 评测器配置 (两种模式共用)
    g = parser.add_argument_group("评测器配置")
    g.add_argument("--evaluator-config",
                   default=str(_CONFIGS_DIR / "evaluator_template.yaml"),
                   help="评测器配置 YAML，路径相对于 run_eval.py (默认 configs/evaluator_template.yaml)")

    # Generate 模式参数
    g = parser.add_argument_group("Generate 模式")
    g.add_argument("--num-memories", type=int, default=5, help="生成的背景记忆数")
    g.add_argument("--num-queries", type=int, default=10, help="生成的提问数")
    g.add_argument("--new-session-ratio", type=float, default=0.3)
    g.add_argument("--typing-speed-ms", type=int, default=200)
    g.add_argument("--typing-jitter-ms", type=int, default=20)
    g.add_argument("--user-simulator-config",
                   default=str(_CONFIGS_DIR / "user_simulator_default.yaml"),
                   help="用户模拟器配置，路径相对于 run_eval.py (默认 configs/user_simulator_default.yaml)")

    # 场景生成 LLM (仅 generate 模式使用, 用于生成背景记忆和 query)
    g = parser.add_argument_group("场景生成 LLM")
    g.add_argument("--scenario-model", default=os.environ.get("ECHOAGENT_TEST_SCENARIO_MODEL", "deepseek-v4-flash"))
    g.add_argument("--scenario-base-url", default=os.environ.get("ECHOAGENT_TEST_SCENARIO_BASE_URL", ""))
    g.add_argument("--scenario-api-key", default=os.environ.get("ECHOAGENT_TEST_SCENARIO_API_KEY", ""))

    # Agent 插件 (声明 LLM / 记忆后端 / 插件特有参数)
    add_agent_plugin_args(parser, default_plugin="echo_agent")

    # 评测基础设施参数
    add_eval_args(parser)
    parser.set_defaults(out_dir="")

    return parser


def validate_dynamic_args(args: argparse.Namespace) -> list[str]:
    errors: list[str] = []
    # LLM 参数 (由插件通过 add_llm_args 声明, 用于质量评估)
    for name, value in (
        ("LLM base URL", getattr(args, "llm_base_url", "")),
        ("LLM model", getattr(args, "llm_model", "")),
        ("LLM API key", getattr(args, "llm_api_key", "")),
    ):
        if not str(value or "").strip():
            errors.append(f"missing {name}")

    # echo_agent 插件特有参数 (仅当使用 echo_agent 时校验)
    if getattr(args, "agent_plugin", "") == "echo_agent":
        for name, value in (
            ("EchoAgent URL", getattr(args, "echoagent_url", "")),
            ("username", getattr(args, "username", "")),
            ("password", getattr(args, "password", "")),
        ):
            if not str(value or "").strip():
                errors.append(f"missing {name}")

    evaluator_path = Path(args.evaluator_config).expanduser()
    if not evaluator_path.is_file():
        errors.append(f"evaluator config not found: {evaluator_path}")
    if args.dataset:
        dataset_path = Path(args.dataset).expanduser()
        if not dataset_path.is_file():
            errors.append(f"dataset not found: {dataset_path}")
    else:
        for name, value in (
            ("scenario base URL", args.scenario_base_url),
            ("scenario model", args.scenario_model),
            ("scenario API key", args.scenario_api_key),
        ):
            if not str(value or "").strip():
                errors.append(f"missing {name}")
        simulator_path = Path(args.user_simulator_config).expanduser()
        if not simulator_path.is_file():
            errors.append(f"user simulator config not found: {simulator_path}")

    commit_timeout = getattr(args, "commit_timeout_s", 0.0)
    commit_interval = getattr(args, "commit_poll_interval_s", 2.0)
    if commit_timeout < 0:
        errors.append("commit timeout must be >= 0")
    if commit_interval <= 0:
        errors.append("commit poll interval must be > 0")
    if args.dataset_limit < 0:
        errors.append("dataset limit must be >= 0")
    if args.num_memories < 1:
        errors.append("num memories must be >= 1")
    if args.num_queries < 1:
        errors.append("num queries must be >= 1")
    if not 0 <= args.new_session_ratio <= 1:
        errors.append("new session ratio must be between 0 and 1")
    if args.typing_speed_ms < 0 or args.typing_jitter_ms < 0:
        errors.append("typing speed and jitter must be >= 0")
    return errors


def main() -> None:
    args = build_parser().parse_args()

    # base_url 互补: 一个有另一个没有时, 没有的跟有的相同
    if args.scenario_base_url and not args.llm_base_url:
        args.llm_base_url = args.scenario_base_url
    elif args.llm_base_url and not args.scenario_base_url:
        args.scenario_base_url = args.llm_base_url

    # api_key 互补
    if args.scenario_api_key and not args.llm_api_key:
        args.llm_api_key = args.scenario_api_key
    elif args.llm_api_key and not args.scenario_api_key:
        args.scenario_api_key = args.llm_api_key

    errors = validate_dynamic_args(args)
    if errors:
        raise ValueError("; ".join(errors))

    # 加载 agent 插件 (EchoAgentPlugin.setup 内部完成登录、agent_id 设置、auth_key 解析)
    try:
        agent_plugin = load_agent_plugin(args.agent_plugin, vars(args))
    except Exception as e:
        print(f"agent plugin 加载失败: {e}", file=sys.stderr)
        sys.exit(1)

    if args.check:
        agent_plugin.memory_client.health()
        print(
            "[check] OK "
            f"benchmark=dynamic echoagent={getattr(args, 'echoagent_url', 'N/A')} "
            f"memory_backend={getattr(args, 'memory_backend', 'echomem')} "
            f"echomem={getattr(args, 'echomem_url', 'N/A')} "
            f"user={getattr(args, 'username', 'N/A')} "
            f"memory_identity={'resolved' if getattr(args, 'echomem_auth_key', '') else 'default'}"
        )
        agent_plugin.teardown()
        return

    # 创建评测运行 (在 auth_key 解析后, 确保保存的配置包含正确的 auth_key)
    results_root = results_root_for(Path(__file__).parent, args.out_dir)
    config = EvalConfig(
        memory_backend=getattr(args, "memory_backend", "echomem"),
        llm_base_url=args.llm_base_url,
        llm_model=args.llm_model,
        llm_api_key=args.llm_api_key,
    )
    run = EvalRun(
        benchmark_name="dynamic",
        results_root=results_root,
        config=config,
        echomem_log_dir=getattr(args, "echomem_log_dir", ""),
    )
    log = run.logger

    log.info("agent plugin loaded: %s", args.agent_plugin)
    log.info("agent_id=%s, auth_key=%s", getattr(args, "agent_id", ""), "已设置" if getattr(args, "echomem_auth_key", "") else "未设置")

    # 创建 LLM 客户端 (用于质量评估)
    llm = LLMClient(
        base_url=args.llm_base_url or args.scenario_base_url,
        api_key=args.llm_api_key or args.scenario_api_key,
        model=args.llm_model,
        temperature=0.3,
        max_tokens=4096,
        timeout_s=120.0,
    )

    # 选择模式
    try:
        if args.dataset:
            run_replay_mode(args, run, agent_plugin, llm)
        else:
            run_generate_mode(args, run, agent_plugin, llm)
    finally:
        agent_plugin.teardown()


if __name__ == "__main__":
    main()
