"""Generate and replay workflows for dynamic EchoAgent evaluation."""

from __future__ import annotations

import random
import time
from pathlib import Path
from typing import Any

from tqdm import tqdm

from backends import create_backend_client
from benchmarks.locomo.dataset import load_dataset
from dynamic.artifacts import save_results
from dynamic.client import EchoAgentClient, simulate_typing
from dynamic.metrics import collect_round_metrics, load_evaluator_config
from dynamic.simulator import MemoryDynamicEvaluator
from shared.eval_base import EvalRun
from shared.llm_client import LLMClient


def _message_seq(message_result: dict[str, Any]) -> int:
    data = message_result.get("data") or message_result
    if data.get("error"):
        raise RuntimeError(
            f"send failed: {data.get('error')} {data.get('message', '')}"
        )
    messages = data.get("messages") or []
    for message in reversed(messages):
        if message.get("status") in {"generating", "completed"}:
            return int(message.get("seq") or 0)
    if messages:
        return int(messages[-1].get("seq") or 0)
    return int(data.get("latestContextSeq") or 0)


def _failed_round(
    round_data: dict[str, Any],
    prefetch_committed: bool,
    error: Exception,
) -> dict[str, Any]:
    query = str(round_data.get("query") or "")
    return {
        "round_id": round_data.get("id", ""),
        "query": query,
        "reply": "",
        "reply_length": 0,
        "query_length": len(query),
        "ttft_ms": None,
        "cached_tokens": 0,
        "prompt_tokens": 0,
        "prefetch_committed": prefetch_committed,
        "is_new_session": bool(round_data.get("new_session")),
        "is_injection": False,
        "complexity": round_data.get("complexity", ""),
        "ground_facts": round_data.get("ground_facts", []),
        "error": str(error),
        "relevant_memory": "[]",
    }


def _ask_echoagent(
    args,
    client: EchoAgentClient,
    session_id: str,
    round_data: dict[str, Any],
) -> dict[str, Any]:
    context_path = "/"
    query = str(round_data.get("query") or "")
    client_turn_id = ""
    prefetch_committed = False
    memory_items: list[dict[str, Any]] = []
    if len(query) > 2:
        client_turn_id, prefetch_committed = simulate_typing(
            client,
            session_id,
            context_path,
            query,
            args.typing_speed_ms,
            args.typing_jitter_ms,
        )
        if prefetch_committed:
            final = client.prefetch_finalize(
                session_id,
                context_path,
                client_turn_id,
                query,
            )
            if final:
                memory_items = (
                    (final.get("data") or final).get("memoryItems") or []
                )
    sent_at = time.monotonic()
    try:
        seq = _message_seq(client.send_message(
            session_id,
            context_path,
            query,
            client_turn_id,
        ))
    except Exception as exc:
        return _failed_round(round_data, prefetch_committed, exc)
    try:
        reply = client.stream_reply(session_id, context_path, seq)
    except Exception as exc:
        reply = {"reply": "", "ttft_ms": None, "error": str(exc)}
    return collect_round_metrics(
        round_data,
        reply,
        sent_at,
        prefetch_committed,
        memory_items,
    )


def _memory_client(args):
    return create_backend_client(
        "echomemory",
        base_url=args.echomem_url,
        api_key=args.echomem_auth_key,
        account=args.account,
        user_id=args.user_id,
        agent_id=args.agent_id,
        workspace=args.workspace,
        timeout_s=60.0,
        max_retries=3,
    )


def run_generate_mode(
    args,
    run: EvalRun,
    client: EchoAgentClient,
    llm: LLMClient,
) -> None:
    log = run.logger
    log.info("模式: generate (LLM 生成场景)")
    evaluator_config_dict = load_evaluator_config(args.evaluator_config)
    evaluator_config = {
        "mode": "dynamic",
        "num_memories": args.num_memories,
        "llm_config": {
            "model": args.scenario_model,
            "base_url": args.scenario_base_url,
            "api_key": args.scenario_api_key,
        },
    }
    if args.user_simulator_config:
        simulator_path = Path(args.user_simulator_config)
        if simulator_path.is_file():
            evaluator_config["user_simulator_config_yaml"] = (
                simulator_path.read_text(encoding="utf-8")
            )
        else:
            evaluator_config["user_simulator_config"] = (
                args.user_simulator_config
            )
    evaluator_path = Path(args.evaluator_config)
    if evaluator_path.is_file():
        evaluator_config["evaluator_config_yaml"] = evaluator_path.read_text(
            encoding="utf-8"
        )
    evaluator = MemoryDynamicEvaluator(evaluator_config)
    memories = evaluator.generate_background_memories().get("memories", [])
    facts = {
        str(fact.get("id") or ""): str(fact.get("text") or "")
        for fact in memories
        if fact.get("id") and fact.get("text")
    }
    log.info("theme=%s memories=%d", evaluator.theme, len(memories))

    memory = _memory_client(args)
    inject_session_id = memory.open_session(
        title=f"generate-{evaluator.theme}"
    )
    for fact in tqdm(memories, desc="注入记忆", unit="mem"):
        if fact.get("text"):
            memory.add_message(
                inject_session_id,
                "user",
                str(fact["text"]),
            )
    archive_id = memory.commit_session(inject_session_id)
    commit = memory.poll_commit(
        inject_session_id,
        archive_id,
        timeout_s=args.commit_timeout_s,
        poll_interval_s=args.commit_poll_interval_s,
    )
    if commit.status != "completed":
        raise RuntimeError(
            f"记忆注入失败: status={commit.status} error={commit.error}"
        )

    rounds: list[dict[str, Any]] = []
    dataset_queries: list[dict[str, Any]] = []
    previous_queries: list[str] = []
    previous_replies: list[str] = []
    session_id = ""
    session_count = 0
    for round_index in tqdm(range(args.num_queries), desc="提问", unit="q"):
        generated = evaluator.generate_next_query({
            "round_index": round_index,
            "previous_queries": previous_queries,
            "previous_replies": previous_replies,
            "is_new_session": not session_id,
        })
        query = str(generated.get("query") or "")
        if not query:
            continue
        round_data = {
            "id": f"r{round_index}",
            "query": query,
            "ground_facts": generated.get("ground_facts", []),
            "new_session": generated.get("new_session_hint", False),
            "complexity": generated.get("complexity", "simple"),
            "is_injection": False,
        }
        dataset_queries.append({
            "query": query,
            "ground_facts": round_data["ground_facts"],
            "complexity": round_data["complexity"],
            "reasoning": generated.get("reasoning", ""),
            "new_session_hint": round_data["new_session"],
        })
        if (
            not session_id
            or round_data["new_session"]
            and random.random() < args.new_session_ratio
        ):
            session_count += 1
            session_id = client.create_session(
                title=f"test-{evaluator.theme}-s{session_count}",
                memory_engine_endpoint=args.memory_engine_endpoint,
            )
        metrics = _ask_echoagent(args, client, session_id, round_data)
        metrics["session_id"] = session_id
        rounds.append(metrics)
        previous_queries.append(query)
        previous_replies.append(str(metrics.get("reply") or ""))
        log.info(
            "Q[%d] ttft=%sms cached=%d reply_len=%d",
            round_index + 1,
            metrics["ttft_ms"],
            metrics["cached_tokens"],
            metrics["reply_length"],
        )

    save_results(
        run,
        rounds,
        facts,
        llm,
        {
            "mode": "generate",
            "num_memories": args.num_memories,
            "num_queries": args.num_queries,
            "echoagent_url": args.echoagent_url,
            "evaluator_config": args.evaluator_config,
            "user_simulator_config": args.user_simulator_config,
        },
        evaluator_config_dict,
        theme=evaluator.theme,
        background_memories=memories,
        dataset_queries=dataset_queries,
        inject_session_id=inject_session_id,
        inject_user_id=args.user_id,
    )


def run_replay_mode(
    args,
    run: EvalRun,
    client: EchoAgentClient,
    llm: LLMClient,
) -> None:
    log = run.logger
    log.info("模式: replay (回放数据集: %s)", args.dataset)
    evaluator_config = load_evaluator_config(args.evaluator_config)
    jobs, plans = load_dataset(
        args.dataset,
        sample_filter=args.dataset_sample,
    )
    if args.dataset_limit > 0:
        jobs = jobs[:args.dataset_limit]
    rounds: list[dict[str, Any]] = []
    facts: dict[str, str] = {}
    memory = _memory_client(args)
    for plan_index, plan in enumerate(
        tqdm(plans, desc="回放 sample", unit="sample")
    ):
        sample_id = str(
            plan.get("sample_id") or f"sample_{plan_index}"
        )
        events = plan.get("events") or []
        if not events:
            continue
        inject_session = memory.open_session(title=f"replay-{sample_id}")
        for event in tqdm(
            events,
            desc="  inject",
            unit="msg",
            leave=False,
        ):
            if event.get("text"):
                memory.add_message(
                    inject_session,
                    "user",
                    str(event["text"]),
                    created_at=str(event.get("time") or ""),
                )
        archive_id = memory.commit_session(inject_session)
        commit = memory.poll_commit(
            inject_session,
            archive_id,
            timeout_s=args.commit_timeout_s,
            poll_interval_s=args.commit_poll_interval_s,
        )
        if commit.status != "completed":
            log.error(
                "记忆注入失败: sample=%s status=%s error=%s",
                sample_id,
                commit.status,
                commit.error,
            )
            continue
        qa_session = client.create_session(
            title=f"replay-qa-{sample_id}",
            memory_engine_endpoint=args.memory_engine_endpoint,
        )
        for job in (
            candidate for candidate in jobs
            if candidate.sample_id == sample_id
        ):
            round_data = {
                "id": job.question_id,
                "query": job.question,
                "ground_facts": [job.answer],
                "new_session": True,
                "is_injection": False,
                "complexity": job.category,
            }
            metrics = _ask_echoagent(
                args,
                client,
                qa_session,
                round_data,
            )
            metrics.update({
                "session_id": qa_session,
                "question_id": job.question_id,
                "gold_answer": job.answer,
            })
            rounds.append(metrics)
            facts[job.question_id] = job.answer
            log.info(
                "QA[%s] ttft=%sms reply_len=%d",
                job.question_id,
                metrics["ttft_ms"],
                metrics["reply_length"],
            )
    save_results(
        run,
        rounds,
        facts,
        llm,
        {
            "mode": "replay",
            "dataset": args.dataset,
            "dataset_sample": args.dataset_sample,
            "dataset_limit": args.dataset_limit,
            "echoagent_url": args.echoagent_url,
            "echomem_url": args.echomem_url,
            "evaluator_config": args.evaluator_config,
        },
        evaluator_config,
        theme="replay",
        inject_user_id=args.user_id,
    )
