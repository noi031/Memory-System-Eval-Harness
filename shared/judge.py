"""Evaluation/judge utilities: LLM judge, F1/EM, LongMemEval accuracy."""

from __future__ import annotations

import json
import re
import string
from collections import Counter
from typing import Any

# ---------------------------------------------------------------------------
# HotpotQA official F1 / EM (ported from hotpotqa_answer_eval.py)
# ---------------------------------------------------------------------------

def normalize_answer(text: Any) -> str:
    """Lowercase, remove punctuation and articles, collapse whitespace."""
    value = str(text or "").lower()
    value = "".join(ch for ch in value if ch not in set(string.punctuation))
    value = re.sub(r"\b(a|an|the)\b", " ", value)
    return " ".join(value.split())


def answer_f1_em(prediction: str, gold: str) -> tuple[float, float]:
    """Return (F1, EM) for a prediction vs gold answer."""
    norm_pred = normalize_answer(prediction)
    norm_gold = normalize_answer(gold)
    # yes/no special handling
    if norm_pred in {"yes", "no", "noanswer"} and norm_pred != norm_gold:
        return 0.0, 0.0
    if norm_gold in {"yes", "no", "noanswer"} and norm_pred != norm_gold:
        return 0.0, 0.0
    pred_tokens = norm_pred.split()
    gold_tokens = norm_gold.split()
    em = 1.0 if norm_pred == norm_gold else 0.0
    if not pred_tokens and not gold_tokens:
        return 1.0, 1.0
    if not pred_tokens or not gold_tokens:
        return 0.0, em
    common = Counter(pred_tokens) & Counter(gold_tokens)
    same = sum(common.values())
    if same == 0:
        return 0.0, em
    precision = same / len(pred_tokens)
    recall = same / len(gold_tokens)
    f1 = 2 * precision * recall / (precision + recall)
    return f1, em


# ---------------------------------------------------------------------------
# LoCoMo LLM judge (ported from local_judge.py)
# ---------------------------------------------------------------------------

LOCOMO_JUDGE_SYSTEM = "You are an expert grader that determines if answers to questions match a gold standard answer."

LOCOMO_JUDGE_TEMPLATE = """Your task is to label an answer to a question as 'CORRECT' or 'WRONG'. You will be given the following data:
    (1) a question (posed by one user to another user),
    (2) a 'gold' (ground truth) answer,
    (3) a generated answer
which you will score as CORRECT/WRONG.

The point of the question is to ask about something one user should know about the other user based on their prior conversations.
The gold answer will usually be a concise and short answer that includes the referenced topic, for example:
Question: Do you remember what I got the last time I went to Hawaii?
Gold answer: A shell necklace
The generated answer might be much longer, but you should be generous with your grading - as long as it touches on the same topic as the gold answer, it should be counted as CORRECT.

For time related questions, the gold answer will be a specific date, month, year, etc. The generated answer might be much longer or use relative time references (like "last Tuesday" or "next month"), but you should be generous with your grading - as long as it refers to the same date or time period as the gold answer, it should be counted as CORRECT. Even if the format differs (e.g., "May 7th" vs "7 May"), consider it CORRECT if it's the same date.

Now it is time for the real question:
Question: {question}
Gold answer: {gold_answer}
Generated answer: {response}

First, provide a short (one sentence) explanation of your reasoning, then finish with CORRECT or WRONG.
Do NOT include both CORRECT and WRONG in your response, or it will break the evaluation script.

Respond with JSON only: {{"is_correct": "CORRECT" or "WRONG", "reasoning": "your explanation"}}"""


def parse_judge_json(text: str) -> tuple[str, str]:
    """Parse judge response JSON, return (verdict, reasoning)."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return "WRONG", f"[PARSE ERROR] No JSON found: {text[:200]}"
    try:
        parsed = json.loads(text[start : end + 1].strip())
    except Exception:
        return "WRONG", f"[PARSE ERROR] Invalid JSON: {text[:200]}"
    if not isinstance(parsed, dict):
        return "WRONG", f"[PARSE ERROR] Not a dict: {text[:200]}"
    verdict = str(parsed.get("is_correct") or "").strip().upper()
    if verdict in {"CORRECT", "WRONG"}:
        return verdict, str(parsed.get("reasoning") or "")
    return "WRONG", f"[PARSE ERROR] Unknown verdict '{verdict}': {text[:200]}"


def locomo_judge(llm, question: str, gold_answer: str, response: str) -> tuple[str, str]:
    """Judge a LoCoMo answer using an LLMClient. Returns (verdict, reasoning)."""
    prompt = LOCOMO_JUDGE_TEMPLATE.format(
        question=question,
        gold_answer=gold_answer,
        response=response,
    )
    raw = llm.judge(LOCOMO_JUDGE_SYSTEM, prompt)
    return parse_judge_json(raw)


# ---------------------------------------------------------------------------
# LongMemEval official accuracy (ported from longmemeval_official_eval.py)
# ---------------------------------------------------------------------------

LONGMEMEVAL_PROMPTS: dict[str, str] = {}


def _longmemeval_anscheck_prompt(task: str, question: str, answer: str, response: str, abstention: bool = False) -> str:
    """Build the LongMemEval answer-check prompt for a given task type."""
    if not abstention:
        if task in {"single-session-user", "single-session-assistant", "multi-session"}:
            return (
                "I will give you a question, a correct answer, and a response from a model. "
                "Please answer yes if the response contains the correct answer. Otherwise, answer no. "
                "If the response is equivalent to the correct answer or contains all the intermediate steps "
                "to get the correct answer, you should also answer yes. If the response only contains a subset "
                "of the information required by the answer, answer no. \n\n"
                f"Question: {question}\n\nCorrect Answer: {answer}\n\nModel Response: {response}\n\n"
                "Is the model response correct? Answer yes or no only."
            )
        if task == "temporal-reasoning":
            return (
                "I will give you a question, a correct answer, and a response from a model. "
                "Please answer yes if the response contains the correct answer. Otherwise, answer no. "
                "If the response is equivalent to the correct answer or contains all the intermediate steps "
                "to get the correct answer, you should also answer yes. If the response only contains a subset "
                "of the information required by the answer, answer no. In addition, do not penalize off-by-one "
                "errors for the number of days. If the question asks for the number of days/weeks/months, etc., "
                "and the model makes off-by-one errors (e.g., predicting 19 days when the answer is 18), "
                "the model's response is still correct. \n\n"
                f"Question: {question}\n\nCorrect Answer: {answer}\n\nModel Response: {response}\n\n"
                "Is the model response correct? Answer yes or no only."
            )
        if task == "knowledge-update":
            return (
                "I will give you a question, a correct answer, and a response from a model. "
                "Please answer yes if the response contains the correct answer. Otherwise, answer no. "
                "If the response contains some previous information along with an updated answer, the response "
                "should be considered as correct as long as the updated answer is the required answer.\n\n"
                f"Question: {question}\n\nCorrect Answer: {answer}\n\nModel Response: {response}\n\n"
                "Is the model response correct? Answer yes or no only."
            )
        if task == "single-session-preference":
            return (
                "I will give you a question, a rubric for desired personalized response, and a response from a model. "
                "Please answer yes if the response satisfies the desired response. Otherwise, answer no. "
                "The model does not need to reflect all the points in the rubric. The response is correct as long "
                "as it recalls and utilizes the user's personal information correctly.\n\n"
                f"Question: {question}\n\nRubric: {answer}\n\nModel Response: {response}\n\n"
                "Is the model response correct? Answer yes or no only."
            )
    return (
        "I will give you an unanswerable question, an explanation, and a response from a model. "
        "Please answer yes if the model correctly identifies the question as unanswerable. "
        "The model could say that the information is incomplete, or some other information is given but "
        "the asked information is not.\n\n"
        f"Question: {question}\n\nExplanation: {answer}\n\nModel Response: {response}\n\n"
        "Does the model correctly identify the question as unanswerable? Answer yes or no only."
    )


def parse_yes_no(text: str) -> bool:
    """Parse a yes/no response. Returns True for 'yes', False otherwise."""
    value = str(text or "").strip().lower()
    if re.search(r"\byes\b", value):
        return True
    return False


def longmemeval_judge(llm, task: str, question: str, answer: str, response: str, abstention: bool = False) -> bool:
    """Judge a LongMemEval answer. Returns True if correct, False otherwise."""
    prompt = _longmemeval_anscheck_prompt(task, question, answer, response, abstention)
    raw = llm.judge("You are an answer evaluation assistant.", prompt)
    return parse_yes_no(raw)
