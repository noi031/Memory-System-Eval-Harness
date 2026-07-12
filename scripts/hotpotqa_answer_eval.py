#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
import string
from collections import Counter
from pathlib import Path
from typing import Any


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_reference(path: Path) -> dict[str, dict[str, Any]]:
    data = load_json(path)
    rows = data if isinstance(data, list) else next((value for value in data.values() if isinstance(value, list)), [])
    refs: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        qid = str(row.get("_id") or row.get("id") or row.get("question_id") or f"hotpotqa_{index}")
        refs[qid] = row
    return refs


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8", errors="replace") as f:
        return list(csv.DictReader(f))


def parse_json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, list) else []
        except Exception:
            return []
    return []


def sanitize_prediction_text(text: Any) -> str:
    value = str(text or "").strip()
    if not value:
        return ""
    value = value.replace("\r", "\n")
    for pattern in (
        r"<\|?DSML\|?[\s\S]*$",
        r"<｜DSML｜[\s\S]*$",
        r"<memory_search[\s\S]*$",
        r"<functioncall[\s\S]*$",
        r"<function[\s\S]*$",
        r"<invoke[\s\S]*$",
        r"<execute[\s\S]*$",
    ):
        value = re.sub(pattern, "", value, flags=re.I)
    value = re.sub(r"`{3}[\s\S]*?`{3}", "", value)
    value = re.sub(r"`[^`]*`", "", value)
    value = re.sub(r"\*\*(.*?)\*\*", r"\1", value)
    value = re.sub(r"\s+", " ", value).strip()
    lead_patterns = (
        r"^(based on (?:the )?(?:available|retrieved) memor(?:y|ies)[^.!?]*[.!?]\s*)",
        r"^(based on my (?:knowledge|memory)[^.!?]*[.!?]\s*)",
        r"^(i(?:'| a)?ll check memory[^.!?]*[.!?]\s*)",
        r"^(i(?:'| a)?ll check [^.!?]*[.!?]\s*)",
        r"^(i will check [^.!?]*[.!?]\s*)",
        r"^(i(?:'| a)?ll search[^.!?]*[.!?]\s*)",
        r"^(i will search[^.!?]*[.!?]\s*)",
        r"^(let me check[^.!?]*[.!?]\s*)",
        r"^(let me search[^.!?]*[.!?]\s*)",
        r"^(let me retrieve[^.!?]*[.!?]\s*)",
        r"^(let me look[^.!?]*[.!?]\s*)",
        r"^(searching for[^.!?]*[.!?]\s*)",
    )
    changed = True
    while changed and value:
        changed = False
        for pattern in lead_patterns:
            updated = re.sub(pattern, "", value, flags=re.I).strip()
            if updated != value:
                value = updated
                changed = True
    for phrase in (
        "让我搜索一下。",
        "让我搜索一下",
        "我来搜索一下。",
        "我来搜索一下",
        "让我查一下。",
        "让我查一下",
        "根据记忆中的信息，",
        "基于记忆中的信息，",
    ):
        value = value.replace(phrase, "").strip()
    value = re.sub(
        r"\bto (?:find|answer|confirm|check|verify)[^.!?]*(?:let me|i(?:'| a)?ll|i will)\s+(?:search|retrieve|look up|check)[^.!?]*[.!?]?",
        "",
        value,
        flags=re.I,
    ).strip()
    value = re.sub(
        r"\bi (?:know|found) from the retrieved memories that\s+",
        "",
        value,
        flags=re.I,
    ).strip()
    filtered_sentences = []
    for sentence in re.split(r"(?<=[.!?。！？])\s+", value):
        piece = sentence.strip()
        if not piece:
            continue
        lowered = piece.lower()
        if (
            re.search(r"\b(let me|i(?:'| a)?ll|i will)\s+(?:search|retrieve|look up|check)\b", lowered)
            or "search my memory" in lowered
            or "check memory" in lowered
            or "retrieved memories" in lowered
            or re.search(r"(让我|我来|我会).*(搜索|查询|检索|查一下)", piece)
            or re.search(r"(需要|还需|仍需).*(查询|搜索|检索|确认)", piece)
        ):
            continue
        filtered_sentences.append(piece)
    value = " ".join(filtered_sentences).strip()
    tail_patterns = (
        r"(?:however, )?(?:the )?retrieved memor(?:y|ies) do(?:es)? not [^.!?]*[.!?]?$",
        r"(?:therefore, )?i cannot confirm[^.!?]*[.!?]?$",
        r"(?:to be thorough, )?let me verify[^.!?]*[.!?]?$",
        r"(?:i )?need to (?:search|retrieve|look up|check)[^.!?]*[.!?]?$",
        r"(?:it )?requires? (?:search|retrieval|looking up)[^.!?]*[.!?]?$",
        r"(?:about|for) [^.!?]* need(?:s)? further (?:search|lookup|retrieval)[^.!?]*[.!?]?$",
        r"(?:关于|对于)[^。！？]*?(?:需要|还需|仍需)(?:进一步)?(?:查询|搜索|检索|确认)[^。！？]*[。！？]?$",
        r"(?:让我|我来)(?:继续)?(?:搜索|查询|检索|查一下)[^。！？]*[。！？]?$",
        r"(?:还需要|仍需要)(?:进一步)?(?:查询|搜索|检索|确认)[^。！？]*[。！？]?$",
    )
    changed = True
    while changed and value:
        changed = False
        for pattern in tail_patterns:
            updated = re.sub(pattern, "", value, flags=re.I).strip()
            if updated != value:
                value = updated
                changed = True
    value = re.sub(r"\b(?:need(?:s)?|requires?) to (?:search|retrieve|look up)[^.!?]*[.!?]?$", "", value, flags=re.I).strip()
    value = re.sub(r"\b(?:let me|i(?:'| a)?ll|i will) (?:search|retrieve|look up|check)[^.!?]*$", "", value, flags=re.I).strip()
    value = re.sub(r"(?:to find [^.!?]*, )?let me search[^.!?]*[.!?]?$", "", value, flags=re.I).strip()
    value = re.sub(r"(?:to answer [^.!?]*, )?i(?:'| a)?ll check memory[^.!?]*[.!?]?$", "", value, flags=re.I).strip()
    value = re.sub(r"\s+", " ", value).strip(" -:\n\t")
    return value


def normalize_answer(text: Any) -> str:
    value = str(text or "").lower()
    value = "".join(ch for ch in value if ch not in set(string.punctuation))
    value = re.sub(r"\b(a|an|the)\b", " ", value)
    return " ".join(value.split())


def answer_precision_recall_f1(prediction: str, gold: str) -> tuple[float, float, float]:
    normalized_prediction = normalize_answer(prediction)
    normalized_gold = normalize_answer(gold)
    zero_metric = (0.0, 0.0, 0.0)
    if normalized_prediction in {"yes", "no", "noanswer"} and normalized_prediction != normalized_gold:
        return zero_metric
    if normalized_gold in {"yes", "no", "noanswer"} and normalized_prediction != normalized_gold:
        return zero_metric
    pred_tokens = normalized_prediction.split()
    gold_tokens = normalized_gold.split()
    if not pred_tokens and not gold_tokens:
        return 1.0, 1.0, 1.0
    if not pred_tokens or not gold_tokens:
        return zero_metric
    common = Counter(pred_tokens) & Counter(gold_tokens)
    same = sum(common.values())
    if same == 0:
        return zero_metric
    precision = same / len(pred_tokens)
    recall = same / len(gold_tokens)
    f1 = 2 * precision * recall / (precision + recall)
    return precision, recall, f1


def exact_match(prediction: str, gold: str) -> float:
    return 1.0 if normalize_answer(prediction) == normalize_answer(gold) else 0.0


def f1_score(prediction: str, gold: str) -> float:
    return answer_precision_recall_f1(prediction, gold)[2]


def normalize_title(text: Any) -> str:
    value = str(text or "").lower()
    value = re.sub(r"\([^)]*\)", " ", value)
    value = "".join(ch if ch not in string.punctuation else " " for ch in value)
    return " ".join(value.split())


def normalize_blob(text: Any) -> str:
    value = str(text or "").lower()
    value = "".join(ch if ch not in set(string.punctuation) else " " for ch in value)
    return " ".join(value.split())


def reference_context_pairs(ref: dict[str, Any]) -> list[tuple[str, list[str]]]:
    context = ref.get("context") or []
    pairs: list[tuple[str, list[str]]] = []
    if isinstance(context, dict):
        titles = context.get("title") or context.get("titles") or []
        sentences = context.get("sentences") or context.get("sentence") or []
        if isinstance(titles, list) and isinstance(sentences, list):
            for index, title in enumerate(titles):
                raw_sentences = sentences[index] if index < len(sentences) else []
                if isinstance(raw_sentences, str):
                    sent_list = [raw_sentences]
                elif isinstance(raw_sentences, list):
                    sent_list = [str(sentence) for sentence in raw_sentences if str(sentence).strip()]
                else:
                    sent_list = [str(raw_sentences)] if raw_sentences else []
                pairs.append((str(title), sent_list))
        return pairs
    if not isinstance(context, list):
        return pairs
    for item in context:
        if isinstance(item, dict):
            title = str(item.get("title") or item.get("name") or item.get("document") or "")
            raw_sentences = item.get("sentences") or item.get("sentence") or item.get("text") or item.get("content") or []
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            title = str(item[0])
            raw_sentences = item[1]
        else:
            continue
        if isinstance(raw_sentences, str):
            sent_list = [raw_sentences]
        elif isinstance(raw_sentences, list):
            sent_list = [str(sentence) for sentence in raw_sentences if str(sentence).strip()]
        else:
            sent_list = [str(raw_sentences)] if raw_sentences else []
        pairs.append((title, sent_list))
    return pairs


def normalize_supporting_fact(item: Any) -> tuple[str, int] | None:
    if isinstance(item, dict):
        title = str(item.get("title") or item.get("document") or item.get("doc") or "").strip()
        sent_raw = item.get("sent_id")
        if sent_raw is None:
            sent_raw = item.get("sentence_id")
        if sent_raw is None:
            sent_raw = item.get("sentence_index")
    elif isinstance(item, (list, tuple)) and len(item) >= 2:
        title = str(item[0]).strip()
        sent_raw = item[1]
    else:
        return None
    if not title:
        return None
    try:
        sent_id = int(sent_raw)
    except Exception:
        return None
    return title, sent_id


def gold_supporting_facts(ref: dict[str, Any]) -> list[list[Any]]:
    facts: list[list[Any]] = []
    for item in parse_json_list(ref.get("supporting_facts") or []):
        normalized = normalize_supporting_fact(item)
        if normalized is not None:
            facts.append([normalized[0], normalized[1]])
    return facts


def memory_item_text(item: Any) -> str:
    if not isinstance(item, dict):
        return str(item or "")
    fields = [
        item.get("content"),
        item.get("_prefetched_content"),
        item.get("text"),
        item.get("abstract"),
        item.get("preview"),
        item.get("summary"),
        item.get("overview"),
        item.get("uri"),
        item.get("path"),
    ]
    return "\n".join(str(field) for field in fields if str(field or "").strip())


def memory_title_hints(item: Any) -> set[str]:
    hints: set[str] = set()
    if not isinstance(item, dict):
        return hints
    for key in ("title", "document_title", "doc_title", "name"):
        if item.get(key):
            hints.add(normalize_title(item.get(key)))
    text = memory_item_text(item)
    for pattern in (
        r"^#\s+(.+?)\s*$",
        r"^\s*title:\s*(.+?)\s*$",
        r"/docs/\d+_([^/#?]+)\.md",
    ):
        for match in re.finditer(pattern, text, flags=re.I | re.M):
            value = re.sub(r"[-_]+", " ", match.group(1))
            if value:
                hints.add(normalize_title(value))
    return {hint for hint in hints if hint}


def explicit_supporting_facts_from_memory(row: dict[str, str]) -> list[list[Any]]:
    memories = parse_json_list(row.get("relevant_memory"))
    predictions: list[list[Any]] = []
    seen: set[tuple[str, int]] = set()
    for memory in memories:
        if not isinstance(memory, dict):
            continue
        text = memory_item_text(memory)
        title = str(
            memory.get("hotpotqa_title")
            or memory.get("title")
            or ""
        ).strip()
        sent_raw: Any = (
            memory.get("hotpotqa_sent_id")
            if memory.get("hotpotqa_sent_id") is not None
            else memory.get("sent_id")
        )
        if not title:
            title_match = re.search(r"^\s*(?:hotpotqa_title|title):\s*(.+?)\s*$", text, flags=re.I | re.M)
            if title_match:
                title = title_match.group(1).strip()
        if sent_raw is None:
            sent_match = re.search(r"^\s*(?:hotpotqa_sent_id|sent_id):\s*(\d+)\s*$", text, flags=re.I | re.M)
            if sent_match:
                sent_raw = sent_match.group(1)
        if not title or sent_raw is None:
            continue
        try:
            sent_id = int(sent_raw)
        except Exception:
            continue
        key = (title, sent_id)
        if key in seen:
            continue
        seen.add(key)
        predictions.append([title, sent_id])
    return predictions


def predicted_supporting_facts_from_row(row: dict[str, str], ref: dict[str, Any]) -> list[list[Any]]:
    explicit = parse_json_list(row.get("supporting_facts_prediction"))
    if explicit:
        predictions: list[list[Any]] = []
        seen: set[tuple[str, int]] = set()
        for item in explicit:
            normalized = normalize_supporting_fact(item)
            if normalized is None or normalized in seen:
                continue
            seen.add(normalized)
            predictions.append([normalized[0], normalized[1]])
        return predictions
    explicit_from_memory = explicit_supporting_facts_from_memory(row)
    if explicit_from_memory:
        return explicit_from_memory

    question_tokens = set(normalize_blob(ref.get("question") or row.get("question") or "").split())
    prediction_tokens = set(normalize_blob(row.get("response") or row.get("prediction") or row.get("answer") or "").split())
    low_signal = {
        "what", "which", "who", "when", "where", "were", "was", "are", "the", "and",
        "that", "this", "with", "held", "woman", "film", "question", "answer",
    }
    question_tokens = {token for token in question_tokens if len(token) > 2 and token not in low_signal}
    prediction_tokens = {token for token in prediction_tokens if len(token) > 1 and token not in low_signal}
    memories = parse_json_list(row.get("relevant_memory"))
    candidates: dict[tuple[str, int], float] = {}
    original_keys: dict[tuple[str, int], list[Any]] = {}
    for rank, memory in enumerate(memories, 1):
        text = memory_item_text(memory)
        text_norm = normalize_blob(text)
        title_hints = memory_title_hints(memory)
        if not text_norm and not title_hints:
            continue
        try:
            memory_score = float(memory.get("score") or 0.0) if isinstance(memory, dict) else 0.0
        except Exception:
            memory_score = 0.0
        rank_bonus = max(0.0, 1.0 - min(rank - 1, 20) * 0.04)
        for title, sentences in reference_context_pairs(ref):
            title_norm = normalize_title(title)
            title_match = bool(title_norm and (title_norm in title_hints or title_norm in text_norm))
            if not title_match:
                continue
            for sent_id, sentence in enumerate(sentences):
                sentence_norm = normalize_blob(sentence)
                if not sentence_norm or sentence_norm not in text_norm:
                    continue
                sent_tokens = set(sentence_norm.split())
                q_overlap = len(sent_tokens & question_tokens)
                pred_overlap = len(sent_tokens & prediction_tokens)
                score = 0.15 + 0.45 * max(0.0, min(memory_score, 1.5)) + 0.25 * rank_bonus
                score += min(0.4, 0.08 * q_overlap)
                score += min(0.5, 0.16 * pred_overlap)
                if title_norm and title_norm in normalize_blob(ref.get("question") or ""):
                    score += 0.25
                if prediction_tokens and pred_overlap:
                    score += 0.25
                if q_overlap == 0 and pred_overlap == 0:
                    score -= 0.35
                key = (title, sent_id)
                candidates[key] = max(candidates.get(key, -999.0), score)
                original_keys[key] = [title, sent_id]
    if not candidates:
        return []
    ordered = sorted(candidates.items(), key=lambda item: item[1], reverse=True)
    max_score = ordered[0][1]
    selected: list[list[Any]] = []
    for key, score in ordered:
        if len(selected) >= 4:
            break
        if len(selected) >= 2 and score < max(0.65, max_score * 0.68):
            continue
        selected.append(original_keys[key])
    return selected


def supporting_fact_metrics(predicted: list[list[Any]], gold: list[list[Any]]) -> dict[str, float | int]:
    pred_set = {item for item in (normalize_supporting_fact(row) for row in predicted) if item is not None}
    gold_set = {item for item in (normalize_supporting_fact(row) for row in gold) if item is not None}
    tp = len(pred_set & gold_set)
    fp = len(pred_set - gold_set)
    fn = len(gold_set - pred_set)
    precision = 1.0 if not pred_set and not gold_set else (tp / len(pred_set) if pred_set else 0.0)
    recall = 1.0 if not pred_set and not gold_set else (tp / len(gold_set) if gold_set else 0.0)
    f1 = 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)
    return {
        "supporting_facts_em": 1.0 if pred_set == gold_set else 0.0,
        "supporting_facts_f1": f1,
        "supporting_facts_precision": precision,
        "supporting_facts_recall": recall,
        "supporting_facts_tp": tp,
        "supporting_facts_fp": fp,
        "supporting_facts_fn": fn,
        "supporting_facts_predicted_count": len(pred_set),
        "supporting_facts_gold_count": len(gold_set),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="HotpotQA scorer for answer EM/F1 plus retrieved supporting-fact prediction and joint metrics.")
    parser.add_argument("--csv", required=True)
    parser.add_argument("--reference", required=True)
    parser.add_argument("--out-dir", default="")
    parser.add_argument("--prediction-field", default="response")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    csv_path = Path(args.csv).expanduser().resolve()
    ref_path = Path(args.reference).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve() if args.out_dir else csv_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    refs = load_reference(ref_path)
    rows = []
    predictions: dict[str, str] = {}
    support_predictions: dict[str, list[list[Any]]] = {}
    for row in load_csv(csv_path):
        qid = str(row.get("question_id") or row.get("sample_id") or row.get("native_question_id") or "")
        if not qid or qid not in refs:
            continue
        raw_prediction = str(row.get(args.prediction_field) or row.get("answer") or "")
        prediction = sanitize_prediction_text(raw_prediction) or raw_prediction
        ref = refs[qid]
        gold = str(ref.get("answer") or "")
        answer_precision, answer_recall, answer_f1 = answer_precision_recall_f1(prediction, gold)
        answer_em = exact_match(prediction, gold)
        support_gold = gold_supporting_facts(ref)
        support_pred = predicted_supporting_facts_from_row(row, ref)
        support_eval = supporting_fact_metrics(support_pred, support_gold)
        joint_precision = answer_precision * float(support_eval["supporting_facts_precision"])
        joint_recall = answer_recall * float(support_eval["supporting_facts_recall"])
        joint_f1 = 0.0 if joint_precision + joint_recall == 0 else 2 * joint_precision * joint_recall / (joint_precision + joint_recall)
        rows.append({
            "question_id": qid,
            "question": ref.get("question") or row.get("question") or "",
            "answer": gold,
            "prediction": prediction,
            "answer_em": answer_em,
            "answer_f1": answer_f1,
            "answer_precision": answer_precision,
            "answer_recall": answer_recall,
            "supporting_facts": support_gold,
            "supporting_facts_prediction": support_pred,
            **support_eval,
            "joint_em": 1.0 if answer_em == 1.0 and float(support_eval["supporting_facts_em"]) == 1.0 else 0.0,
            "joint_f1": joint_f1,
            "joint_precision": joint_precision,
            "joint_recall": joint_recall,
            "type": ref.get("type") or row.get("category") or "",
            "level": ref.get("level") or "",
        })
        predictions[qid] = prediction
        support_predictions[qid] = support_pred
        if args.limit and len(rows) >= args.limit:
            break

    def avg(key: str) -> float | None:
        return round(sum(float(row[key]) for row in rows) / len(rows), 4) if rows else None

    type_counts: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        type_counts.setdefault(str(row.get("type") or "unknown"), []).append(row)

    support_tp = sum(int(row["supporting_facts_tp"]) for row in rows)
    support_fp = sum(int(row["supporting_facts_fp"]) for row in rows)
    support_fn = sum(int(row["supporting_facts_fn"]) for row in rows)
    support_recall_den = support_tp + support_fn
    support_precision_den = support_tp + support_fp
    support_micro_recall = support_tp / support_recall_den if support_recall_den else None
    support_micro_precision = support_tp / support_precision_den if support_precision_den else None
    avg_support_predicted = (
        sum(int(row["supporting_facts_predicted_count"]) for row in rows) / len(rows)
        if rows else None
    )

    summary = {
        "status": "HOTPOTQA_ANSWER_SUPPORT_EVAL_DONE",
        "metric_scope": "answer_and_supporting_facts",
        "official_metric_note": "Answer EM/F1 is aligned to HotpotQA hotpot_evaluate_v1.py, including official normalization and yes/no/noanswer handling. Supporting-fact predictions are derived from retrieved memory evidence by mapping retrieved content back to HotpotQA context title/sentence ids; joint metrics combine answer and supporting-fact precision/recall.",
        "input_csv": str(csv_path),
        "reference": str(ref_path),
        "prediction_field": args.prediction_field,
        "reference_count": len(refs),
        "graded": len(rows),
        "answer_em": avg("answer_em"),
        "answer_f1": avg("answer_f1"),
        "answer_precision": avg("answer_precision"),
        "answer_recall": avg("answer_recall"),
        "supporting_facts_em": avg("supporting_facts_em"),
        "supporting_facts_f1": avg("supporting_facts_f1"),
        "supporting_facts_precision": avg("supporting_facts_precision"),
        "supporting_facts_recall": avg("supporting_facts_recall"),
        "supporting_facts_micro_precision": round(support_micro_precision, 4) if support_micro_precision is not None else None,
        "supporting_facts_micro_recall": round(support_micro_recall, 4) if support_micro_recall is not None else None,
        "supporting_facts_tp": support_tp,
        "supporting_facts_fp": support_fp,
        "supporting_facts_fn": support_fn,
        "avg_supporting_facts_predicted": round(avg_support_predicted, 4) if avg_support_predicted is not None else None,
        "joint_em": avg("joint_em"),
        "joint_f1": avg("joint_f1"),
        "joint_precision": avg("joint_precision"),
        "joint_recall": avg("joint_recall"),
        "hotpotqa_info_matrix": (
            f"hotpotqa Recall: {support_tp}/{support_recall_den} = {support_micro_recall * 100:.2f}%\n"
            f"hotpotqa Precision: {support_tp}/{support_precision_den} = {support_micro_precision * 100:.2f}%\n"
            f"hotpotqa Average Supporting Facts Predicted per Question: {avg_support_predicted:.2f}"
        ) if rows and support_micro_recall is not None and support_micro_precision is not None and avg_support_predicted is not None else "",
        "by_type": {
            key: {
                "count": len(items),
                "answer_em": round(sum(float(item["answer_em"]) for item in items) / len(items), 4),
                "answer_f1": round(sum(float(item["answer_f1"]) for item in items) / len(items), 4),
                "supporting_facts_f1": round(sum(float(item["supporting_facts_f1"]) for item in items) / len(items), 4),
                "joint_f1": round(sum(float(item["joint_f1"]) for item in items) / len(items), 4),
            }
            for key, items in sorted(type_counts.items())
        },
        "predictions": str(out_dir / "hotpotqa_answer_predictions.json"),
        "supporting_facts_predictions": str(out_dir / "hotpotqa_supporting_facts_predictions.json"),
        "eval_log": str(out_dir / "hotpotqa_answer_eval_rows.jsonl"),
    }

    (out_dir / "hotpotqa_answer_predictions.json").write_text(json.dumps(predictions, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "hotpotqa_supporting_facts_predictions.json").write_text(json.dumps(support_predictions, ensure_ascii=False, indent=2), encoding="utf-8")
    with (out_dir / "hotpotqa_answer_eval_rows.jsonl").open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    (out_dir / "hotpotqa_answer_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
