from __future__ import annotations

import argparse
import contextlib
import os
import unittest
from unittest.mock import patch

from backends.memory_args import add_memory_backend_args

# Env vars that add_memory_backend_args() reads for defaults.
ENV_KEYS = (
    "ECHOMEM_BASE_URL",
    "ECHOMEM_AUTH_KEY",
    "ECHOMEM_ACCOUNT",
    "ECHOMEM_USER_ID",
    "ECHOMEM_AGENT_ID",
    "ECHOMEM_WORKSPACE",
)

# Dest -> expected default when none of the env vars are set.
EXPECTED_DEFAULTS = {
    "echomem_url": "http://127.0.0.1:8010",
    "echomem_auth_key": "",
    "account": "default",
    "user_id": "default",
    "agent_id": "default",
    "workspace": "",
    "echomem_log_dir": "",
    "commit_timeout_s": 0.0,
    "commit_poll_interval_s": 2.0,
}


@contextlib.contextmanager
def _env_with(**overrides: str):
    """Run a block with memory env vars cleared, then apply *overrides*.

    patch.dict restores the full original environment (including keys deleted
    inside the block) on exit, so the surrounding environment is untouched.
    """
    with patch.dict(os.environ, {}, clear=False):
        for key in ENV_KEYS:
            os.environ.pop(key, None)
        os.environ.update(overrides)
        yield


def _new_parser(*, with_backend_choice: bool = False) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="test-memory-args")
    add_memory_backend_args(parser, with_backend_choice=with_backend_choice)
    return parser


class DefaultArgsTests(unittest.TestCase):
    """with_backend_choice=False (the default)."""

    def test_does_not_add_memory_backend(self) -> None:
        with _env_with():
            parser = _new_parser()
            args = parser.parse_args([])
        self.assertFalse(hasattr(args, "memory_backend"))

    def test_adds_all_args_with_expected_defaults(self) -> None:
        with _env_with():
            parser = _new_parser()
            args = parser.parse_args([])
        for dest, expected in EXPECTED_DEFAULTS.items():
            with self.subTest(dest=dest):
                self.assertTrue(hasattr(args, dest), f"missing argument dest {dest}")
                self.assertEqual(expected, getattr(args, dest))

    def test_commit_timeout_s_is_float(self) -> None:
        with _env_with():
            args = _new_parser().parse_args([])
        self.assertIsInstance(args.commit_timeout_s, float)

    def test_commit_poll_interval_s_is_float(self) -> None:
        with _env_with():
            args = _new_parser().parse_args([])
        self.assertIsInstance(args.commit_poll_interval_s, float)


class BackendChoiceTests(unittest.TestCase):
    """with_backend_choice=True adds --memory-backend."""

    def test_memory_backend_default_is_echomem(self) -> None:
        with _env_with():
            args = _new_parser(with_backend_choice=True).parse_args([])
        self.assertEqual("echomem", args.memory_backend)

    def test_memory_backend_accepts_each_choice(self) -> None:
        for choice in ("echomem", "openviking"):
            with self.subTest(choice=choice):
                with _env_with():
                    args = _new_parser(with_backend_choice=True).parse_args(
                        ["--memory-backend", choice]
                    )
                self.assertEqual(choice, args.memory_backend)

    def test_still_adds_all_other_args(self) -> None:
        with _env_with():
            args = _new_parser(with_backend_choice=True).parse_args([])
        for dest, expected in EXPECTED_DEFAULTS.items():
            with self.subTest(dest=dest):
                self.assertEqual(expected, getattr(args, dest))


class EnvFallbackTests(unittest.TestCase):
    """Env vars feed the argument defaults."""

    ENV_CASES = [
        ("ECHOMEM_BASE_URL", "echomem_url", "http://example.invalid:9999"),
        ("ECHOMEM_AUTH_KEY", "echomem_auth_key", "secret-key-123"),
        ("ECHOMEM_ACCOUNT", "account", "acct-override"),
        ("ECHOMEM_USER_ID", "user_id", "user-override"),
        ("ECHOMEM_AGENT_ID", "agent_id", "agent-override"),
        ("ECHOMEM_WORKSPACE", "workspace", "/tmp/echo-workspace"),
    ]

    def test_env_vars_override_defaults(self) -> None:
        for env_key, dest, value in self.ENV_CASES:
            with self.subTest(env_key=env_key):
                with _env_with(**{env_key: value}):
                    args = _new_parser().parse_args([])
                self.assertEqual(value, getattr(args, dest))

    def test_echomem_log_dir_has_no_env_fallback(self) -> None:
        # --echomem-log-dir is always "" regardless of env; confirm isolation.
        with _env_with(ECHOMEM_LOG_DIR="/should/not/be/used"):
            args = _new_parser().parse_args([])
        self.assertEqual("", args.echomem_log_dir)


class ParsingTests(unittest.TestCase):
    """Explicit CLI values are parsed and stored correctly."""

    def test_parses_explicit_values(self) -> None:
        with _env_with():
            args = _new_parser().parse_args(
                [
                    "--echomem-url", "http://10.0.0.1:7000",
                    "--echomem-auth-key", "k",
                    "--account", "a",
                    "--user-id", "u",
                    "--agent-id", "g",
                    "--workspace", "/w",
                    "--echomem-log-dir", "/logs",
                    "--commit-timeout-s", "30.5",
                    "--commit-poll-interval-s", "0.5",
                ]
            )
        self.assertEqual("http://10.0.0.1:7000", args.echomem_url)
        self.assertEqual("k", args.echomem_auth_key)
        self.assertEqual("a", args.account)
        self.assertEqual("u", args.user_id)
        self.assertEqual("g", args.agent_id)
        self.assertEqual("/w", args.workspace)
        self.assertEqual("/logs", args.echomem_log_dir)
        self.assertEqual(30.5, args.commit_timeout_s)
        self.assertEqual(0.5, args.commit_poll_interval_s)

    def test_commit_timeout_s_parses_as_float(self) -> None:
        with _env_with():
            args = _new_parser().parse_args(["--commit-timeout-s", "30.5"])
        self.assertEqual(30.5, args.commit_timeout_s)
        self.assertIsInstance(args.commit_timeout_s, float)

    def test_commit_poll_interval_s_parses_as_float(self) -> None:
        with _env_with():
            args = _new_parser().parse_args(["--commit-poll-interval-s", "0.25"])
        self.assertEqual(0.25, args.commit_poll_interval_s)
        self.assertIsInstance(args.commit_poll_interval_s, float)

    def test_memory_backend_openviking_parses(self) -> None:
        with _env_with():
            args = _new_parser(with_backend_choice=True).parse_args(
                ["--memory-backend", "openviking"]
            )
        self.assertEqual("openviking", args.memory_backend)

    def test_invalid_memory_backend_choice_raises_system_exit(self) -> None:
        with _env_with():
            parser = _new_parser(with_backend_choice=True)
            with self.assertRaises(SystemExit):
                parser.parse_args(["--memory-backend", "redis"])

    def test_invalid_float_commit_timeout_raises_system_exit(self) -> None:
        with _env_with():
            parser = _new_parser()
            with self.assertRaises(SystemExit):
                parser.parse_args(["--commit-timeout-s", "not-a-number"])


if __name__ == "__main__":
    unittest.main()
