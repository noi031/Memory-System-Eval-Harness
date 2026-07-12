from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from echomemory_common import context_item_to_dict
from echomemory_qa_common import compact
from openviking_memory_qa import token_estimate


def hit_score(item: dict[str, Any]) -> float:
    try:
        return float(item.get("score") or item.get("confidence") or 0.0)
    except (TypeError, ValueError):
        return 0.0


STOPWORDS = {
    "a",
    "an",
    "and",
    "answer",
    "at",
    "both",
    "by",
    "current",
    "date",
    "did",
    "directly",
    "do",
    "does",
    "for",
    "from",
    "has",
    "have",
    "he",
    "her",
    "his",
    "how",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "question",
    "she",
    "the",
    "their",
    "they",
    "to",
    "was",
    "were",
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
    "with",
}

LOW_SIGNAL_QUERY_TOKENS = {
    "answer",
    "answers",
    "are",
    "as",
    "be",
    "been",
    "being",
    "can",
    "could",
    "current",
    "currently",
    "did",
    "do",
    "does",
    "done",
    "get",
    "gets",
    "had",
    "has",
    "have",
    "holding",
    "how",
    "in",
    "into",
    "kind",
    "type",
    "look",
    "looks",
    "looking",
    "lately",
    "made",
    "make",
    "means",
    "might",
    "bit",
    "named",
    "quite",
    "recently",
    "same",
    "set",
    "than",
    "that",
    "think",
    "thinks",
    "ideal",
    "decide",
    "decided",
    "start",
    "started",
    "tell",
    "tells",
    "say",
    "says",
    "said",
    "according",
    "was",
    "were",
    "what",
    "which",
    "who",
    "whom",
    "whose",
    "will",
    "with",
    "would",
    "i'm",
    "i’ve",
    "i've",
    "i’ll",
    "i'll",
    "you're",
    "you’re",
    "you've",
    "you’ve",
    "we're",
    "we’re",
    "we've",
    "we’ve",
}


def clean_query_text(query: str) -> str:
    text = re.sub(r"current date:\s*[^.]+\.", " ", str(query or ""), flags=re.I)
    text = re.sub(r"answer the question directly:\s*", " ", text, flags=re.I)
    return compact(text, 1000)


def is_unknownish_answer(text: Any) -> bool:
    lowered = str(text or "").strip().lower()
    if lowered in {
        "",
        "unknown",
        "i do not know",
        "i do not know.",
        "i don't know",
        "i don't know.",
        "not sure",
        "not sure.",
    }:
        return True
    prefix_patterns = (
        "the information provided is not enough",
        "the provided information is not enough",
        "there is not enough information",
        "i do not have enough information",
        "i don't have enough information",
        "i do not have information about",
        "i don't have information about",
        "i do not have enough details",
        "i don't have enough details",
        "there is no information",
        "no information is available",
        "i can't suggest",
        "i cannot suggest",
        "i can't determine",
        "i cannot determine",
    )
    return any(lowered.startswith(pattern) for pattern in prefix_patterns)


def normalize_title_for_match(text: Any) -> str:
    value = str(text or "").lower()
    value = re.sub(r"\([^)]*\)", " ", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def normalized_title_tokens(text: Any) -> set[str]:
    return {token for token in normalize_title_for_match(text).split() if token}


def significant_title_tokens(text: Any) -> set[str]:
    return {
        token
        for token in normalize_title_for_match(text).split()
        if token and token not in STOPWORDS and token not in LOW_SIGNAL_QUERY_TOKENS
    }


def title_token_overlap_ratio(query_title: Any, candidate_title: Any) -> float:
    query_tokens = significant_title_tokens(query_title)
    candidate_tokens = significant_title_tokens(candidate_title)
    if not query_tokens or not candidate_tokens:
        return 0.0
    return len(query_tokens.intersection(candidate_tokens)) / max(1, len(query_tokens))


def title_anchor_alignment_score(query_titles: list[str], candidate_title: Any) -> float:
    normalized_candidate = normalize_title_for_match(candidate_title)
    if not normalized_candidate:
        return 0.0
    candidate_tokens = significant_title_tokens(candidate_title)
    if not candidate_tokens:
        return 0.0
    best = 0.0
    for query_title in query_titles:
        normalized_query = normalize_title_for_match(query_title)
        if not normalized_query:
            continue
        if normalized_candidate == normalized_query:
            best = max(best, 1.0)
            continue
        overlap_ratio = title_token_overlap_ratio(query_title, candidate_title)
        if overlap_ratio <= 0:
            continue
        query_tokens = significant_title_tokens(query_title)
        overlap_tokens = query_tokens.intersection(candidate_tokens)
        score = overlap_ratio
        if len(overlap_tokens) >= 2:
            score += 0.18
        if normalized_candidate.startswith(normalized_query) or normalized_query.startswith(normalized_candidate):
            score += 0.08
        best = max(best, min(1.0, score))
    return best


def is_weak_query_token(token: Any) -> bool:
    low = str(token or "").strip().lower()
    if not low:
        return True
    if low in STOPWORDS or low in LOW_SIGNAL_QUERY_TOKENS:
        return True
    if len(low) < 3:
        return True
    return False


async def gather_search_items(
    sdk: Any,
    context: dict[str, Any],
    queries: list[str],
    limit: int,
    *,
    from_followup: bool = False,
) -> tuple[list[dict[str, Any]], list[str]]:
    if not queries:
        return [], []

    async def run_one(search_query: str) -> tuple[str, list[dict[str, Any]], str]:
        try:
            result = await sdk.search(search_query, ctx=context, budget={"max_results": limit})
            rows: list[dict[str, Any]] = []
            for item in list(getattr(result, "items", [])):
                row = context_item_to_dict(item)
                row["_matched_queries"] = [search_query]
                if from_followup:
                    row["_from_followup_query"] = True
                rows.append(row)
            return search_query, rows, ""
        except Exception as exc:
            return search_query, [], f"search[{compact(search_query, 120)}]: {exc}"

    results = await asyncio.gather(*(run_one(search_query) for search_query in queries))
    items: list[dict[str, Any]] = []
    errors: list[str] = []
    for _query, rows, error in results:
        items.extend(rows)
        if error:
            errors.append(error)
    return items, errors


def text_tokens(text: Any) -> list[str]:
    raw = re.findall(r"[a-zA-Z][a-zA-Z0-9']*|\d{4}|\d+", str(text or "").lower())
    tokens: list[str] = []
    for raw_token in raw:
        token = raw_token.strip("'")
        if not token:
            continue
        if len(token) == 1 and not token.isdigit():
            if token in STOPWORDS:
                continue
            tokens.append(token)
            continue
        if token in STOPWORDS:
            continue
        tokens.append(token)
    return tokens


def query_alias_terms(query: str) -> list[str]:
    aliases = re.findall(r"\b[A-Z][A-Za-z0-9']+\b", clean_query_text(query))
    return [term for term in aliases if str(term or "").strip() and not is_weak_query_token(term)]


def extract_named_phrases(text: Any, *, max_phrases: int = 8) -> list[str]:
    raw = str(text or "")
    if not raw:
        return []
    phrases: list[str] = []
    seen: set[str] = set()
    trim_tokens = {
        "a", "an", "the", "who", "what", "which", "where", "when", "why", "how", "and", "of",
        "in", "on", "for", "to", "at", "by", "with", "from", "are", "is", "was", "were", "do",
        "does", "did", "can", "could", "will", "would", "has", "have", "had",
    }
    patterns = [
        r'"([^"\n]{3,80})"',
        r"\b(?:[A-Z][A-Za-z0-9'&.-]*)(?:\s+(?:[A-Z][A-Za-z0-9'&.-]*|[A-Z]|and|of|the|for|to|in|on)){0,5}",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, raw):
            phrase = " ".join(str(match.group(1) if match.lastindex else match.group(0)).split()).strip(" ,.;:!?-")
            if not phrase:
                continue
            original_words = [part for part in phrase.split() if part]
            words = list(original_words)
            title_like = len(original_words) >= 2 and all(
                re.match(r"[A-Z]", part) for part in original_words if part.lower() not in {"and", "of", "the", "for", "to", "in", "on"}
            )
            lead_trim_tokens = trim_tokens if not title_like else {
                tok for tok in trim_tokens if tok in {"who", "what", "which", "where", "when", "why", "how", "are", "is", "was", "were"}
            }
            tail_trim_tokens = trim_tokens if not title_like else {
                tok for tok in trim_tokens if tok in {"and", "of", "the", "for", "to", "in", "on", "at", "by", "with", "from"}
            }
            while words and words[0].lower() in lead_trim_tokens:
                words.pop(0)
            while words and words[-1].lower() in tail_trim_tokens:
                words.pop()
            phrase = " ".join(words).strip()
            if not phrase:
                continue
            if words and len(words[-1]) == 1:
                if len(words) < 2 or not re.match(r"^[A-Z]", words[-1]):
                    continue
            if len(words) == 1 and len(phrase) < 4:
                continue
            if len(words) == 1 and is_weak_query_token(words[0]):
                continue
            if all(part.lower() in trim_tokens for part in words):
                continue
            key = phrase.lower()
            if key in seen:
                continue
            seen.add(key)
            phrases.append(phrase)
            if len(phrases) >= max_phrases:
                return phrases
    return phrases


def decoded_path_text(value: Any) -> str:
    raw = unquote(str(value or ""))
    raw = raw.replace("%20", " ")
    raw = raw.replace("/", " ")
    raw = raw.replace("_", " ")
    return raw


def combined_query_text(query: str) -> str:
    extras = query_alias_terms(query)
    if not extras:
        return clean_query_text(query)
    return clean_query_text(query) + " " + " ".join(extras)


def focused_keyword_query(query: str) -> str:
    cleaned = clean_query_text(query)
    raw_tokens = text_tokens(cleaned)
    if not raw_tokens:
        return ""
    keywords: list[str] = []
    for token in raw_tokens:
        low = str(token or "").strip().lower()
        if not low:
            continue
        if low in LOW_SIGNAL_QUERY_TOKENS:
            continue
        if len(low) < 4:
            continue
        if low not in keywords:
            keywords.append(low)
    parts: list[str] = []
    for token in keywords[:8]:
        if token and token not in parts:
            parts.append(token)
    return " ".join(parts[:8])


def context_token_estimate(user_memory: str, agent_memory: str) -> int:
    return token_estimate(
        f"### user memories:\n{user_memory or '(none)'}\n\n### agent memories:\n{agent_memory or '(none)'}"
    )


def is_comparison_style_query(query: str) -> bool:
    cleaned = clean_query_text(query) or compact(query, 1000)
    lowered = f" {cleaned.lower()} "
    if not cleaned:
        return False
    if re.match(r"^(?:are|is|was|were|do|does|did|has|have|had|can|could|should|would)\b", cleaned.lower()):
        return True
    cue_patterns = (
        " both ", " same ", " older ", " younger ", " larger ", " smaller ", " bigger ", " higher ",
        " lower ", " taller ", " shorter ", " earlier ", " later ", " longer ", " closer ", " farther ",
        " first ", " last ", " more ", " less ", " before ", " after ", " than ",
    )
    if any(pattern in lowered for pattern in cue_patterns):
        return True
    if " or " in lowered:
        named_phrases = extract_named_phrases(cleaned, max_phrases=6)
        if len(named_phrases) >= 2:
            return True
    return False


def retrieval_query_variants(query: str) -> list[str]:
    cleaned = clean_query_text(query)
    if not cleaned:
        cleaned = compact(query, 1000)
    variants: list[str] = []

    def add(value: Any) -> None:
        text = compact(value, 1000).strip()
        normalized = re.sub(r"\s+", " ", text).strip(" ,.;:!?-")
        if not normalized:
            return
        tokens = text_tokens(normalized)
        if not tokens:
            return
        if len(tokens) == 1 and is_weak_query_token(tokens[0]):
            return
        if normalized not in variants:
            variants.append(normalized)

    add(cleaned)
    focused = focused_keyword_query(query)
    add(focused)
    entity_phrases = extract_named_phrases(cleaned, max_phrases=6)
    entity_token_set = {tok for phrase in entity_phrases for tok in text_tokens(phrase)}
    focused_relation_tokens = [tok for tok in text_tokens(focused) if tok not in entity_token_set]
    relation_hint = " ".join(focused_relation_tokens[:4])
    for phrase in entity_phrases:
        add(phrase)
        if focused:
            add(f"{phrase} {focused}")
        if relation_hint:
            add(f"{phrase} {relation_hint}")
    extras = [str(term or "").strip() for term in query_alias_terms(cleaned)]
    extras = [term for term in extras if term]
    extras_text = " ".join(dict.fromkeys(extras))
    if extras_text:
        add(f"{cleaned} {extras_text}")
        if focused:
            add(f"{focused} {extras_text}")
    return variants[:10]


def direct_primary_queries(query: str, *, max_queries: int = 4) -> list[str]:
    cleaned = clean_query_text(query) or compact(query, 1000)
    focused = focused_keyword_query(query)
    entity_phrases = extract_named_phrases(cleaned, max_phrases=6)
    quoted_spans = {match.group(1).strip().lower() for match in re.finditer(r'"([^"\n]{2,80})"', str(query or ""))}
    comparison_queries = comparison_entity_queries(cleaned, max_queries=2)
    alias_queries = relation_alias_queries(cleaned, max_queries=2)
    queries: list[str] = []

    def add(value: Any) -> None:
        text = compact(value, 240).strip()
        text = re.sub(r"\s+", " ", text).strip(" ,.;:!?-")
        if text and text not in queries:
            queries.append(text)

    add(cleaned)
    add(focused)
    for candidate in alias_queries:
        add(candidate)
        if len(queries) >= max(1, int(max_queries)):
            return queries[: max(1, int(max_queries))]
    for candidate in comparison_queries:
        add(candidate)
        if len(queries) >= max(1, int(max_queries)):
            return queries[: max(1, int(max_queries))]
    for phrase in entity_phrases:
        if is_comparison_style_query(cleaned) and re.search(r"\b(?:and|or|versus|vs\.?)\b", phrase, flags=re.I):
            continue
        phrase_words = [part for part in phrase.split() if part]
        if len(phrase_words) == 1:
            lowered = phrase.lower()
            if lowered not in quoted_spans and len(phrase) < 9 and "/" not in phrase and not re.search(r"\d", phrase):
                continue
        add(phrase)
        if len(queries) >= max(1, int(max_queries)):
            break
    return queries[: max(1, int(max_queries))]


def relation_alias_queries(query: str, *, max_queries: int = 2) -> list[str]:
    cleaned = clean_query_text(query) or compact(query, 1000)
    lowered = cleaned.lower()
    if not re.search(r"\b(stage name|known as|formerly known as)\b", lowered):
        return []
    aliases: list[str] = []

    def add_alias(value: Any) -> None:
        text = " ".join(str(value or "").split()).strip(" ,.;:!?-")
        if not text or text in aliases:
            return
        tokens = text_tokens(text)
        if len(tokens) != 1 or len(text) < 4:
            return
        aliases.append(text)

    for phrase in extract_named_phrases(cleaned, max_phrases=8):
        add_alias(phrase)
    for match in re.finditer(r"\b(?:name|as)\s+([A-Z][A-Za-z0-9'&.-]{3,})\b", cleaned):
        add_alias(match.group(1))

    queries: list[str] = []
    for alias in aliases:
        for value in (f"{alias} stage name", f"{alias} known as", alias):
            text = compact(value, 220).strip()
            text = re.sub(r"\s+", " ", text).strip(" ,.;:!?-")
            if text and text not in queries:
                queries.append(text)
        if len(queries) >= max(1, int(max_queries)):
            break
    return queries[: max(1, int(max_queries))]


def comparison_query_entities(query: str, *, max_entities: int = 2) -> list[str]:
    cleaned = clean_query_text(query) or compact(query, 1000)
    if not is_comparison_style_query(cleaned):
        return []
    entities: list[str] = []
    seen: set[str] = set()

    def add_entity(value: Any) -> None:
        words = [part for part in str(value or "").split() if part]
        while words and words[0].lower() in {
            "a", "an", "the", "are", "is", "was", "were", "do", "does", "did", "has", "have", "had", "can", "could", "should", "would",
        }:
            words.pop(0)
        while words and words[-1].lower() in {"and", "or", "versus", "vs", "vs."}:
            words.pop()
        entity = " ".join(words).strip(" ,.;:!?-")
        entity_words = [part for part in entity.split() if part]
        if len(entity_words) < 2:
            return
        key = normalize_title_for_match(entity)
        if not key or key in seen:
            return
        seen.add(key)
        entities.append(entity)

    title_token = r"(?:[A-Z][A-Za-z0-9'&.-]*|[A-Z]|\d+[A-Za-z0-9'&.-]*)"
    entity_end_pattern = re.compile(
        rf"({title_token}(?:\s+(?:(?:of|the|for|to|in|on)\s+)?{title_token}){{0,5}})$"
    )
    entity_start_pattern = re.compile(
        rf"^({title_token}(?:\s+(?:(?:of|the|for|to|in|on)\s+)?{title_token}){{0,5}})"
    )
    lowered = cleaned.lower()
    split_match = re.search(r"\b(and|or|versus|vs\.?)\b", lowered)
    if split_match:
        left = cleaned[: split_match.start()].strip()
        right = cleaned[split_match.end() :].strip()
        left_match = entity_end_pattern.search(left)
        right_match = entity_start_pattern.search(right)
        if left_match:
            add_entity(left_match.group(1))
        if right_match:
            add_entity(right_match.group(1))
    if len(entities) < 2:
        named_phrases = extract_named_phrases(cleaned, max_phrases=10)
        filtered_phrases = [
            phrase
            for phrase in named_phrases
            if len(text_tokens(phrase)) >= 2 and normalize_title_for_match(phrase) != normalize_title_for_match(cleaned)
        ]
        for phrase in filtered_phrases:
            add_entity(phrase)
            if len(entities) >= 2:
                break
    return entities[: max(1, int(max_entities))]


def comparison_entity_queries(query: str, *, max_queries: int = 2) -> list[str]:
    cleaned = clean_query_text(query) or compact(query, 1000)
    if not is_comparison_style_query(cleaned):
        return []
    focused = focused_keyword_query(cleaned)
    raw_phrases = extract_named_phrases(cleaned, max_phrases=8)
    entity_token_set = {tok for phrase in raw_phrases for tok in text_tokens(phrase)}
    relation_tokens = [tok for tok in text_tokens(focused) if tok not in entity_token_set] or text_tokens(focused)
    relation_hint = " ".join(relation_tokens[:5]).strip()
    entity_groups = comparison_query_entities(cleaned, max_entities=3)
    queries: list[str] = []
    for entity in entity_groups:
        if len(text_tokens(entity)) < 2:
            continue
        for value in ([f"{entity} {relation_hint}".strip(), entity] if relation_hint else [entity]):
            text = compact(value, 220).strip()
            text = re.sub(r"\s+", " ", text).strip(" ,.;:!?-")
            if text and text not in queries:
                queries.append(text)
            if len(queries) >= max(1, int(max_queries)):
                return queries
    return queries[: max(1, int(max_queries))]


def useful_clause_fragment(source_query: str, fragment: str) -> bool:
    cleaned_fragment = compact(fragment, 220).strip()
    if not cleaned_fragment:
        return False
    if normalize_title_for_match(cleaned_fragment) == normalize_title_for_match(source_query):
        return False
    tokens = [tok for tok in text_tokens(cleaned_fragment) if tok not in LOW_SIGNAL_QUERY_TOKENS]
    if len(tokens) < 2:
        return False
    named_phrases = extract_named_phrases(cleaned_fragment, max_phrases=4)
    return bool(named_phrases or len(tokens) >= 3)


def clause_decomposition_queries(query: str, *, max_queries: int = 2) -> list[str]:
    cleaned = clean_query_text(query) or compact(query, 1000)
    if not cleaned:
        return []
    if is_comparison_style_query(cleaned):
        return []
    fragments = re.split(r"\b(?:and|or|while|whereas|after|before|during|with)\b|[,;]", cleaned)
    queries: list[str] = []
    for candidate in fragments:
        text = compact(candidate, 220).strip()
        text = re.sub(r"\s+", " ", text).strip(" ,.;:!?-")
        if not useful_clause_fragment(cleaned, text):
            continue
        if text not in queries:
            queries.append(text)
        if len(queries) >= max(1, int(max_queries)):
            return queries
    for phrase in extract_named_phrases(cleaned, max_phrases=10):
        if len(text_tokens(phrase)) < 2:
            continue
        if phrase not in queries:
            queries.append(phrase)
        if len(queries) >= max(1, int(max_queries)):
            break
    return queries[: max(1, int(max_queries))]


def allows_bridge_entity_expansion(query: str) -> bool:
    cleaned = clean_query_text(query) or compact(query, 1000)
    if not cleaned or is_comparison_style_query(cleaned):
        return False
    if re.match(r"^(?:who|what|which|where|when|whom)\b", cleaned.lower()):
        return True
    named_phrases = extract_named_phrases(cleaned, max_phrases=6)
    if len(named_phrases) >= 2 and "?" in cleaned:
        return True
    return False


def looks_like_bridge_entity_phrase(phrase: str) -> bool:
    words = [part for part in str(phrase or "").split() if part]
    if not words:
        return False
    lead_bad = {
        "alongside", "during", "despite", "including", "featuring", "starring", "based", "located",
        "assistant", "current", "former", "scientific",
    }
    tail_bad = {
        "department", "administration", "affairs", "performance", "population", "number", "years", "year",
        "country", "city", "area", "district", "conference", "album", "film", "movie", "series", "group",
        "team", "song",
    }
    connector_tokens = {"and", "of", "the", "for", "to", "in", "on", "at", "by", "with", "from"}
    significant = [word for word in words if word.lower() not in connector_tokens]
    if not significant:
        return False
    if significant[0].lower() in lead_bad or significant[-1].lower() in tail_bad:
        return False
    if len(significant) == 1:
        token = significant[0]
        return bool(token.isupper() or re.search(r"\d", token))
    generic_count = sum(1 for word in significant if word.lower() in lead_bad or word.lower() in tail_bad)
    if generic_count >= 2:
        return False
    return True


def hotpotqa_primary_queries(query: str) -> list[str]:
    cleaned = clean_query_text(query) or compact(query, 1000)
    focused = focused_keyword_query(query)
    entity_phrases = extract_named_phrases(cleaned, max_phrases=6)
    queries: list[str] = []
    for candidate in [cleaned, focused, *entity_phrases]:
        text = compact(candidate, 220).strip()
        text = re.sub(r"\s+", " ", text).strip(" ,.;:!?-")
        if text and text not in queries:
            queries.append(text)
    return queries[:4]
