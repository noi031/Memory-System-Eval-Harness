"""Unit tests for plugins.base and plugins.registry.

Covers every functional point in the base plugin interface and the
dynamic-loading registry: the AgentDescriptor / TypingResult /
AgentResponse dataclasses, the AgentPlugin ABC default-method behavior,
and get_plugin_class / load_agent_plugin. No real services are started;
LLMClient is mocked where a real plugin is loaded end-to-end.

Run: python -m unittest tests.test_plugins_base -v
"""

from __future__ import annotations

import argparse
import types
import unittest
from unittest.mock import MagicMock, patch

from plugins.base import (
    AgentDescriptor,
    AgentPlugin,
    AgentResponse,
    TypingResult,
)
from plugins.bare_llm.plugin import BareLLMPlugin
from plugins.registry import get_plugin_class, load_agent_plugin


# ------------------------------------------------------------------ #
#  Test helpers                                                      #
# ------------------------------------------------------------------ #

class _MinimalPlugin(AgentPlugin):
    """Concrete subclass implementing only setup(); keeps all base defaults.

    Used to exercise the default method behavior of AgentPlugin without
    pulling in any real plugin's overrides.
    """

    descriptor = AgentDescriptor(
        id="minimal",
        name="Minimal",
        description="Test-only plugin exercising default methods.",
    )

    def setup(self, config: dict) -> None:
        self.config = config

    def getlog(self) -> str:
        return "{}"


# ------------------------------------------------------------------ #
#  AgentDescriptor                                                   #
# ------------------------------------------------------------------ #

class AgentDescriptorTests(unittest.TestCase):
    """Verify the frozen metadata dataclass."""

    def test_is_frozen_dataclass(self):
        self.assertTrue(AgentDescriptor.__dataclass_params__.frozen)

    def test_required_fields_id_name_description(self):
        d = AgentDescriptor(id="x", name="Y", description="z")
        self.assertEqual("x", d.id)
        self.assertEqual("Y", d.name)
        self.assertEqual("z", d.description)

    def test_capabilities_defaults_to_empty_tuple(self):
        d = AgentDescriptor(id="x", name="y", description="z")
        self.assertEqual((), d.capabilities)

    def test_capabilities_accepts_tuple(self):
        caps = ("memory", "typing")
        d = AgentDescriptor(
            id="x", name="y", description="z", capabilities=caps,
        )
        self.assertEqual(caps, d.capabilities)

    def test_frozen_raises_on_attribute_assignment(self):
        d = AgentDescriptor(id="x", name="y", description="z")
        with self.assertRaises(AttributeError):
            d.id = "other"  # type: ignore[misc]

    def test_frozen_raises_on_attribute_deletion(self):
        d = AgentDescriptor(id="x", name="y", description="z")
        with self.assertRaises(AttributeError):
            del d.name

    def test_equality_same_fields(self):
        a = AgentDescriptor(
            id="x", name="y", description="z", capabilities=("a",),
        )
        b = AgentDescriptor(
            id="x", name="y", description="z", capabilities=("a",),
        )
        self.assertEqual(a, b)

    def test_inequality_different_id(self):
        a = AgentDescriptor(id="x", name="y", description="z")
        b = AgentDescriptor(id="other", name="y", description="z")
        self.assertNotEqual(a, b)

    def test_inequality_different_capabilities(self):
        a = AgentDescriptor(
            id="x", name="y", description="z", capabilities=("a",),
        )
        b = AgentDescriptor(
            id="x", name="y", description="z", capabilities=("b",),
        )
        self.assertNotEqual(a, b)

    def test_repr_contains_class_name_and_fields(self):
        d = AgentDescriptor(id="x", name="y", description="z")
        rep = repr(d)
        self.assertIn("AgentDescriptor", rep)
        self.assertIn("id='x'", rep)
        self.assertIn("name='y'", rep)


# ------------------------------------------------------------------ #
#  TypingResult                                                      #
# ------------------------------------------------------------------ #

class TypingResultTests(unittest.TestCase):
    """Verify the typing-simulation result dataclass."""

    def test_default_committed_is_false(self):
        self.assertFalse(TypingResult().committed)

    def test_default_memory_items_is_empty_list(self):
        self.assertEqual([], TypingResult().memory_items)

    def test_custom_values(self):
        items = [{"text": "m1"}]
        r = TypingResult(committed=True, memory_items=items)
        self.assertTrue(r.committed)
        self.assertEqual(items, r.memory_items)

    def test_memory_items_independent_per_instance(self):
        a = TypingResult()
        b = TypingResult()
        a.memory_items.append({"x": 1})
        self.assertEqual([], b.memory_items)

    def test_is_mutable(self):
        r = TypingResult()
        r.committed = True
        self.assertTrue(r.committed)

    def test_repr_contains_fields(self):
        r = TypingResult(committed=True)
        rep = repr(r)
        self.assertIn("TypingResult", rep)
        self.assertIn("committed=True", rep)


# ------------------------------------------------------------------ #
#  AgentResponse                                                     #
# ------------------------------------------------------------------ #

class AgentResponseTests(unittest.TestCase):
    """Verify the standardized agent response dataclass."""

    def test_defaults(self):
        r = AgentResponse()
        self.assertEqual("", r.text)
        self.assertIsNone(r.ttft_ms)
        self.assertEqual(0, r.prompt_tokens)
        self.assertEqual(0, r.completion_tokens)
        self.assertEqual(0, r.cached_tokens)
        self.assertFalse(r.prefetch_committed)
        self.assertEqual([], r.memory_items)
        self.assertIsNone(r.error)
        self.assertEqual({}, r.extra)

    def test_all_fields_set(self):
        items = [{"text": "m"}]
        extra = {"k": "v"}
        r = AgentResponse(
            text="hello",
            ttft_ms=42.5,
            prompt_tokens=10,
            completion_tokens=5,
            cached_tokens=3,
            prefetch_committed=True,
            memory_items=items,
            error="boom",
            extra=extra,
        )
        self.assertEqual("hello", r.text)
        self.assertEqual(42.5, r.ttft_ms)
        self.assertEqual(10, r.prompt_tokens)
        self.assertEqual(5, r.completion_tokens)
        self.assertEqual(3, r.cached_tokens)
        self.assertTrue(r.prefetch_committed)
        self.assertEqual(items, r.memory_items)
        self.assertEqual("boom", r.error)
        self.assertEqual(extra, r.extra)

    def test_error_defaults_to_none(self):
        self.assertIsNone(AgentResponse().error)

    def test_extra_defaults_to_empty_dict(self):
        self.assertEqual({}, AgentResponse().extra)

    def test_memory_items_independent_per_instance(self):
        a = AgentResponse()
        b = AgentResponse()
        a.memory_items.append({"x": 1})
        self.assertEqual([], b.memory_items)

    def test_extra_independent_per_instance(self):
        a = AgentResponse()
        b = AgentResponse()
        a.extra["k"] = "v"
        self.assertEqual({}, b.extra)

    def test_is_mutable(self):
        r = AgentResponse()
        r.text = "mutated"
        r.error = "err"
        self.assertEqual("mutated", r.text)
        self.assertEqual("err", r.error)

    def test_repr_contains_fields(self):
        r = AgentResponse(text="hi", error="boom")
        rep = repr(r)
        self.assertIn("AgentResponse", rep)
        self.assertIn("text='hi'", rep)
        self.assertIn("error='boom'", rep)


# ------------------------------------------------------------------ #
#  AgentPlugin ABC                                                   #
# ------------------------------------------------------------------ #

class AgentPluginABCTests(unittest.TestCase):
    """Verify the abstract base class and its default method behavior."""

    # -- instantiation / abstractness ----------------------------------

    def test_cannot_instantiate_abstract_base(self):
        with self.assertRaises(TypeError):
            AgentPlugin()  # type: ignore[abstract]

    def test_setup_is_abstract(self):
        self.assertIn("setup", AgentPlugin.__abstractmethods__)

    def test_concrete_subclass_is_instantiable(self):
        p = _MinimalPlugin()
        self.assertIsInstance(p, AgentPlugin)

    def test_setup_stores_config(self):
        p = _MinimalPlugin()
        p.setup({"key": "val"})
        self.assertEqual({"key": "val"}, p.config)

    # -- add_arguments -------------------------------------------------

    def test_add_arguments_default_is_noop(self):
        parser = argparse.ArgumentParser()
        _MinimalPlugin.add_arguments(parser)
        # Only the implicit -h/--help action should be present.
        self.assertEqual({"help"}, {a.dest for a in parser._actions})

    def test_add_arguments_returns_none(self):
        self.assertIsNone(_MinimalPlugin.add_arguments(argparse.ArgumentParser()))

    def test_add_arguments_callable_on_base_class(self):
        parser = argparse.ArgumentParser()
        AgentPlugin.add_arguments(parser)
        self.assertEqual({"help"}, {a.dest for a in parser._actions})

    def test_add_arguments_callable_on_instance(self):
        plugin = _MinimalPlugin()
        parser = argparse.ArgumentParser()
        plugin.add_arguments(parser)
        self.assertEqual({"help"}, {a.dest for a in parser._actions})

    # -- inject_memories -----------------------------------------------

    def test_inject_memories_returns_session_id(self):
        plugin = _MinimalPlugin()
        self.assertEqual("sid", plugin.inject_memories([], session_id="sid"))

    def test_inject_memories_default_session_id_is_empty(self):
        plugin = _MinimalPlugin()
        self.assertEqual("", plugin.inject_memories([]))

    def test_inject_memories_ignores_backend(self):
        plugin = _MinimalPlugin()
        for backend in ("echomem", "openviking", "other"):
            with self.subTest(backend=backend):
                self.assertEqual(
                    "sid",
                    plugin.inject_memories([], backend=backend, session_id="sid"),
                )

    def test_inject_memories_ignores_memories_content(self):
        plugin = _MinimalPlugin()
        result = plugin.inject_memories(
            [{"text": "a"}, {"text": "b"}], session_id="sid",
        )
        self.assertEqual("sid", result)

    # -- create_session ------------------------------------------------

    def test_create_session_raises_not_implemented(self):
        plugin = _MinimalPlugin()
        with self.assertRaises(NotImplementedError):
            plugin.create_session()

    def test_create_session_with_title_raises_not_implemented(self):
        plugin = _MinimalPlugin()
        with self.assertRaises(NotImplementedError):
            plugin.create_session("title")

    # -- send_message --------------------------------------------------

    def test_send_message_raises_not_implemented(self):
        plugin = _MinimalPlugin()
        with self.assertRaises(NotImplementedError):
            plugin.send_message("sid", "msg")

    def test_send_message_with_extra_raises_not_implemented(self):
        plugin = _MinimalPlugin()
        with self.assertRaises(NotImplementedError):
            plugin.send_message("sid", "msg", "/", extra={"q": 1})

    # -- supports_typing_simulation ------------------------------------

    def test_supports_typing_simulation_default_false(self):
        plugin = _MinimalPlugin()
        self.assertFalse(plugin.supports_typing_simulation)

    # -- simulate_typing -----------------------------------------------

    def test_simulate_typing_default_returns_none(self):
        plugin = _MinimalPlugin()
        self.assertIsNone(plugin.simulate_typing("sid", "/", "text"))

    def test_simulate_typing_custom_params_returns_none(self):
        plugin = _MinimalPlugin()
        for speed, jitter in [(200, 20), (10, 5), (0, 0)]:
            with self.subTest(speed_ms=speed, jitter_ms=jitter):
                self.assertIsNone(
                    plugin.simulate_typing(
                        "sid", "/", "x", speed_ms=speed, jitter_ms=jitter,
                    )
                )

    # -- teardown ------------------------------------------------------

    def test_teardown_does_not_raise(self):
        plugin = _MinimalPlugin()
        plugin.teardown()  # should not raise

    def test_teardown_returns_none(self):
        plugin = _MinimalPlugin()
        self.assertIsNone(plugin.teardown())

    # -- getlog --------------------------------------------------------

    def test_getlog_is_abstract(self):
        self.assertIn("getlog", AgentPlugin.__abstractmethods__)

    def test_getlog_minimal_plugin_is_noop(self):
        plugin = _MinimalPlugin()
        plugin.getlog()  # must not raise

    def test_getlog_returns_str(self):
        plugin = _MinimalPlugin()
        result = plugin.getlog()
        self.assertIsInstance(result, str)

    # -- qa_profile ----------------------------------------------------

    def test_qa_profile_returns_descriptor_id(self):
        plugin = _MinimalPlugin()
        self.assertEqual("minimal", plugin.qa_profile)

    def test_qa_profile_reflects_descriptor_id(self):
        class _Other(AgentPlugin):
            descriptor = AgentDescriptor(
                id="other-id", name="o", description="d",
            )

            def setup(self, config: dict) -> None:
                pass

            def getlog(self) -> str:
                return "{}"

        self.assertEqual("other-id", _Other().qa_profile)


# ------------------------------------------------------------------ #
#  registry.get_plugin_class                                         #
# ------------------------------------------------------------------ #

class GetPluginClassTests(unittest.TestCase):
    """Verify dynamic plugin loading by name."""

    def test_loads_bare_llm_plugin(self):
        cls = get_plugin_class("bare_llm")
        self.assertIs(cls, BareLLMPlugin)

    def test_returns_agent_plugin_subclass(self):
        cls = get_plugin_class("bare_llm")
        self.assertTrue(issubclass(cls, AgentPlugin))

    def test_result_is_not_agent_plugin_base(self):
        cls = get_plugin_class("bare_llm")
        self.assertIsNot(cls, AgentPlugin)

    def test_nonexistent_plugin_raises_import_error(self):
        with self.assertRaises(ImportError):
            get_plugin_class("does_not_exist_xyz")

    def test_module_without_subclass_raises_value_error(self):
        fake = types.ModuleType("plugins.nosub.plugin")
        with patch("importlib.import_module", return_value=fake):
            with self.assertRaises(ValueError):
                get_plugin_class("nosub")

    def test_module_with_only_base_class_raises_value_error(self):
        fake = types.ModuleType("plugins.onlybase.plugin")
        fake.AgentPlugin = AgentPlugin
        with patch("importlib.import_module", return_value=fake):
            with self.assertRaises(ValueError):
                get_plugin_class("onlybase")

    def test_value_error_message_mentions_plugin_name(self):
        fake = types.ModuleType("plugins.nosub.plugin")
        with patch("importlib.import_module", return_value=fake):
            with self.assertRaises(ValueError) as ctx:
                get_plugin_class("nosub")
        self.assertIn("nosub", str(ctx.exception))


# ------------------------------------------------------------------ #
#  registry.load_agent_plugin                                        #
# ------------------------------------------------------------------ #

def _mock_plugin_cls(instance: MagicMock | None = None) -> MagicMock:
    """Build a mock plugin class with __name__ (registry logs plugin_cls.__name__)."""
    cls = MagicMock(return_value=instance or MagicMock())
    cls.__name__ = "MockPlugin"
    return cls


class LoadAgentPluginTests(unittest.TestCase):
    """Verify instantiation + setup orchestration."""

    def test_instantiates_and_calls_setup(self):
        mock_instance = MagicMock()
        mock_cls = _mock_plugin_cls(mock_instance)
        with patch("plugins.registry.get_plugin_class", return_value=mock_cls):
            result = load_agent_plugin("x", {"k": "v"})
        mock_cls.assert_called_once_with()
        mock_instance.setup.assert_called_once_with({"k": "v"})
        self.assertIs(mock_instance, result)

    def test_passes_config_to_setup(self):
        mock_instance = MagicMock()
        mock_cls = _mock_plugin_cls(mock_instance)
        config = {"llm_base_url": "http://llm", "llm_model": "m"}
        with patch("plugins.registry.get_plugin_class", return_value=mock_cls):
            load_agent_plugin("x", config)
        mock_instance.setup.assert_called_once_with(config)

    def test_empty_config(self):
        mock_instance = MagicMock()
        mock_cls = _mock_plugin_cls(mock_instance)
        with patch("plugins.registry.get_plugin_class", return_value=mock_cls):
            load_agent_plugin("x", {})
        mock_instance.setup.assert_called_once_with({})

    def test_none_config(self):
        mock_instance = MagicMock()
        mock_cls = _mock_plugin_cls(mock_instance)
        with patch("plugins.registry.get_plugin_class", return_value=mock_cls):
            load_agent_plugin("x", None)
        mock_instance.setup.assert_called_once_with(None)

    @patch("plugins.bare_llm.plugin.LLMClient")
    def test_loads_real_bare_llm_plugin(self, mock_llm_cls):
        plugin = load_agent_plugin("bare_llm", {
            "llm_base_url": "http://llm:8080",
            "llm_api_key": "k",
            "llm_model": "m",
        })
        self.assertIsInstance(plugin, BareLLMPlugin)
        mock_llm_cls.assert_called_once()
        self.assertIs(mock_llm_cls.return_value, plugin._llm)


if __name__ == "__main__":
    unittest.main()
