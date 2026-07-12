from __future__ import annotations

import argparse
import calendar
import re
from datetime import datetime
from typing import Any, Callable

import benchmark_adapter
from echomemory_qa_common import LONGMEMEVAL_ABSTAIN_TEXT, compact


def sanitize_final_answer_text(answer: str) -> str:
    text = str(answer or "").strip()
    if not text:
        return ""
    text = text.replace("\r", "\n")
    text = re.sub(r"<mem_thinking>[\s\S]*?</mem_thinking>", " ", text, flags=re.I)
    text = re.sub(r"<judge_thinking>[\s\S]*?</judge_thinking>", " ", text, flags=re.I)
    for pattern in (
        r"<\|?DSML\|?[\s\S]*$",
        r"<｜DSML｜[\s\S]*$",
        r"<memory_search[\s\S]*$",
        r"<functioncall[\s\S]*$",
        r"<function[\s\S]*$",
        r"<invoke[\s\S]*$",
        r"<execute[\s\S]*$",
    ):
        text = re.sub(pattern, "", text, flags=re.I)
    text = re.sub(r"`{3}[\s\S]*?`{3}", "", text)
    text = re.sub(r"`[^`]*`", "", text)
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"^(?:answer_[0-9a-z]+(?:_abs)?\s+)+", "", text, flags=re.I).strip()
    text = re.sub(r"^(?:turn_\d+\s+)+", "", text, flags=re.I).strip()
    text = re.sub(r"^(?:\[[^][]{1,80}\]\s*)+", "", text).strip()
    text = re.sub(r"^(?:memory_\d+\s*:\s*)+", "", text, flags=re.I).strip()
    text = re.sub(
        r"^(?:by the way|speaking of|actually|well|anyway|meanwhile|incidentally)\s*,\s*",
        "",
        text,
        flags=re.I,
    ).strip()
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
    while changed and text:
        changed = False
        for pattern in lead_patterns:
            updated = re.sub(pattern, "", text, flags=re.I).strip()
            if updated != text:
                text = updated
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
        text = text.replace(phrase, "").strip()
    text = re.sub(
        r"\bto (?:find|answer|confirm|check|verify)[^.!?]*(?:let me|i(?:'| a)?ll|i will)\s+(?:search|retrieve|look up|check)[^.!?]*[.!?]?",
        "",
        text,
        flags=re.I,
    ).strip()
    text = re.sub(
        r"\bi (?:know|found) from the retrieved memories that\s+",
        "",
        text,
        flags=re.I,
    ).strip()
    filtered_sentences = []
    for sentence in re.split(r"(?<=[.!?。！？])\s+", text):
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
    text = " ".join(filtered_sentences).strip()
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
    while changed and text:
        changed = False
        for pattern in tail_patterns:
            updated = re.sub(pattern, "", text, flags=re.I).strip()
            if updated != text:
                text = updated
                changed = True
    text = re.sub(r"\b(?:need(?:s)?|requires?) to (?:search|retrieve|look up)[^.!?]*[.!?]?$", "", text, flags=re.I).strip()
    text = re.sub(r"\b(?:let me|i(?:'| a)?ll|i will) (?:search|retrieve|look up|check)[^.!?]*$", "", text, flags=re.I).strip()
    text = re.sub(r"(?:to find [^.!?]*, )?let me search[^.!?]*[.!?]?$", "", text, flags=re.I).strip()
    text = re.sub(r"(?:to answer [^.!?]*, )?i(?:'| a)?ll check memory[^.!?]*[.!?]?$", "", text, flags=re.I).strip()
    text = re.sub(r"\s+", " ", text).strip(" -:\n\t")
    return text


def normalize_longmemeval_answer(
    answer: str,
    *,
    is_unknownish_answer_fn: Callable[[str], bool],
) -> str:
    text = sanitize_final_answer_text(answer)
    if not text:
        return text
    if is_unknownish_answer_fn(text):
        return LONGMEMEVAL_ABSTAIN_TEXT
    return text


def is_toollike_answer(answer: str) -> bool:
    text = str(answer or "").strip()
    if not text:
        return False
    lowered = text.lower()
    if (
        "<invoke" in lowered
        or "<function" in lowered
        or "tool_calls" in lowered
        or "<｜dsml｜" in lowered
        or "<functioncalls>" in lowered
    ):
        return True
    if (
        lowered.startswith("let me search")
        or lowered.startswith("let me retrieve")
        or lowered.startswith("let me look")
        or lowered.startswith("let's search")
        or lowered.startswith("lets search")
        or lowered.startswith("let's look")
        or lowered.startswith("lets look")
        or lowered.startswith("i'll search")
        or lowered.startswith("i will search")
        or lowered.startswith("i'll look")
        or lowered.startswith("i will look")
        or lowered.startswith("i need to search")
        or lowered.startswith("i should search")
        or lowered.startswith("searching for")
        or lowered.startswith("looking deeper")
        or lowered.startswith("i'm looking")
        or lowered.startswith("im looking")
        or "memory_search" in lowered
        or "let's search more specifically" in lowered
        or "lets search more specifically" in lowered
        or "look deeper into" in lowered
    ):
        return True
    return False


def answer_refinement_needed(
    job: benchmark_adapter.Job,
    answer: str,
    *,
    query_answer_kind_fn: Callable[[str], str],
    answer_needs_grounded_fallback_fn: Callable[[benchmark_adapter.Job, str], bool],
    is_unknownish_answer_fn: Callable[[str], bool],
    clean_query_text_fn: Callable[[str], str],
    is_duration_query_fn: Callable[[str], bool],
) -> bool:
    text = str(answer or "").strip()
    if not text:
        return False
    lowered = text.lower()
    q = str(job.question or "").lower()
    dataset_format = str(getattr(job, "dataset_format", "") or "").strip().lower()
    kind = query_answer_kind_fn(q)
    cleaned_question = clean_query_text_fn(q)
    if answer_needs_grounded_fallback_fn(job, text):
        if kind != "boolean" or not re.fullmatch(r"(yes|no)\b", lowered):
            return True
    if (
        kind == "generic"
        and re.search(r"\b(recommend|suggest|advice|advise|plan|ideas?|options?|prefer)\b", cleaned_question, re.I)
        and (re.fullmatch(r"(yes|no)\b", lowered) or len(text.split()) <= 2)
    ):
        return True
    if (
        kind == "generic"
        and not is_unknownish_answer_fn(text)
        and not is_toollike_answer(text)
        and 1 <= len(text.split()) <= 6
        and len(text) <= 56
        and not re.search(r"[.!?]\s+\S", text)
        and not re.fullmatch(r"\d[\d,]*(?:\.\d+)?", text)
        and not re.fullmatch(r"(yes|no|here|there|this|that|these|those|it)\b", lowered)
        and not lowered.startswith(
            (
                "based on ",
                "the information provided",
                "by the way",
                "i ",
                "we ",
                "you ",
                "he ",
                "she ",
                "they ",
            )
        )
    ):
        return False
    if dataset_format == "longmemeval":
        if is_unknownish_answer_fn(text) or lowered == LONGMEMEVAL_ABSTAIN_TEXT.lower():
            return True
        if re.search(r"\b(close to|around|about|approximately|roughly|almost|nearly)\b", lowered):
            return True
        if re.search(r"\bhow many\b|\bcount\b|\bprevious\b", q):
            return True
    if dataset_format == "hotpotqa" and is_unknownish_answer_fn(text):
        return True
    if dataset_format == "hotpotqa":
        hotpotqa_targeted_patterns = (
            r"^who\b",
            r"^what year\b",
            r"^are\b",
            r"^is\b",
            r"^was\b",
            r"^were\b",
            r"\bbased in what\b",
            r"\bbased in which\b",
            r"\bhow many\b",
            r"\bcan seat how many\b",
            r"\bwhat government position\b",
        )
        if len(text) > 80:
            return True
        if any(re.search(pattern, q) for pattern in hotpotqa_targeted_patterns) and (
            "," in text
            or " is " in lowered
            or " was " in lowered
            or " served as " in lowered
            or " based in " in lowered
        ):
            return True
    if is_duration_query_fn(q):
        return True
    if is_toollike_answer(text):
        return True
    if (
        "don't have" in lowered
        or "do not have" in lowered
        or "no information" in lowered
        or "not possible to determine" in lowered
        or "unknown from" in lowered
        or "i only have" in lowered
        or "let me do a broader search" in lowered
        or "let me retrieve" in lowered
        or "let me search" in lowered
        or "current session memory" in lowered
        or "available memories" in lowered
        or "based on my memory search results" in lowered
        or "based on the memory search results" in lowered
        or "based on the retrieved memories" in lowered
    ):
        return True
    if "\n-" in text or "\n\n-" in text or text.count("\n") >= 3:
        return True
    if len(text) > 260:
        return True
    return False


def evidence_focus_snippets(
    query: str,
    hits: list[dict[str, Any]],
    *,
    local_memory_score_fn: Callable[[str, str], float],
    limit: int = 12,
) -> str:
    scored: list[tuple[float, str, str]] = []
    focus_hit_limit = 16
    for item in hits[: min(len(hits), focus_hit_limit)]:
        uri = str(item.get("uri") or "")
        content = str(item.get("content") or "")
        for raw in re.split(r"\n+|(?<=[.!?])\s+| - ", content):
            text = " ".join(raw.split()).strip()
            if not text or len(text) < 8:
                continue
            if text.lower().startswith("## session metadata"):
                text = re.sub(r"^##\s*session metadata\s*", "", text, flags=re.I).strip()
                if not text:
                    continue
            if text.lower().startswith(("title=", "session_date=", "created_at=", "score=")):
                continue
            score = local_memory_score_fn(query, text)
            if score < 0.12:
                continue
            scored.append((score, uri, text))
    if not scored:
        return ""
    picked: list[str] = []
    seen: set[str] = set()
    uri_counts: dict[str, int] = {}
    for score, uri, text in sorted(scored, key=lambda item: item[0], reverse=True):
        normalized = text.lower()
        if normalized in seen:
            continue
        per_uri_cap = 2
        if uri and uri_counts.get(uri, 0) >= per_uri_cap:
            continue
        seen.add(normalized)
        if uri:
            uri_counts[uri] = uri_counts.get(uri, 0) + 1
        picked.append(f"- ({uri}) {text}")
        if len(picked) >= limit:
            break
    return "\n".join(picked)


def refinement_focus_text(
    job: benchmark_adapter.Job,
    hits: list[dict[str, Any]],
    draft_answer: str,
    *,
    is_unknownish_answer_fn: Callable[[str], bool],
    hotpotqa_display_title_fn: Callable[[dict[str, Any]], str],
    memory_type_of_fn: Callable[[dict[str, Any]], str],
    memory_content_fn: Callable[[dict[str, Any]], str],
    local_memory_score_fn: Callable[[str, str], float],
) -> str:
    unknownish = is_unknownish_answer_fn(draft_answer)
    focus = evidence_focus_snippets(
        job.question,
        hits,
        local_memory_score_fn=local_memory_score_fn,
        limit=14 if unknownish else 12,
    )
    if not hits:
        return focus
    min_chars = 900 if unknownish else 520
    if len(focus) >= min_chars:
        return focus

    extras: list[str] = []
    seen: set[str] = set()
    max_hits = 8 if unknownish else 5
    max_chars = 420 if unknownish else 280
    for index, item in enumerate(hits[:max_hits], 1):
        title = hotpotqa_display_title_fn(item) or memory_type_of_fn(item) or f"memory_{index}"
        snippet = compact(memory_content_fn(item), max_chars)
        if not snippet:
            continue
        line = f"- [extra {index}] {title}: {snippet}"
        normalized = line.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        extras.append(line)
    if not extras:
        return focus
    if focus:
        return f"{focus}\n" + "\n".join(extras)
    return "\n".join(extras)


def build_answer_refinement_messages(
    job: benchmark_adapter.Job,
    draft_answer: str,
    focus_snippets: str,
) -> list[dict[str, str]]:
    dataset_format = str(getattr(job, "dataset_format", "") or "").strip().lower()
    system = (
        "You refine a draft answer for a memory benchmark. "
        "Keep only the smallest exact answer supported by the evidence. "
        "Remove broader adjacent facts, extra list items, generic summaries, and unsupported embellishments. "
        "If the draft answer says unknown, not found, or no information, but the evidence contains a direct answer, replace the draft with that direct answer. "
        "For HotpotQA-style multi-hop questions, you may combine two directly supported facts when they clearly resolve the answer. "
        "When the question asks for a specific relation or attribute, keep the value tied to that asked relation and discard nearby but unrelated facts about the same entity, such as birth years, death years, headquarters, generic biographies, or other side details. "
        "If the evidence mentions multiple dates, numbers, places, or names for the same entity, choose the one that directly completes the question rather than the first one mentioned. "
        "If the question asks for a title, role, song, conference former name, seat count, population, duration, or date range, do not replace it with a related person name, birthplace, founding year, or isolated year token unless that exact field is what the question asks for. "
        "For list questions, return all and only the required items as a compact comma-separated list. "
        "For event questions, keep event names only. "
        "For temporal answers, convert ISO-like values such as 2023-06 into natural month/year wording when the evidence supports only month-level precision. "
        "For duration questions, if the evidence gives a start date and an opening/end date, return only the elapsed duration in the benchmark's compact form. "
        "When the span runs from one month to a later month on roughly the same day, prefer whole calendar months rather than a prose timeline. "
        "For offer/provide/plan/promote questions, prefer the most specific supported phrase rather than a broader category. "
        "For symbol, feeling, advice, and description questions, prefer the exact phrase used in evidence over a looser paraphrase. "
        "For profession, internship, role, city, book, and object questions, return the shortest noun phrase that fully answers the question. "
        "For recommendation or suggestion questions, never restate the prompt as a question; return either the preferred item/features or the concrete suggested activity. "
        "If the evidence contains contrastive wording such as 'besides X, I am offering Y', prefer Y when the question asks what is being offered. "
        "Never output tool calls, XML tags, markdown bullets, or explanations. "
        "If the draft answer says the information is missing but the evidence contains a direct answer, replace it with the direct answer. "
        "If the evidence truly does not answer the question, reply with 'unknown'. "
        "Reply with answer text only."
    )
    if dataset_format == "longmemeval":
        system += (
            " LongMemEval-specific rules: "
            "for current/now questions, prefer the latest matching value; when the same metric appears twice on the same day, prefer the later or updated value. "
            "For previous-before-current questions, return the earlier value, not the current one. "
            "If the evidence contains raw numbers needed for a computation, do the computation and return the exact result. "
            "If the evidence contains a precise number, replace approximate phrases like 'close to 1300' with the exact answer text expected by the evidence. "
            "If an overview mixes a running total with later incremental additions, choose the value that directly answers the question rather than summing unrelated lines."
        )
    user = (
        f"Question: {job.question}\n"
        f"Draft answer: {draft_answer}\n\n"
        f"Focused evidence:\n{focus_snippets or '(none)'}\n\n"
        "Return the minimal exact final answer:"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _small_number_words(value: int) -> str:
    words = {
        0: "zero",
        1: "one",
        2: "two",
        3: "three",
        4: "four",
        5: "five",
        6: "six",
        7: "seven",
        8: "eight",
        9: "nine",
        10: "ten",
        11: "eleven",
        12: "twelve",
    }
    return words.get(int(value), str(int(value)))


def duration_answer_override(
    job: benchmark_adapter.Job,
    answer: str,
    focus_snippets: str,
    *,
    is_duration_query_fn: Callable[[str], bool],
) -> str:
    if not is_duration_query_fn(str(job.question or "")):
        return answer
    blob = f"{answer}\n{focus_snippets or ''}"
    month_lookup = {calendar.month_name[i].lower(): i for i in range(1, 13)}
    month_pattern = "|".join(calendar.month_name[i] for i in range(1, 13))
    date_pattern = re.compile(
        rf"\b(?P<month>{month_pattern})\s+(?P<day>\d{{1,2}}),\s*(?P<year>\d{{4}})\b",
        re.I,
    )
    parsed_dates: list[datetime] = []
    for match in date_pattern.finditer(blob):
        month_num = month_lookup.get(str(match.group("month") or "").strip().lower())
        if not month_num:
            continue
        try:
            parsed_dates.append(
                datetime(
                    year=int(match.group("year")),
                    month=month_num,
                    day=int(match.group("day")),
                )
            )
        except Exception:
            continue
    unique_dates = sorted({value.date(): value for value in parsed_dates}.values(), key=lambda value: value.date())
    if len(unique_dates) < 2:
        return answer
    start_dt = unique_dates[0]
    end_dt = unique_dates[-1]
    if end_dt <= start_dt:
        return answer
    if start_dt.day == end_dt.day:
        inclusive_months = (end_dt.year - start_dt.year) * 12 + (end_dt.month - start_dt.month) + 1
        if 1 <= inclusive_months <= 24:
            return f"{_small_number_words(inclusive_months)} month" + ("s" if inclusive_months != 1 else "")
    return answer


def is_hotpotqa_job(job: benchmark_adapter.Job) -> bool:
    return str(getattr(job, "dataset_format", "") or "").strip().lower() == "hotpotqa"


def hotpotqa_disable_answer_tooling(args: argparse.Namespace) -> None:
    dataset_format = str(getattr(args, "dataset_format", "") or "").strip().lower()
    if dataset_format != "hotpotqa":
        return
    args.vikingboat_tool_loop = False
    args.initial_tool_prefetch = False
    args.toolloop_rescue_on_toollike_answer = False


def _clean_hotpotqa_span(text: str) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip(" ,.;:-")
    value = re.sub(r"^(?:the|a|an)\s+", "", value, flags=re.I)
    value = re.sub(r"\s+\((?:[^()]*)\)\s*$", "", value).strip(" ,.;:-")
    return value


def hotpotqa_display_title(
    item: dict[str, Any],
    *,
    memory_content_fn: Callable[[dict[str, Any]], str],
) -> str:
    for key in ("hotpotqa_title", "title", "document_title", "doc_title", "name"):
        value = str(item.get(key) or "").strip()
        if value:
            return value
    content = memory_content_fn(item)
    if content:
        title_match = re.search(r"^\s*#\s+(.+?)\s*:", content, flags=re.M)
        if title_match:
            candidate = " ".join(title_match.group(1).split()).strip()
            words = candidate.split()
            half = len(words) // 2
            if len(words) >= 2 and len(words) % 2 == 0 and words[:half] == words[half:]:
                candidate = " ".join(words[:half])
            if candidate:
                return candidate
    uri = str(item.get("uri") or "")
    if uri:
        leaf = uri.rstrip("/").rsplit("/", 1)[-1]
        leaf = re.sub(r"\.md(?:#.*)?$", "", leaf, flags=re.I)
        leaf = re.sub(r"^\d+_", "", leaf)
        leaf = leaf.replace("--", " ").replace("-", " ").replace("_", " ")
        return " ".join(leaf.split()).strip()
    return ""


def significant_answer_tokens(
    text: str,
    *,
    clean_query_text_fn: Callable[[str], str],
    text_tokens_fn: Callable[[str], list[str]],
    is_weak_query_token_fn: Callable[[str], bool],
) -> set[str]:
    return {
        token
        for token in text_tokens_fn(clean_query_text_fn(text))
        if token and not is_weak_query_token_fn(token)
    }


def is_question_echo_answer(
    query: str,
    answer: str,
    *,
    clean_query_text_fn: Callable[[str], str],
    text_tokens_fn: Callable[[str], list[str]],
    is_weak_query_token_fn: Callable[[str], bool],
) -> bool:
    cleaned_answer = sanitize_final_answer_text(answer)
    if not cleaned_answer or "?" not in cleaned_answer:
        return False
    query_tokens = significant_answer_tokens(
        query,
        clean_query_text_fn=clean_query_text_fn,
        text_tokens_fn=text_tokens_fn,
        is_weak_query_token_fn=is_weak_query_token_fn,
    )
    if not query_tokens:
        return False
    segments = [segment.strip(" \"'“”") for segment in re.split(r"(?<=[?])\s+", cleaned_answer) if "?" in segment]
    segments.extend(
        match.group(1).strip(" \"'“”")
        for match in re.finditer(r"[\"“']([^\"”'\n]{1,220}\?)\s*[\"”']", cleaned_answer)
    )
    if not segments:
        return False
    for segment in segments:
        segment_clean = clean_query_text_fn(segment)
        segment_tokens = significant_answer_tokens(
            segment_clean,
            clean_query_text_fn=clean_query_text_fn,
            text_tokens_fn=text_tokens_fn,
            is_weak_query_token_fn=is_weak_query_token_fn,
        )
        if not segment_tokens:
            continue
        overlap = len(query_tokens.intersection(segment_tokens))
        if (
            overlap >= max(3, min(len(query_tokens), len(segment_tokens)) // 2)
            or re.match(r"^(?:how|what|which|who|where|when|why|can|could|would|do|does|did|is|are|was|were)\b", segment_clean, re.I)
        ):
            return True
    return False
