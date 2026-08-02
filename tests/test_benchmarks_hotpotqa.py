"""Comprehensive unit tests for the HotpotQA benchmark module.

Covers dataset loading, evaluation metrics, memory import, QA flow,
selection logic, reporting, recovery CLI, and run_eval CLI -- without
any real HTTP, network, or subprocess calls.
"""

from __future__ import annotations

import csv
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from backends.memory_types import CommitResult
from benchmarks.hotpotqa.dataset import (
    ID_KEYS,
    TIME_KEYS,
    _compact,
    _context_pairs,
    _pick,
    context_documents,
    context_events,
    load_dataset,
)
from benchmarks.hotpotqa.evaluate import (
    EVAL_FIELDS,
    EvaluationReport,
    _explicit_supporting_fact,
    _gold_supporting_facts,
    _normalize_blob,
    answer_metrics,
    evaluate_hotpotqa,
    load_references,
    predict_supporting_facts,
    supporting_fact_metrics,
)
from benchmarks.hotpotqa.import_memory import (
    IMPORT_FIELDS,
    ImportReport,
    _add_events,
    import_hotpotqa_memory,
)
from benchmarks.hotpotqa.qa import QA_FIELDS, build_qa_tasks, run_hotpotqa_qa
from benchmarks.hotpotqa.reporting import build_summary
from benchmarks.hotpotqa.selection import parse_question_ids, select_jobs_and_plans
from shared.eval_base import EvalConfig
from shared.qa import BASE_QA_FIELDS, QAResult


# ------------------------------------------------------------------ #
#  Helpers                                                            #
# ------------------------------------------------------------------ #

class _Log:
    """Minimal logger stand-in that swallows everything."""

    def error(self, *_args, **_kwargs):
        return None

    def info(self, *_args, **_kwargs):
        return None

    def warning(self, *_args, **_kwargs):
        return None


def _ok_commit_result(session_id="s1", archive_id="a1"):
    return CommitResult(
        session_id=session_id,
        archive_id=archive_id,
        status="completed",
        elapsed_s=0.5,
        polls=1,
    )


class _RecordingClient:
    """Fake memory client that records every call and always succeeds."""

    def __init__(self):
        self.opened = []
        self.messages = []
        self.commits = []

    def open_session(self, title=""):
        self.opened.append(title)
        return f"session-{len(self.opened)}"

    def add_message(self, session_id, role, text, created_at=""):
        self.messages.append((session_id, role, text, created_at))

    def commit_session(self, session_id):
        archive = f"archive-{session_id}"
        self.commits.append(session_id)
        return archive

    def poll_commit(self, session_id, archive_id, **_kwargs):
        return _ok_commit_result(session_id, archive_id)


def _make_qa_result(
    question_id="q1",
    question="What?",
    answer="yes",
    response="yes",
    retrieval_items=None,
    llm_error="",
    retrieval_error="",
    elapsed_s=1.0,
    prompt_tokens=10,
    completion_tokens=5,
):
    return QAResult(
        question_id=question_id,
        question=question,
        answer=answer,
        response=response,
        retrieval_items=retrieval_items or [],
        llm_error=llm_error,
        retrieval_error=retrieval_error,
        elapsed_s=elapsed_s,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )


def _write_hotpotqa_json(path: Path, items: list[dict]) -> None:
    path.write_text(json.dumps(items), encoding="utf-8")


# ------------------------------------------------------------------ #
#  dataset._compact                                                   #
# ------------------------------------------------------------------ #

class TestDatasetCompact(unittest.TestCase):
    def test_normal_text_unchanged(self):
        self.assertEqual("hello world", _compact("hello world"))

    def test_none_returns_empty(self):
        self.assertEqual("", _compact(None))

    def test_empty_string_returns_empty(self):
        self.assertEqual("", _compact(""))

    def test_whitespace_collapsed(self):
        self.assertEqual("a b c", _compact("  a   b\n\nc  "))

    def test_truncation_adds_ellipsis(self):
        text = "x" * 400
        result = _compact(text, limit=50)
        self.assertEqual(50, len(result))
        self.assertTrue(result.endswith("..."))

    def test_text_at_limit_not_truncated(self):
        text = "x" * 50
        result = _compact(text, limit=50)
        self.assertEqual(text, result)

    def test_text_one_over_limit_truncated(self):
        text = "x" * 51
        result = _compact(text, limit=50)
        self.assertEqual(50, len(result))
        self.assertTrue(result.endswith("..."))


# ------------------------------------------------------------------ #
#  dataset._pick                                                      #
# ------------------------------------------------------------------ #

class TestDatasetPick(unittest.TestCase):
    def test_returns_first_available_key(self):
        mapping = {"_id": "abc", "id": "def"}
        self.assertEqual("abc", _pick(mapping, ID_KEYS))

    def test_falls_through_to_next_key(self):
        mapping = {"id": "def"}
        self.assertEqual("def", _pick(mapping, ID_KEYS))

    def test_case_insensitive(self):
        mapping = {"ID": "val"}
        self.assertEqual("val", _pick(mapping, ID_KEYS))

    def test_skips_none_values(self):
        mapping = {"_id": None, "id": "fallback"}
        self.assertEqual("fallback", _pick(mapping, ID_KEYS))

    def test_skips_empty_string_values(self):
        mapping = {"_id": "", "id": "fallback"}
        self.assertEqual("fallback", _pick(mapping, ID_KEYS))

    def test_returns_empty_when_no_match(self):
        self.assertEqual("", _pick({"foo": "bar"}, ID_KEYS))

    def test_time_keys(self):
        mapping = {"timestamp": "2024-01-01"}
        self.assertEqual("2024-01-01", _pick(mapping, TIME_KEYS))


# ------------------------------------------------------------------ #
#  dataset._context_pairs                                             #
# ------------------------------------------------------------------ #

class TestDatasetContextPairs(unittest.TestCase):
    def test_dict_context_with_titles_and_sentences(self):
        context = {
            "title": ["Doc A", "Doc B"],
            "sentences": [["s1", "s2"], ["s3"]],
        }
        pairs = _context_pairs(context)
        self.assertEqual([("Doc A", ["s1", "s2"]), ("Doc B", ["s3"])], pairs)

    def test_dict_context_with_plural_titles_key(self):
        context = {
            "titles": ["T1"],
            "sentence": [["only"]],
        }
        pairs = _context_pairs(context)
        self.assertEqual([("T1", ["only"])], pairs)

    def test_dict_context_missing_sentences_for_title(self):
        context = {"title": ["A", "B"], "sentences": [["s1"]]}
        pairs = _context_pairs(context)
        self.assertEqual([("A", ["s1"]), ("B", [])], pairs)

    def test_list_context_with_dict_items(self):
        context = [{"title": "X", "sentences": ["a", "b"]}]
        pairs = _context_pairs(context)
        self.assertEqual([("X", ["a", "b"])], pairs)

    def test_list_context_dict_item_name_fallback(self):
        context = [{"name": "N", "text": "hello"}]
        pairs = _context_pairs(context)
        self.assertEqual([("N", ["hello"])], pairs)

    def test_list_context_dict_item_document_fallback(self):
        context = [{"document": "D", "content": "world"}]
        pairs = _context_pairs(context)
        self.assertEqual([("D", ["world"])], pairs)

    def test_list_context_with_tuple_items(self):
        context = [("Title", ["s1", "s2"])]
        pairs = _context_pairs(context)
        self.assertEqual([("Title", ["s1", "s2"])], pairs)

    def test_list_context_tuple_with_string_sentences(self):
        context = [("Title", "single sentence")]
        pairs = _context_pairs(context)
        self.assertEqual([("Title", ["single sentence"])], pairs)

    def test_list_context_invalid_item_skipped(self):
        context = [("only_title",), "bad", 42]
        pairs = _context_pairs(context)
        self.assertEqual([], pairs)

    def test_empty_context(self):
        self.assertEqual([], _context_pairs([]))

    def test_none_context(self):
        self.assertEqual([], _context_pairs(None))


# ------------------------------------------------------------------ #
#  dataset.context_events                                             #
# ------------------------------------------------------------------ #

class TestDatasetContextEvents(unittest.TestCase):
    def test_generates_events(self):
        item = {"context": [{"title": "Doc", "sentences": ["Hello world"]}]}
        events = context_events(item)
        self.assertEqual(1, len(events))
        self.assertEqual("", events[0]["time"])
        self.assertIn("Doc", events[0]["text"])
        self.assertIn("Hello world", events[0]["text"])
        self.assertIn("document_1", events[0]["text"])

    def test_empty_title_and_body_skipped(self):
        item = {"context": [{"title": "", "sentences": []}]}
        self.assertEqual([], context_events(item))

    def test_whitespace_only_body_skipped(self):
        item = {"context": [{"title": "T", "sentences": ["   "]}]}
        events = context_events(item)
        self.assertEqual(1, len(events))

    def test_multiple_documents_indexed(self):
        item = {
            "context": [
                {"title": "A", "sentences": ["a1"]},
                {"title": "B", "sentences": ["b1"]},
            ]
        }
        events = context_events(item)
        self.assertEqual(2, len(events))
        self.assertIn("document_1", events[0]["text"])
        self.assertIn("document_2", events[1]["text"])

    def test_long_body_compacted(self):
        long_sentence = "y" * 2000
        item = {"context": [{"title": "T", "sentences": [long_sentence]}]}
        events = context_events(item)
        self.assertLessEqual(len(events[0]["text"]), 1600)


# ------------------------------------------------------------------ #
#  dataset.context_documents                                          #
# ------------------------------------------------------------------ #

class TestDatasetContextDocuments(unittest.TestCase):
    def test_generates_documents(self):
        item = {"context": [{"title": "Doc", "sentences": ["Body text"]}]}
        docs = context_documents(item)
        self.assertEqual(1, len(docs))
        self.assertEqual("document_1_Doc", docs[0]["doc_id"])
        self.assertEqual("Doc", docs[0]["title"])
        self.assertEqual("", docs[0]["time"])
        self.assertIn("source_dataset: HotpotQA", docs[0]["text"])
        self.assertIn("title: Doc", docs[0]["text"])
        self.assertIn("Body text", docs[0]["text"])

    def test_empty_title_uses_fallback(self):
        item = {"context": [{"title": "", "sentences": ["text"]}]}
        docs = context_documents(item)
        self.assertEqual(1, len(docs))
        self.assertEqual("document_1", docs[0]["title"])
        self.assertEqual("document_1_document_1", docs[0]["doc_id"])

    def test_empty_title_and_body_skipped(self):
        item = {"context": [{"title": "", "sentences": []}]}
        self.assertEqual([], context_documents(item))

    def test_document_text_stripped(self):
        item = {"context": [{"title": "T", "sentences": ["   ", "body"]}]}
        docs = context_documents(item)
        self.assertEqual(1, len(docs))
        # Leading/trailing whitespace stripped from the joined text
        self.assertFalse(docs[0]["text"].startswith("\n"))
        self.assertFalse(docs[0]["text"].endswith("\n"))


# ------------------------------------------------------------------ #
#  dataset.load_dataset                                               #
# ------------------------------------------------------------------ #

class TestDatasetLoadDataset(unittest.TestCase):
    def test_load_from_json_list(self):
        items = [
            {
                "_id": "h1",
                "question": "What is X?",
                "answer": "42",
                "type": "bridge",
                "level": "hard",
                "context": [{"title": "Doc", "sentences": ["Some fact."]}],
                "supporting_facts": [["Doc", 0]],
            }
        ]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "data.json"
            _write_hotpotqa_json(path, items)
            jobs, plans = load_dataset(path)
        self.assertEqual(1, len(jobs))
        self.assertEqual(1, len(plans))
        job = jobs[0]
        self.assertEqual("hotpotqa", job.dataset_format)
        self.assertEqual("h1", job.sample_id)
        self.assertEqual("h1", job.question_id)
        self.assertEqual("What is X?", job.question)
        self.assertEqual("42", job.answer)
        self.assertEqual("bridge/hard", job.category)
        self.assertEqual("h1", job.native_question_id)

    def test_category_without_level(self):
        items = [{"_id": "x", "question": "q", "answer": "a", "type": "comparison"}]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "data.json"
            _write_hotpotqa_json(path, items)
            jobs, _ = load_dataset(path)
        self.assertEqual("comparison", jobs[0].category)

    def test_category_fallback_to_hotpotqa(self):
        items = [{"_id": "x", "question": "q", "answer": "a"}]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "data.json"
            _write_hotpotqa_json(path, items)
            jobs, _ = load_dataset(path)
        self.assertEqual("hotpotqa", jobs[0].category)

    def test_category_from_category_field(self):
        items = [{"_id": "x", "question": "q", "answer": "a", "category": "custom"}]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "data.json"
            _write_hotpotqa_json(path, items)
            jobs, _ = load_dataset(path)
        self.assertEqual("custom", jobs[0].category)

    def test_default_sample_id_when_no_id(self):
        items = [{"question": "q", "answer": "a"}]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "data.json"
            _write_hotpotqa_json(path, items)
            jobs, _ = load_dataset(path)
        self.assertEqual("hotpotqa_0", jobs[0].sample_id)

    def test_query_time_picked(self):
        items = [{"_id": "x", "question": "q", "answer": "a", "timestamp": "2024-06-01"}]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "data.json"
            _write_hotpotqa_json(path, items)
            jobs, _ = load_dataset(path)
        self.assertEqual("2024-06-01", jobs[0].query_time)

    def test_sample_filter_by_id(self):
        items = [
            {"_id": "a", "question": "q1", "answer": "a1"},
            {"_id": "b", "question": "q2", "answer": "a2"},
        ]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "data.json"
            _write_hotpotqa_json(path, items)
            jobs, _ = load_dataset(path, sample_filter="b")
        self.assertEqual(1, len(jobs))
        self.assertEqual("b", jobs[0].sample_id)

    def test_sample_filter_by_index(self):
        items = [
            {"_id": "a", "question": "q1", "answer": "a1"},
            {"_id": "b", "question": "q2", "answer": "a2"},
        ]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "data.json"
            _write_hotpotqa_json(path, items)
            jobs, _ = load_dataset(path, sample_filter="1")
        self.assertEqual(1, len(jobs))
        self.assertEqual("b", jobs[0].sample_id)

    def test_sample_filter_all(self):
        items = [{"_id": "a"}, {"_id": "b"}]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "data.json"
            _write_hotpotqa_json(path, items)
            jobs, _ = load_dataset(path, sample_filter="all")
        self.assertEqual(2, len(jobs))

    def test_injection_tokens_est_with_context(self):
        items = [{"_id": "x", "context": [{"title": "T", "sentences": ["abcd"]}]}]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "data.json"
            _write_hotpotqa_json(path, items)
            jobs, _ = load_dataset(path)
        self.assertGreater(jobs[0].injection_tokens_est, 0)

    def test_injection_tokens_est_zero_without_context(self):
        items = [{"_id": "x"}]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "data.json"
            _write_hotpotqa_json(path, items)
            jobs, _ = load_dataset(path)
        self.assertEqual(0, jobs[0].injection_tokens_est)

    def test_plans_structure(self):
        items = [{
            "_id": "h1",
            "question": "q",
            "answer": "a",
            "type": "bridge",
            "level": "medium",
            "context": [{"title": "D", "sentences": ["s1"]}],
            "supporting_facts": [["D", 0]],
        }]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "data.json"
            _write_hotpotqa_json(path, items)
            _, plans = load_dataset(path)
        plan = plans[0]
        self.assertEqual("h1", plan["sample_id"])
        self.assertEqual(1, plan["event_count"])
        self.assertEqual("bridge", plan["type"])
        self.assertEqual("medium", plan["level"])
        self.assertTrue(plan["has_answer"])
        self.assertEqual([["D", 0]], plan["supporting_facts"])
        self.assertEqual(len(plan["events"]), len(plan["preview_events"]))

    def test_plans_has_answer_false_when_no_answer(self):
        items = [{"_id": "x", "question": "q"}]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "data.json"
            _write_hotpotqa_json(path, items)
            _, plans = load_dataset(path)
        self.assertFalse(plans[0]["has_answer"])

    def test_plans_has_answer_from_gold_answer(self):
        items = [{"_id": "x", "question": "q", "gold_answer": "ans"}]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "data.json"
            _write_hotpotqa_json(path, items)
            _, plans = load_dataset(path)
        self.assertTrue(plans[0]["has_answer"])

    def test_non_dict_raw_wrapped_in_input(self):
        items = ["just a string"]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "data.json"
            _write_hotpotqa_json(path, items)
            jobs, plans = load_dataset(path)
        self.assertEqual(1, len(jobs))
        self.assertEqual("", jobs[0].question)

    def test_question_falls_back_to_query_field(self):
        items = [{"_id": "x", "query": "from query field", "answer": "a"}]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "data.json"
            _write_hotpotqa_json(path, items)
            jobs, _ = load_dataset(path)
        self.assertEqual("from query field", jobs[0].question)

    def test_answer_falls_back_to_gold_answer(self):
        items = [{"_id": "x", "question": "q", "gold_answer": "gold"}]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "data.json"
            _write_hotpotqa_json(path, items)
            jobs, _ = load_dataset(path)
        self.assertEqual("gold", jobs[0].answer)

    def test_memory_documents_in_plans(self):
        items = [{"_id": "x", "context": [{"title": "D", "sentences": ["s1"]}]}]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "data.json"
            _write_hotpotqa_json(path, items)
            _, plans = load_dataset(path)
        self.assertEqual(1, len(plans[0]["memory_documents"]))
        self.assertEqual("D", plans[0]["memory_documents"][0]["title"])


# ------------------------------------------------------------------ #
#  evaluate.answer_metrics                                            #
# ------------------------------------------------------------------ #

class TestEvaluateAnswerMetrics(unittest.TestCase):
    def test_exact_match(self):
        m = answer_metrics("Paris", "Paris")
        self.assertEqual(1.0, m["em"])
        self.assertEqual(1.0, m["f1"])
        self.assertEqual(1.0, m["precision"])
        self.assertEqual(1.0, m["recall"])

    def test_yes_no_mismatch(self):
        for pred, gold in [("yes", "no"), ("no", "yes")]:
            with self.subTest(pred=pred, gold=gold):
                m = answer_metrics(pred, gold)
                self.assertEqual(0.0, m["f1"])
                self.assertEqual(0.0, m["em"])
                self.assertEqual(0.0, m["precision"])
                self.assertEqual(0.0, m["recall"])

    def test_yes_vs_normal_answer(self):
        m = answer_metrics("yes", "Paris")
        self.assertEqual(0.0, m["f1"])

    def test_noanswer_vs_normal_answer(self):
        m = answer_metrics("noanswer", "Paris")
        self.assertEqual(0.0, m["f1"])

    def test_both_empty_returns_ones(self):
        m = answer_metrics("", "")
        self.assertEqual(1.0, m["em"])
        self.assertEqual(1.0, m["f1"])
        self.assertEqual(1.0, m["precision"])
        self.assertEqual(1.0, m["recall"])

    def test_prediction_empty_gold_nonempty(self):
        m = answer_metrics("", "something")
        self.assertEqual(0.0, m["f1"])
        self.assertEqual(0.0, m["em"])

    def test_prediction_nonempty_gold_empty(self):
        m = answer_metrics("something", "")
        self.assertEqual(0.0, m["f1"])

    def test_partial_overlap(self):
        m = answer_metrics("paris france", "paris")
        self.assertGreater(m["f1"], 0.0)
        self.assertLess(m["f1"], 1.0)
        self.assertAlmostEqual(0.5, m["precision"])
        self.assertEqual(1.0, m["recall"])

    def test_no_overlap(self):
        m = answer_metrics("london", "paris")
        self.assertEqual(0.0, m["f1"])
        self.assertEqual(0.0, m["em"])

    def test_punctuation_normalized(self):
        m = answer_metrics("The Eiffel Tower.", "Eiffel Tower")
        self.assertEqual(1.0, m["em"])
        self.assertEqual(1.0, m["f1"])

    def test_articles_removed(self):
        m = answer_metrics("the cat", "cat")
        self.assertEqual(1.0, m["em"])
        self.assertEqual(1.0, m["f1"])


# ------------------------------------------------------------------ #
#  evaluate._normalize_blob                                           #
# ------------------------------------------------------------------ #

class TestEvaluateNormalizeBlob(unittest.TestCase):
    def test_lowercase(self):
        self.assertEqual("hello", _normalize_blob("HELLO"))

    def test_punctuation_replaced_with_space(self):
        self.assertEqual("a b c", _normalize_blob("a,b.c!"))

    def test_whitespace_collapsed(self):
        self.assertEqual("a b", _normalize_blob("  a   b  "))

    def test_none_returns_empty(self):
        self.assertEqual("", _normalize_blob(None))

    def test_empty_returns_empty(self):
        self.assertEqual("", _normalize_blob(""))


# ------------------------------------------------------------------ #
#  evaluate._gold_supporting_facts                                    #
# ------------------------------------------------------------------ #

class TestEvaluateGoldSupportingFacts(unittest.TestCase):
    def test_normal_extraction(self):
        ref = {"supporting_facts": [["Doc A", 0], ["Doc B", 2]]}
        result = _gold_supporting_facts(ref)
        self.assertEqual({("Doc A", 0), ("Doc B", 2)}, result)

    def test_invalid_items_skipped(self):
        ref = {"supporting_facts": [["Doc", 0], ["bad"], "not_a_list"]}
        result = _gold_supporting_facts(ref)
        self.assertEqual({("Doc", 0)}, result)

    def test_invalid_sentence_id_skipped(self):
        ref = {"supporting_facts": [["Doc", "not_a_number"]]}
        result = _gold_supporting_facts(ref)
        self.assertEqual(set(), result)

    def test_empty(self):
        self.assertEqual(set(), _gold_supporting_facts({}))

    def test_none(self):
        self.assertEqual(set(), _gold_supporting_facts({"supporting_facts": None}))


# ------------------------------------------------------------------ #
#  evaluate._explicit_supporting_fact                                 #
# ------------------------------------------------------------------ #

class TestEvaluateExplicitSupportingFact(unittest.TestCase):
    def test_from_hotpotqa_title_and_sent_id(self):
        item = {"hotpotqa_title": "Doc", "hotpotqa_sent_id": 3}
        self.assertEqual(("Doc", 3), _explicit_supporting_fact(item))

    def test_from_title_and_sent_id(self):
        item = {"title": "Doc", "sent_id": 1}
        self.assertEqual(("Doc", 1), _explicit_supporting_fact(item))

    def test_from_document_title_and_sentence_id(self):
        item = {"document_title": "Doc", "sentence_id": 2}
        self.assertEqual(("Doc", 2), _explicit_supporting_fact(item))

    def test_title_from_text_regex(self):
        item = {"content": "title: My Title\nsent_id: 2\nsome text"}
        result = _explicit_supporting_fact(item)
        self.assertIsNotNone(result)
        self.assertEqual("My Title", result[0])

    def test_sent_id_from_text_regex(self):
        item = {"text": "title: Doc\nsent_id: 5\nbody"}
        result = _explicit_supporting_fact(item)
        self.assertIsNotNone(result)
        self.assertEqual(5, result[1])

    def test_both_from_text_regex(self):
        item = {"text": "hotpotqa_title: Doc\nhotpotqa_sent_id: 7\nbody"}
        result = _explicit_supporting_fact(item)
        self.assertEqual(("Doc", 7), result)

    def test_missing_title_returns_none(self):
        item = {"hotpotqa_sent_id": 3}
        self.assertIsNone(_explicit_supporting_fact(item))

    def test_missing_sent_id_returns_none(self):
        item = {"hotpotqa_title": "Doc"}
        self.assertIsNone(_explicit_supporting_fact(item))

    def test_empty_item_returns_none(self):
        self.assertIsNone(_explicit_supporting_fact({}))

    def test_invalid_sent_id_value_returns_none(self):
        item = {"title": "Doc", "sent_id": "abc"}
        self.assertIsNone(_explicit_supporting_fact(item))

    def test_title_stripped(self):
        item = {"hotpotqa_title": "  Doc  ", "hotpotqa_sent_id": 0}
        self.assertEqual(("Doc", 0), _explicit_supporting_fact(item))


# ------------------------------------------------------------------ #
#  evaluate.predict_supporting_facts                                  #
# ------------------------------------------------------------------ #

class TestEvaluatePredictSupportingFacts(unittest.TestCase):
    def test_explicit_metadata_short_circuits(self):
        items = [
            {"content": "irrelevant", "hotpotqa_title": "A", "hotpotqa_sent_id": 0},
            {"content": "also irrelevant", "hotpotqa_title": "B", "hotpotqa_sent_id": 1},
        ]
        ref = {"context": [["A", ["s0", "s1"]]], "supporting_facts": [["A", 0]]}
        result = predict_supporting_facts(items, ref)
        self.assertEqual({("A", 0), ("B", 1)}, result)

    def test_text_inference_matches_sentence(self):
        items = [{"content": "The Eiffel Tower is in Paris."}]
        ref = {
            "context": [["Eiffel Tower", ["The Eiffel Tower is in Paris.", "Other."]]],
        }
        result = predict_supporting_facts(items, ref)
        self.assertIn(("Eiffel Tower", 0), result)
        self.assertNotIn(("Eiffel Tower", 1), result)

    def test_title_not_in_evidence_skips_document(self):
        items = [{"content": "unrelated text about nothing"}]
        ref = {"context": [["Eiffel Tower", ["The Eiffel Tower is in Paris."]]]}
        result = predict_supporting_facts(items, ref)
        self.assertEqual(set(), result)

    def test_empty_retrieval_items(self):
        ref = {"context": [["Doc", ["text"]]]}
        self.assertEqual(set(), predict_supporting_facts([], ref))

    def test_explicit_none_falls_back_to_inference(self):
        items = [
            {"content": "The Eiffel Tower is in Paris."},
        ]
        ref = {
            "context": [["Eiffel Tower", ["The Eiffel Tower is in Paris."]]],
        }
        result = predict_supporting_facts(items, ref)
        self.assertEqual({("Eiffel Tower", 0)}, result)

    def test_inference_uses_uri_fallback(self):
        items = [{"uri": "The Eiffel Tower is in Paris."}]
        ref = {"context": [["Eiffel Tower", ["The Eiffel Tower is in Paris."]]]}
        result = predict_supporting_facts(items, ref)
        self.assertIn(("Eiffel Tower", 0), result)


# ------------------------------------------------------------------ #
#  evaluate.supporting_fact_metrics                                    #
# ------------------------------------------------------------------ #

class TestEvaluateSupportingFactMetrics(unittest.TestCase):
    def test_perfect_match(self):
        gold = {("A", 0), ("B", 1)}
        m = supporting_fact_metrics(gold, gold)
        self.assertEqual(1.0, m["em"])
        self.assertEqual(1.0, m["f1"])
        self.assertEqual(1.0, m["precision"])
        self.assertEqual(1.0, m["recall"])

    def test_both_empty(self):
        m = supporting_fact_metrics(set(), set())
        self.assertEqual(1.0, m["em"])
        self.assertEqual(1.0, m["f1"])
        self.assertEqual(1.0, m["precision"])
        self.assertEqual(1.0, m["recall"])

    def test_predicted_empty_gold_nonempty(self):
        m = supporting_fact_metrics(set(), {("A", 0)})
        self.assertEqual(0.0, m["em"])
        self.assertEqual(0.0, m["precision"])
        self.assertEqual(0.0, m["recall"])
        self.assertEqual(0.0, m["f1"])

    def test_predicted_nonempty_gold_empty(self):
        m = supporting_fact_metrics({("A", 0)}, set())
        self.assertEqual(0.0, m["em"])
        self.assertEqual(0.0, m["precision"])
        self.assertEqual(0.0, m["recall"])
        self.assertEqual(0.0, m["f1"])

    def test_partial_overlap(self):
        predicted = {("A", 0), ("B", 1), ("C", 2)}
        gold = {("A", 0), ("B", 1)}
        m = supporting_fact_metrics(predicted, gold)
        self.assertEqual(0.0, m["em"])
        self.assertAlmostEqual(2 / 3, m["precision"])
        self.assertEqual(1.0, m["recall"])

    def test_no_overlap(self):
        predicted = {("A", 0)}
        gold = {("B", 1)}
        m = supporting_fact_metrics(predicted, gold)
        self.assertEqual(0.0, m["em"])
        self.assertEqual(0.0, m["precision"])
        self.assertEqual(0.0, m["recall"])
        self.assertEqual(0.0, m["f1"])


# ------------------------------------------------------------------ #
#  evaluate.load_references                                           #
# ------------------------------------------------------------------ #

class TestEvaluateLoadReferences(unittest.TestCase):
    def test_list_data_with_id(self):
        data = [{"_id": "r1", "context": []}, {"_id": "r2", "context": []}]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "refs.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            refs = load_references(path)
        self.assertEqual(2, len(refs))
        self.assertIn("r1", refs)
        self.assertIn("r2", refs)

    def test_id_fallback(self):
        data = [{"id": "x1"}]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "refs.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            refs = load_references(path)
        self.assertIn("x1", refs)

    def test_question_id_fallback(self):
        data = [{"question_id": "q1"}]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "refs.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            refs = load_references(path)
        self.assertIn("q1", refs)

    def test_index_fallback(self):
        data = [{"context": []}]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "refs.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            refs = load_references(path)
        self.assertIn("0", refs)

    def test_dict_data_returns_empty(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "refs.json"
            path.write_text(json.dumps({"data": []}), encoding="utf-8")
            refs = load_references(path)
        self.assertEqual({}, refs)

    def test_non_dict_rows_skipped(self):
        data = ["string", 42, {"_id": "ok"}]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "refs.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            refs = load_references(path)
        self.assertEqual({"ok"}, set(refs.keys()))


# ------------------------------------------------------------------ #
#  evaluate.evaluate_hotpotqa                                         #
# ------------------------------------------------------------------ #

class TestEvaluateEvaluateHotpotqa(unittest.TestCase):
    def test_writes_csv_with_all_fields(self):
        result = _make_qa_result(question_id="q1", response="Paris", answer="Paris")
        refs = {"q1": {"_id": "q1", "context": [], "supporting_facts": []}}
        with tempfile.TemporaryDirectory() as d:
            report = evaluate_hotpotqa([result], refs, Path(d))
            csv_path = Path(d) / "eval_results.csv"
            self.assertTrue(csv_path.exists())
            with csv_path.open(encoding="utf-8") as f:
                reader = csv.DictReader(f)
                self.assertEqual(list(EVAL_FIELDS), reader.fieldnames)
                rows = list(reader)
        self.assertEqual(1, len(report.rows))
        self.assertEqual(1, len(rows))

    def test_perfect_answer_em(self):
        result = _make_qa_result(question_id="q1", response="Paris", answer="Paris")
        refs = {"q1": {"_id": "q1", "context": [], "supporting_facts": []}}
        with tempfile.TemporaryDirectory() as d:
            report = evaluate_hotpotqa([result], refs, Path(d))
        self.assertEqual(1.0, report.answer_em)
        self.assertEqual(1.0, report.answer_f1)

    def test_joint_em_requires_both_answer_and_support_em(self):
        result = _make_qa_result(
            question_id="q1",
            response="Paris",
            answer="London",
            retrieval_items=[{"content": "The tower is in Paris."}],
        )
        refs = {
            "q1": {
                "_id": "q1",
                "context": [["Tower", ["The tower is in Paris."]]],
                "supporting_facts": [["Tower", 0]],
            }
        }
        with tempfile.TemporaryDirectory() as d:
            report = evaluate_hotpotqa([result], refs, Path(d))
        row = report.rows[0]
        # answer em=0 -> joint em must be 0 even if support em=1
        self.assertEqual(0.0, row["joint_em"])
        self.assertEqual(0.0, report.joint_em)

    def test_joint_em_one_when_both_correct(self):
        result = _make_qa_result(
            question_id="q1",
            response="Paris",
            answer="Paris",
            retrieval_items=[{"content": "The tower is in Paris."}],
        )
        refs = {
            "q1": {
                "_id": "q1",
                "context": [["Tower", ["The tower is in Paris."]]],
                "supporting_facts": [["Tower", 0]],
            }
        }
        with tempfile.TemporaryDirectory() as d:
            report = evaluate_hotpotqa([result], refs, Path(d))
        self.assertEqual(1.0, report.joint_em)

    def test_averages_over_multiple_rows(self):
        r1 = _make_qa_result(question_id="q1", response="Paris", answer="Paris")
        r2 = _make_qa_result(question_id="q2", response="London", answer="Paris")
        refs = {"q1": {"_id": "q1"}, "q2": {"_id": "q2"}}
        with tempfile.TemporaryDirectory() as d:
            report = evaluate_hotpotqa([r1, r2], refs, Path(d))
        self.assertEqual(0.5, report.answer_em)

    def test_empty_qa_results(self):
        with tempfile.TemporaryDirectory() as d:
            report = evaluate_hotpotqa([], {}, Path(d))
            csv_path = Path(d) / "eval_results.csv"
            self.assertTrue(csv_path.exists())
        self.assertEqual(0, len(report.rows))
        self.assertEqual(0.0, report.answer_em)
        self.assertEqual(0.0, report.answer_f1)
        self.assertEqual(0.0, report.supporting_facts_em)
        self.assertEqual(0.0, report.joint_em)

    def test_row_values_rounded(self):
        result = _make_qa_result(
            question_id="q1",
            response="paris france",
            answer="paris",
        )
        refs = {"q1": {"_id": "q1", "context": [], "supporting_facts": []}}
        with tempfile.TemporaryDirectory() as d:
            report = evaluate_hotpotqa([result], refs, Path(d))
        row = report.rows[0]
        for key in ("answer_f1", "answer_precision", "answer_recall",
                     "supporting_facts_f1", "supporting_facts_precision",
                     "supporting_facts_recall", "joint_f1"):
            val = row[key]
            self.assertLessEqual(len(str(val).split(".")[-1]), 4,
                                 f"{key} not rounded to 4 decimals")

    def test_reference_missing_uses_empty_dict(self):
        result = _make_qa_result(question_id="missing", response="x", answer="y")
        with tempfile.TemporaryDirectory() as d:
            report = evaluate_hotpotqa([result], {}, Path(d))
        # No crash, empty reference -> support metrics on empty sets
        self.assertEqual(1.0, report.supporting_facts_em)


# ------------------------------------------------------------------ #
#  evaluate.EvaluationReport                                          #
# ------------------------------------------------------------------ #

class TestEvaluationReport(unittest.TestCase):
    def test_construction(self):
        report = EvaluationReport(
            rows=[{"question_id": "q1"}],
            answer_em=0.5,
            answer_f1=0.6,
            supporting_facts_em=0.7,
            supporting_facts_f1=0.8,
            joint_em=0.9,
            joint_f1=1.0,
        )
        self.assertEqual(1, len(report.rows))
        self.assertEqual(0.5, report.answer_em)
        self.assertEqual(0.6, report.answer_f1)
        self.assertEqual(0.7, report.supporting_facts_em)
        self.assertEqual(0.8, report.supporting_facts_f1)
        self.assertEqual(0.9, report.joint_em)
        self.assertEqual(1.0, report.joint_f1)


# ------------------------------------------------------------------ #
#  import_memory._add_events                                          #
# ------------------------------------------------------------------ #

class TestImportAddEvents(unittest.TestCase):
    def test_counts_non_empty_events(self):
        client = _RecordingClient()
        plan = {"events": [{"text": "a"}, {"text": "b"}, {"text": "c"}]}
        count = _add_events(client, "s1", plan)
        self.assertEqual(3, count)
        self.assertEqual(3, len(client.messages))

    def test_skips_empty_text(self):
        client = _RecordingClient()
        plan = {"events": [{"text": "a"}, {"text": ""}, {"text": None}]}
        count = _add_events(client, "s1", plan)
        self.assertEqual(1, count)
        self.assertEqual(1, len(client.messages))

    def test_passes_created_at_from_time(self):
        client = _RecordingClient()
        plan = {"events": [{"text": "hello", "time": "2024-01-01"}]}
        _add_events(client, "s1", plan)
        self.assertEqual("2024-01-01", client.messages[0][3])

    def test_empty_events(self):
        client = _RecordingClient()
        self.assertEqual(0, _add_events(client, "s1", {"events": []}))
        self.assertEqual(0, _add_events(client, "s1", {}))

    def test_role_is_user(self):
        client = _RecordingClient()
        plan = {"events": [{"text": "hello"}]}
        _add_events(client, "s1", plan)
        self.assertEqual("user", client.messages[0][1])


# ------------------------------------------------------------------ #
#  import_memory.import_hotpotqa_memory (per_question)               #
# ------------------------------------------------------------------ #

class TestImportPerQuestionMode(unittest.TestCase):
    def test_per_question_success(self):
        jobs = [SimpleNamespace(question_id="q1"), SimpleNamespace(question_id="q2")]
        plans = [
            {"events": [{"text": "fact1"}]},
            {"events": [{"text": "fact2"}, {"text": "fact3"}]},
        ]
        client = _RecordingClient()
        with tempfile.TemporaryDirectory() as d:
            report = import_hotpotqa_memory(
                jobs, plans, client, EvalConfig(),
                Path(d), _Log(), import_mode="per_question",
            )
        self.assertEqual(2, report.completed)
        self.assertEqual(2, report.total)
        self.assertEqual(0, report.incomplete)
        self.assertEqual(2, len(client.opened))
        self.assertIn("q1", report.question_to_session)
        self.assertIn("q2", report.question_to_session)

    def test_per_question_error_handling(self):
        jobs = [SimpleNamespace(question_id="q1")]
        plans = [{"events": [{"text": "fact1"}]}]
        client = _RecordingClient()
        client.open_session = MagicMock(side_effect=RuntimeError("boom"))
        with tempfile.TemporaryDirectory() as d:
            report = import_hotpotqa_memory(
                jobs, plans, client, EvalConfig(),
                Path(d), _Log(), import_mode="per_question",
            )
        self.assertEqual(0, report.completed)
        self.assertEqual(1, report.total)
        self.assertEqual(1, report.incomplete)
        self.assertEqual("error", report.rows[0]["status"])
        self.assertIn("boom", report.rows[0]["error"])

    def test_per_question_writes_csv(self):
        jobs = [SimpleNamespace(question_id="q1")]
        plans = [{"events": [{"text": "x"}]}]
        client = _RecordingClient()
        with tempfile.TemporaryDirectory() as d:
            import_hotpotqa_memory(
                jobs, plans, client, EvalConfig(),
                Path(d), _Log(), import_mode="per_question",
            )
            csv_path = Path(d) / "import_results.csv"
            self.assertTrue(csv_path.exists())
            with csv_path.open(encoding="utf-8") as f:
                reader = csv.DictReader(f)
                self.assertEqual(list(IMPORT_FIELDS), reader.fieldnames)
                rows = list(reader)
        self.assertEqual(1, len(rows))
        self.assertEqual("q1", rows[0]["question_id"])

    def test_per_question_session_title(self):
        jobs = [SimpleNamespace(question_id="qX")]
        plans = [{"events": [{"text": "x"}]}]
        client = _RecordingClient()
        with tempfile.TemporaryDirectory() as d:
            import_hotpotqa_memory(
                jobs, plans, client, EvalConfig(),
                Path(d), _Log(), import_mode="per_question",
            )
        self.assertEqual("hotpotqa_qX", client.opened[0])


# ------------------------------------------------------------------ #
#  import_memory.import_hotpotqa_memory (global)                     #
# ------------------------------------------------------------------ #

class TestImportGlobalMode(unittest.TestCase):
    def test_global_error_handling(self):
        jobs = [SimpleNamespace(question_id="q1")]
        plans = [{"events": [{"text": "x"}]}]
        client = _RecordingClient()
        client.open_session = MagicMock(side_effect=RuntimeError("fail"))
        with tempfile.TemporaryDirectory() as d:
            report = import_hotpotqa_memory(
                jobs, plans, client, EvalConfig(),
                Path(d), _Log(), import_mode="global",
            )
        self.assertEqual(0, report.completed)
        self.assertEqual(1, report.total)
        self.assertEqual(1, report.incomplete)
        self.assertEqual("error", report.rows[0]["status"])
        self.assertEqual("global", report.rows[0]["question_id"])
        self.assertEqual({}, report.question_to_session)

    def test_global_writes_csv(self):
        jobs = [SimpleNamespace(question_id="q1")]
        plans = [{"events": [{"text": "x"}]}]
        client = _RecordingClient()
        with tempfile.TemporaryDirectory() as d:
            import_hotpotqa_memory(
                jobs, plans, client, EvalConfig(),
                Path(d), _Log(), import_mode="global",
            )
            csv_path = Path(d) / "import_results.csv"
            self.assertTrue(csv_path.exists())
            with csv_path.open(encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
        self.assertEqual(1, len(rows))
        self.assertEqual("global", rows[0]["question_id"])

    def test_global_commits_once(self):
        jobs = [SimpleNamespace(question_id="q1"), SimpleNamespace(question_id="q2")]
        plans = [{"events": [{"text": "a"}]}, {"events": [{"text": "b"}]}]
        client = _RecordingClient()
        with tempfile.TemporaryDirectory() as d:
            import_hotpotqa_memory(
                jobs, plans, client, EvalConfig(),
                Path(d), _Log(), import_mode="global",
            )
        self.assertEqual(1, len(client.commits))
        self.assertEqual(1, len(client.opened))


# ------------------------------------------------------------------ #
#  import_memory.ImportReport                                         #
# ------------------------------------------------------------------ #

class TestImportReport(unittest.TestCase):
    def test_construction(self):
        report = ImportReport(
            rows=[{"question_id": "q1", "status": "completed"}],
            question_to_session={"q1": "s1"},
            completed=1,
            total=1,
            incomplete=0,
        )
        self.assertEqual(1, len(report.rows))
        self.assertEqual({"q1": "s1"}, report.question_to_session)
        self.assertEqual(1, report.completed)
        self.assertEqual(1, report.total)
        self.assertEqual(0, report.incomplete)


# ------------------------------------------------------------------ #
#  qa.build_qa_tasks                                                 #
# ------------------------------------------------------------------ #

class TestQABuildTasks(unittest.TestCase):
    def test_correct_field_mapping(self):
        config = EvalConfig(top_k=5, memory_budget_chars=4000)
        jobs = [
            SimpleNamespace(
                question_id="q1",
                sample_id="s1",
                category="bridge",
                question="What?",
                answer="42",
                query_time="2024-01-01",
            ),
        ]
        tasks = build_qa_tasks(jobs, {"q1": "sess1"}, config, agent_id="agent_x")
        self.assertEqual(1, len(tasks))
        task = tasks[0]
        self.assertEqual("q1", task["question_id"])
        self.assertEqual("s1", task["sample_id"])
        self.assertEqual("bridge", task["category"])
        self.assertEqual("What?", task["question"])
        self.assertEqual("42", task["answer"])
        self.assertEqual(5, task["top_k"])
        self.assertEqual(4000, task["memory_budget_chars"])
        self.assertEqual("sess1", task["session_id"])
        self.assertEqual("agent_x", task["agent_id"])
        self.assertEqual("2024-01-01", task["question_time"])

    def test_empty_jobs(self):
        self.assertEqual([], build_qa_tasks([], {}, EvalConfig()))

    def test_missing_session_maps_to_empty(self):
        jobs = [SimpleNamespace(
            question_id="q1", sample_id="s1", category="c",
            question="q", answer="a", query_time="",
        )]
        tasks = build_qa_tasks(jobs, {}, EvalConfig(), agent_id="ag")
        self.assertEqual("", tasks[0]["session_id"])

    def test_multiple_jobs(self):
        jobs = [
            SimpleNamespace(
                question_id=f"q{i}", sample_id=f"s{i}", category="c",
                question=f"Q{i}", answer=f"A{i}", query_time="",
            )
            for i in range(3)
        ]
        tasks = build_qa_tasks(jobs, {}, EvalConfig())
        self.assertEqual(3, len(tasks))
        self.assertEqual(["q0", "q1", "q2"], [t["question_id"] for t in tasks])


# ------------------------------------------------------------------ #
#  qa.run_hotpotqa_qa                                                #
# ------------------------------------------------------------------ #

class TestQARunHotpotqaQA(unittest.TestCase):
    def test_writes_csv_with_retrieval_items_json(self):
        tasks = [{"question_id": "q1", "question": "Q", "answer": "A"}]
        results = [_make_qa_result(
            question_id="q1",
            retrieval_items=[{"content": "evidence", "score": 0.9}],
        )]
        agent_plugin = MagicMock()
        with patch("benchmarks.hotpotqa.qa.run_concurrent_qa", return_value=results):
            with tempfile.TemporaryDirectory() as d:
                returned = run_hotpotqa_qa(
                    tasks, agent_plugin, EvalConfig(),
                    Path(d), _Log(),
                )
                csv_path = Path(d) / "qa_results.csv"
                self.assertTrue(csv_path.exists())
                with csv_path.open(encoding="utf-8") as f:
                    reader = csv.DictReader(f)
                    self.assertEqual(
                        list(QA_FIELDS), reader.fieldnames,
                    )
                    rows = list(reader)
        self.assertEqual(results, returned)
        self.assertEqual(1, len(rows))
        retrieved = json.loads(rows[0]["retrieval_items_json"])
        self.assertEqual(1, len(retrieved))
        self.assertEqual("evidence", retrieved[0]["content"])

    def test_empty_tasks_returns_empty(self):
        agent_plugin = MagicMock()
        with patch("benchmarks.hotpotqa.qa.run_concurrent_qa", return_value=[]):
            with tempfile.TemporaryDirectory() as d:
                results = run_hotpotqa_qa(
                    [], agent_plugin, EvalConfig(),
                    Path(d), _Log(),
                )
                csv_path = Path(d) / "qa_results.csv"
                self.assertTrue(csv_path.exists())
        self.assertEqual([], results)

    def test_passes_config_to_run_concurrent_qa(self):
        config = EvalConfig(concurrency=8, question_timeout_s=60.0)
        agent_plugin = MagicMock()
        with patch("benchmarks.hotpotqa.qa.run_concurrent_qa", return_value=[]) as mock_run:
            with tempfile.TemporaryDirectory() as d:
                run_hotpotqa_qa(
                    [{"question_id": "q1"}], agent_plugin, config,
                    Path(d), _Log(),
                )
        _, kwargs = mock_run.call_args
        self.assertEqual(8, kwargs["concurrency"])
        self.assertEqual(60.0, kwargs["question_timeout_s"])

    def test_exception_propagates(self):
        agent_plugin = MagicMock()
        with patch("benchmarks.hotpotqa.qa.run_concurrent_qa",
                    side_effect=RuntimeError("crash")):
            with tempfile.TemporaryDirectory() as d:
                with self.assertRaises(RuntimeError):
                    run_hotpotqa_qa(
                        [{"question_id": "q1"}], agent_plugin, EvalConfig(),
                        Path(d), _Log(),
                    )


# ------------------------------------------------------------------ #
#  selection.parse_question_ids                                       #
# ------------------------------------------------------------------ #

class TestSelectionParseQuestionIds(unittest.TestCase):
    def test_normal(self):
        self.assertEqual(["a", "b", "c"], parse_question_ids("a,b,c"))

    def test_dedup_preserves_order(self):
        self.assertEqual(["a", "b"], parse_question_ids("a,b,a,b"))

    def test_empty_string(self):
        self.assertEqual([], parse_question_ids(""))

    def test_none(self):
        self.assertEqual([], parse_question_ids(None))

    def test_whitespace_stripped(self):
        self.assertEqual(["a", "b"], parse_question_ids("  a ,  b  "))

    def test_empty_parts_filtered(self):
        self.assertEqual(["a", "b"], parse_question_ids("a,,b,"))

    def test_single_id(self):
        self.assertEqual(["only"], parse_question_ids("only"))


# ------------------------------------------------------------------ #
#  selection.select_jobs_and_plans                                    #
# ------------------------------------------------------------------ #

class TestSelectionSelectJobsAndPlans(unittest.TestCase):
    def _make_jobs(self, n=3):
        return [
            SimpleNamespace(
                question_id=f"q{i}",
                native_question_id=f"n{i}",
                sample_id=f"s{i}",
            )
            for i in range(n)
        ]

    def _make_plans(self, n=3):
        return [{"idx": i} for i in range(n)]

    def test_no_filter_returns_all(self):
        jobs = self._make_jobs()
        plans = self._make_plans()
        sel_jobs, sel_plans = select_jobs_and_plans(jobs, plans)
        self.assertEqual(jobs, sel_jobs)
        self.assertEqual(plans, sel_plans)

    def test_limit(self):
        jobs = self._make_jobs()
        plans = self._make_plans()
        sel_jobs, sel_plans = select_jobs_and_plans(jobs, plans, limit=2)
        self.assertEqual(2, len(sel_jobs))
        self.assertEqual(2, len(sel_plans))
        self.assertEqual(["q0", "q1"], [j.question_id for j in sel_jobs])

    def test_limit_zero_means_all(self):
        jobs = self._make_jobs()
        plans = self._make_plans()
        sel_jobs, _ = select_jobs_and_plans(jobs, plans, limit=0)
        self.assertEqual(3, len(sel_jobs))

    def test_question_ids_by_question_id(self):
        jobs = self._make_jobs()
        plans = self._make_plans()
        sel_jobs, _ = select_jobs_and_plans(jobs, plans, question_ids=["q0", "q2"])
        self.assertEqual(["q0", "q2"], [j.question_id for j in sel_jobs])

    def test_question_ids_by_sample_id(self):
        jobs = self._make_jobs()
        plans = self._make_plans()
        sel_jobs, _ = select_jobs_and_plans(jobs, plans, question_ids=["s1"])
        self.assertEqual(["q1"], [j.question_id for j in sel_jobs])

    def test_question_ids_by_native_question_id(self):
        jobs = self._make_jobs()
        plans = self._make_plans()
        sel_jobs, _ = select_jobs_and_plans(jobs, plans, question_ids=["n2"])
        self.assertEqual(["q2"], [j.question_id for j in sel_jobs])

    def test_missing_ids_raise_value_error(self):
        jobs = self._make_jobs()
        plans = self._make_plans()
        with self.assertRaises(ValueError) as ctx:
            select_jobs_and_plans(jobs, plans, question_ids=["q0", "unknown"])
        self.assertIn("unknown", str(ctx.exception))

    def test_limit_applied_after_question_ids(self):
        jobs = self._make_jobs(5)
        plans = self._make_plans(5)
        sel_jobs, _ = select_jobs_and_plans(
            jobs, plans, question_ids=["q0", "q1", "q2", "q3"], limit=2,
        )
        self.assertEqual(2, len(sel_jobs))

    def test_empty_inputs(self):
        sel_jobs, sel_plans = select_jobs_and_plans([], [])
        self.assertEqual([], sel_jobs)
        self.assertEqual([], sel_plans)

    def test_question_ids_empty_list_returns_all(self):
        jobs = self._make_jobs()
        plans = self._make_plans()
        sel_jobs, _ = select_jobs_and_plans(jobs, plans, question_ids=[])
        self.assertEqual(3, len(sel_jobs))


# ------------------------------------------------------------------ #
#  reporting.build_summary                                            #
# ------------------------------------------------------------------ #

class TestReportingBuildSummary(unittest.TestCase):
    def _make_import_report(self, completed=1, total=1, incomplete=0):
        return ImportReport(
            rows=[{"question_id": "q1", "status": "completed"}],
            question_to_session={"q1": "s1"},
            completed=completed,
            total=total,
            incomplete=incomplete,
        )

    def _make_eval_report(self, answer_em=1.0, answer_f1=1.0,
                          sup_em=1.0, sup_f1=1.0, joint_em=1.0, joint_f1=1.0):
        return EvaluationReport(
            rows=[],
            answer_em=answer_em,
            answer_f1=answer_f1,
            supporting_facts_em=sup_em,
            supporting_facts_f1=sup_f1,
            joint_em=joint_em,
            joint_f1=joint_f1,
        )

    def test_status_completed(self):
        jobs = [SimpleNamespace(question_id="q1")]
        qa_results = [_make_qa_result()]
        summary = build_summary(
            dataset_path="/data.json",
            import_mode="per_question",
            jobs=jobs,
            import_report=self._make_import_report(),
            qa_results=qa_results,
            evaluation_report=self._make_eval_report(),
            evaluation_identity={"mode": "fresh"},
        )
        self.assertEqual("completed", summary["status"])

    def test_status_failed_on_incomplete_imports(self):
        summary = build_summary(
            dataset_path="/d",
            import_mode="per_question",
            jobs=[SimpleNamespace(question_id="q1")],
            import_report=self._make_import_report(completed=0, incomplete=1),
            qa_results=[_make_qa_result()],
            evaluation_report=self._make_eval_report(),
            evaluation_identity={},
        )
        self.assertEqual("failed", summary["status"])

    def test_status_failed_on_qa_errors(self):
        summary = build_summary(
            dataset_path="/d",
            import_mode="per_question",
            jobs=[SimpleNamespace(question_id="q1")],
            import_report=self._make_import_report(),
            qa_results=[_make_qa_result(llm_error="timeout")],
            evaluation_report=self._make_eval_report(),
            evaluation_identity={},
        )
        self.assertEqual("failed", summary["status"])
        self.assertEqual(1, summary["qa_errors"])

    def test_status_failed_on_retrieval_errors(self):
        summary = build_summary(
            dataset_path="/d",
            import_mode="per_question",
            jobs=[SimpleNamespace(question_id="q1")],
            import_report=self._make_import_report(),
            qa_results=[_make_qa_result(retrieval_error="empty")],
            evaluation_report=self._make_eval_report(),
            evaluation_identity={},
        )
        self.assertEqual("failed", summary["status"])
        self.assertEqual(1, summary["retrieval_errors"])

    def test_token_sums(self):
        qa_results = [
            _make_qa_result(prompt_tokens=10, completion_tokens=5),
            _make_qa_result(prompt_tokens=20, completion_tokens=15),
        ]
        summary = build_summary(
            dataset_path="/d",
            import_mode="per_question",
            jobs=[SimpleNamespace(question_id="q1")] * 2,
            import_report=self._make_import_report(),
            qa_results=qa_results,
            evaluation_report=self._make_eval_report(),
            evaluation_identity={},
        )
        self.assertEqual(30, summary["total_prompt_tokens"])
        self.assertEqual(20, summary["total_completion_tokens"])

    def test_avg_qa_elapsed(self):
        qa_results = [
            _make_qa_result(elapsed_s=2.0),
            _make_qa_result(elapsed_s=4.0),
        ]
        summary = build_summary(
            dataset_path="/d",
            import_mode="global",
            jobs=[SimpleNamespace(question_id="q1")] * 2,
            import_report=self._make_import_report(),
            qa_results=qa_results,
            evaluation_report=self._make_eval_report(),
            evaluation_identity={},
        )
        self.assertEqual(3.0, summary["avg_qa_elapsed_s"])

    def test_avg_qa_elapsed_empty(self):
        summary = build_summary(
            dataset_path="/d",
            import_mode="per_question",
            jobs=[],
            import_report=self._make_import_report(),
            qa_results=[],
            evaluation_report=self._make_eval_report(),
            evaluation_identity={},
        )
        self.assertEqual(0.0, summary["avg_qa_elapsed_s"])

    def test_all_metric_fields_present(self):
        summary = build_summary(
            dataset_path="/d",
            import_mode="per_question",
            jobs=[SimpleNamespace(question_id="q1")],
            import_report=self._make_import_report(),
            qa_results=[_make_qa_result()],
            evaluation_report=self._make_eval_report(
                answer_em=0.5, answer_f1=0.6,
                sup_em=0.7, sup_f1=0.8,
                joint_em=0.9, joint_f1=1.0,
            ),
            evaluation_identity={"mode": "fresh", "tenant_id": "t", "user_id": "u"},
        )
        for key in ("avg_f1", "avg_em", "answer_f1", "answer_em",
                     "supporting_facts_f1", "supporting_facts_em",
                     "joint_f1", "joint_em"):
            self.assertIn(key, summary)
        self.assertEqual(0.5, summary["answer_em"])
        self.assertEqual(0.6, summary["answer_f1"])
        self.assertEqual(0.7, summary["supporting_facts_em"])
        self.assertEqual(0.8, summary["supporting_facts_f1"])
        self.assertEqual(0.9, summary["joint_em"])
        self.assertEqual(1.0, summary["joint_f1"])

    def test_benchmark_and_memory_source(self):
        summary = build_summary(
            dataset_path="/d",
            import_mode="per_question",
            jobs=[],
            import_report=self._make_import_report(),
            qa_results=[],
            evaluation_report=self._make_eval_report(),
            evaluation_identity={},
        )
        self.assertEqual("hotpotqa", summary["benchmark"])
        self.assertEqual("injected", summary["memory_source"])

    def test_total_questions_matches_jobs(self):
        jobs = [SimpleNamespace(question_id=f"q{i}") for i in range(5)]
        summary = build_summary(
            dataset_path="/d",
            import_mode="per_question",
            jobs=jobs,
            import_report=self._make_import_report(),
            qa_results=[],
            evaluation_report=self._make_eval_report(),
            evaluation_identity={},
        )
        self.assertEqual(5, summary["total_questions"])

    def test_memory_identity_passed_through(self):
        identity = {"mode": "fresh", "tenant_id": "t1", "user_id": "u1"}
        summary = build_summary(
            dataset_path="/d",
            import_mode="per_question",
            jobs=[],
            import_report=self._make_import_report(),
            qa_results=[],
            evaluation_report=self._make_eval_report(),
            evaluation_identity=identity,
        )
        self.assertEqual(identity, summary["memory_identity"])


# ------------------------------------------------------------------ #
#  recovery.build_parser                                              #
# ------------------------------------------------------------------ #

class TestRecoveryBuildParser(unittest.TestCase):
    def test_required_args(self):
        parser = __import__(
            "benchmarks.hotpotqa.recovery", fromlist=["build_parser"],
        ).build_parser()
        args = parser.parse_args([
            "--qa", "/path/qa.csv",
            "--dataset", "/path/data.json",
            "--out-dir", "/out",
        ])
        self.assertEqual("/path/qa.csv", args.qa)
        self.assertEqual("/path/data.json", args.dataset)
        self.assertEqual("/out", args.out_dir)
        self.assertEqual("all", args.sample)
        self.assertEqual("failed-or-missing", args.mode)
        self.assertEqual("", args.retry_qa)
        self.assertEqual("", args.output)

    def test_mode_choices(self):
        from benchmarks.hotpotqa.recovery import build_parser
        for mode in ("failed", "missing", "failed-or-missing"):
            with self.subTest(mode=mode):
                args = build_parser().parse_args([
                    "--qa", "q", "--dataset", "d", "--out-dir", "o",
                    "--mode", mode,
                ])
                self.assertEqual(mode, args.mode)

    def test_invalid_mode_rejected(self):
        from benchmarks.hotpotqa.recovery import build_parser
        with self.assertRaises(SystemExit):
            build_parser().parse_args([
                "--qa", "q", "--dataset", "d", "--out-dir", "o",
                "--mode", "bogus",
            ])

    def test_retry_qa_and_output(self):
        from benchmarks.hotpotqa.recovery import build_parser
        args = build_parser().parse_args([
            "--qa", "q", "--dataset", "d", "--out-dir", "o",
            "--retry-qa", "retry.csv", "--output", "merged.csv",
        ])
        self.assertEqual("retry.csv", args.retry_qa)
        self.assertEqual("merged.csv", args.output)


# ------------------------------------------------------------------ #
#  recovery.main                                                      #
# ------------------------------------------------------------------ #

class TestRecoveryMain(unittest.TestCase):
    def test_main_writes_summary_json(self):
        from benchmarks.hotpotqa.recovery import main

        qa_rows = [
            {"question_id": "q1", "response": "answer", "llm_error": ""},
            {"question_id": "q2", "response": "", "llm_error": "timeout"},
        ]
        dataset_items = [
            {"_id": "q1", "question": "Q1", "answer": "A1"},
            {"_id": "q2", "question": "Q2", "answer": "A2"},
            {"_id": "q3", "question": "Q3", "answer": "A3"},
        ]
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            qa_path = d / "qa_results.csv"
            with qa_path.open("w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=["question_id", "response", "llm_error"])
                writer.writeheader()
                writer.writerows(qa_rows)
            dataset_path = d / "data.json"
            _write_hotpotqa_json(dataset_path, dataset_items)
            out_dir = d / "recovery"

            argv = [
                "recovery.py",
                "--qa", str(qa_path),
                "--dataset", str(dataset_path),
                "--out-dir", str(out_dir),
                "--mode", "failed-or-missing",
            ]
            with patch.object(sys, "argv", argv):
                main()

            summary_path = out_dir / "recovery_summary.json"
            self.assertTrue(summary_path.exists())
            summary = json.loads(summary_path.read_text(encoding="utf-8"))

        self.assertEqual("failed-or-missing", summary["mode"])
        self.assertEqual(3, summary["expected_questions"])
        self.assertEqual(2, summary["observed_questions"])
        self.assertEqual(["q2", "q3"], summary["recovery_question_ids"])
        self.assertEqual(2, summary["recovery_count"])

    def test_main_with_retry_qa_merges(self):
        from benchmarks.hotpotqa.recovery import main

        qa_rows = [
            {"question_id": "q1", "response": "ok", "llm_error": ""},
            {"question_id": "q2", "response": "", "llm_error": "timeout"},
        ]
        retry_rows = [
            {"question_id": "q2", "response": "recovered", "llm_error": ""},
        ]
        dataset_items = [
            {"_id": "q1", "question": "Q1", "answer": "A1"},
            {"_id": "q2", "question": "Q2", "answer": "A2"},
        ]
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            qa_path = d / "qa.csv"
            _write_csv(qa_path, qa_rows)
            retry_path = d / "retry.csv"
            _write_csv(retry_path, retry_rows)
            dataset_path = d / "data.json"
            _write_hotpotqa_json(dataset_path, dataset_items)
            out_dir = d / "out"

            argv = [
                "recovery.py",
                "--qa", str(qa_path),
                "--dataset", str(dataset_path),
                "--out-dir", str(out_dir),
                "--retry-qa", str(retry_path),
            ]
            with patch.object(sys, "argv", argv):
                main()

            recovered_path = out_dir / "qa_results.recovered.csv"
            self.assertTrue(recovered_path.exists())
            with recovered_path.open(encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            merged_q2 = [r for r in rows if r["question_id"] == "q2"][0]
            self.assertEqual("recovered", merged_q2["response"])

            summary = json.loads(
                (out_dir / "recovery_summary.json").read_text(encoding="utf-8")
            )
        self.assertEqual(1, summary["merge"]["recovered"])

    def test_main_custom_output_path(self):
        from benchmarks.hotpotqa.recovery import main

        qa_rows = [{"question_id": "q1", "response": "", "llm_error": "err"}]
        retry_rows = [{"question_id": "q1", "response": "fixed", "llm_error": ""}]
        dataset_items = [{"_id": "q1", "question": "Q", "answer": "A"}]
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            qa_path = d / "qa.csv"
            _write_csv(qa_path, qa_rows)
            retry_path = d / "retry.csv"
            _write_csv(retry_path, retry_rows)
            dataset_path = d / "data.json"
            _write_hotpotqa_json(dataset_path, dataset_items)
            out_dir = d / "out"
            custom_output = d / "custom_merged.csv"

            argv = [
                "recovery.py",
                "--qa", str(qa_path),
                "--dataset", str(dataset_path),
                "--out-dir", str(out_dir),
                "--retry-qa", str(retry_path),
                "--output", str(custom_output),
            ]
            with patch.object(sys, "argv", argv):
                main()
            self.assertTrue(custom_output.exists())


def _write_csv(path: Path, rows: list[dict]) -> None:
    fieldnames = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


# ------------------------------------------------------------------ #
#  run_eval.build_parser                                              #
# ------------------------------------------------------------------ #

class TestRunEvalBuildParser(unittest.TestCase):
    def test_defaults(self):
        from benchmarks.hotpotqa.run_eval import build_parser
        # --agent-plugin bare_llm keeps the parser simple for testing
        argv = ["run_eval.py", "--agent-plugin", "bare_llm",
                "--llm-api-key", "k", "--llm-base-url", "http://x",
                "--llm-model", "m"]
        with patch.object(sys, "argv", argv):
            parser = build_parser()
            args = parser.parse_args(argv[1:])
        self.assertEqual("per_question", args.import_mode)
        self.assertEqual("all", args.sample)
        self.assertEqual(0, args.questions)
        self.assertEqual("", args.question_ids)
        self.assertEqual(4, args.concurrency)
        self.assertEqual("results", args.out_dir)
        self.assertFalse(args.check)

    def test_import_mode_choices(self):
        from benchmarks.hotpotqa.run_eval import build_parser
        for mode in ("per_question", "global"):
            with self.subTest(mode=mode):
                argv = ["run_eval.py", "--agent-plugin", "bare_llm",
                        "--llm-api-key", "k", "--llm-base-url", "http://x",
                        "--llm-model", "m", "--import-mode", mode]
                with patch.object(sys, "argv", argv):
                    args = build_parser().parse_args(argv[1:])
                self.assertEqual(mode, args.import_mode)

    def test_check_flag(self):
        from benchmarks.hotpotqa.run_eval import build_parser
        argv = ["run_eval.py", "--agent-plugin", "bare_llm",
                "--llm-api-key", "k", "--llm-base-url", "http://x",
                "--llm-model", "m", "--check"]
        with patch.object(sys, "argv", argv):
            args = build_parser().parse_args(argv[1:])
        self.assertTrue(args.check)

    def test_question_ids_arg(self):
        from benchmarks.hotpotqa.run_eval import build_parser
        argv = ["run_eval.py", "--agent-plugin", "bare_llm",
                "--llm-api-key", "k", "--llm-base-url", "http://x",
                "--llm-model", "m", "--question-ids", "a,b,c"]
        with patch.object(sys, "argv", argv):
            args = build_parser().parse_args(argv[1:])
        self.assertEqual("a,b,c", args.question_ids)


# ------------------------------------------------------------------ #
#  run_eval.main (--check mode)                                      #
# ------------------------------------------------------------------ #

class TestRunEvalMainCheck(unittest.TestCase):
    def test_check_success(self):
        from benchmarks.hotpotqa import run_eval as mod

        fake_job = SimpleNamespace(question_id="q1")
        fake_plan = {"sample_id": "q1"}
        argv = [
            "run_eval.py", "--agent-plugin", "bare_llm",
            "--llm-api-key", "k", "--llm-base-url", "http://x",
            "--llm-model", "m", "--check",
            "--dataset", "/fake/path.json",
        ]
        with patch.object(sys, "argv", argv), \
             patch.object(mod, "validate_eval_config") as mock_val, \
             patch.object(mod, "resolve_dataset_path", return_value="/fake/path.json"), \
             patch.object(mod, "load_dataset", return_value=([fake_job], [fake_plan])), \
             patch.object(mod, "select_jobs_and_plans", return_value=([fake_job], [fake_plan])), \
             patch("builtins.print") as mock_print:
            mod.main()
        mock_val.assert_called_once()
        printed = " ".join(str(c) for c in mock_print.call_args[0])
        self.assertIn("[check] OK", printed)
        self.assertIn("hotpotqa", printed)

    def test_check_no_questions_raises(self):
        from benchmarks.hotpotqa import run_eval as mod

        argv = [
            "run_eval.py", "--agent-plugin", "bare_llm",
            "--llm-api-key", "k", "--llm-base-url", "http://x",
            "--llm-model", "m", "--check",
            "--dataset", "/fake/path.json",
        ]
        with patch.object(sys, "argv", argv), \
             patch.object(mod, "validate_eval_config"), \
             patch.object(mod, "resolve_dataset_path", return_value="/fake/path.json"), \
             patch.object(mod, "load_dataset", return_value=([], [])), \
             patch.object(mod, "select_jobs_and_plans", return_value=([], [])):
            with self.assertRaises(ValueError) as ctx:
                mod.main()
        self.assertIn("no HotpotQA questions", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
