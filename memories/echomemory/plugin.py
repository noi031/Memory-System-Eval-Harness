"""EchoMemory memory plugin: CLI args, client creation, identity isolation, memory injection.

Design intent: this plugin owns every CLI argument related to the EchoMem
HTTP service and the evaluation identity lifecycle (provision / delete).
Benchmark run_evals declare these args by calling add_memory_plugin_args,
which loads this plugin class and calls add_arguments before argparse parses.

setup() creates self.client (EchoMemClient) and provisions an isolated
identity when benchmark_name and run_id are present in config. The
platform reads self.client for retrieval and complex import operations.
inject_memories() is the simple primitive for dynamic workflows: open
session, add messages, commit, poll. teardown() is a no-op -- identity
cleanup is handled by cleanup_pending_identities() registered as an atexit
handler and called by eval.py's finally block.
"""

from __future__ import annotations

import argparse
import atexit
import logging
import os
from typing import Any

from memories.base import (
    MemoryCapability,
    MemoryDescriptor,
    MemoryPlugin,
)

from .client import EchoMemClient

logger = logging.getLogger("memory.echomemory")

# Ephemeral identities scheduled for deletion at exit or run completion.
_PENDING_CLEANUPS: list[Any] = []


class EchoMemoryPlugin(MemoryPlugin):
    descriptor = MemoryDescriptor(
        id="echomemory",
        name="EchoMemory",
        status="active",
        description="EchoMemory HTTP backend for isolated import, retrieval, and QA.",
        capabilities=(
            MemoryCapability("session_write", "Create sessions and append messages."),
            MemoryCapability("commit_session", "Commit sessions and poll extraction."),
            MemoryCapability("relevant_memory", "Retrieve account-scoped memory."),
            MemoryCapability("content_read", "Read full content by EchoMemory URI."),
            MemoryCapability("identity_isolation", "Provision and delete evaluation identities."),
        ),
    )

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        g = parser.add_argument_group("EchoMemory")
        g.add_argument(
            "--echomem-url",
            default=os.getenv("ECHOMEM_BASE_URL", "http://127.0.0.1:8010"),
            help="EchoMem HTTP base URL",
        )
        g.add_argument("--echomem-auth-key", default=os.getenv("ECHOMEM_AUTH_KEY", ""), help="EchoMem X-Auth-Key")
        g.add_argument("--account", default=os.getenv("ECHOMEM_ACCOUNT", "default"))
        g.add_argument("--user-id", default=os.getenv("ECHOMEM_USER_ID", "default"))
        g.add_argument("--agent-id", default=os.getenv("ECHOMEM_AGENT_ID", "default"))
        g.add_argument("--workspace", default=os.getenv("ECHOMEM_WORKSPACE", ""), help="EchoMem workspace path")
        g.add_argument("--echomem-log-dir", default="", help="EchoMem log directory for log collection")
        identity = g.add_mutually_exclusive_group()
        identity.add_argument(
            "--reuse-memory-account",
            action="store_true",
            help="Reuse the configured EchoMem identity instead of isolating this evaluation run",
        )
        identity.add_argument(
            "--keep-memory-account",
            action="store_true",
            help="Keep the isolated EchoMem tenant after evaluation for diagnostics",
        )

    def setup(self, config: dict) -> None:
        self._commit_timeout_s = float(config.get("commit_timeout_s", 0.0))
        self._commit_poll_interval_s = float(config.get("commit_poll_interval_s", 2.0))

        self.client = EchoMemClient(
            base_url=config.get("echomem_url", "http://127.0.0.1:8010"),
            auth_key=config.get("echomem_auth_key", ""),
            account=config.get("account", "default"),
            user_id=config.get("user_id", "default"),
            agent_id=config.get("agent_id", "default"),
            workspace=config.get("workspace", ""),
            timeout_s=float(config.get("timeout_s", 60.0)),
            max_retries=int(config.get("max_retries", 3)),
        )

        # Identity isolation: skip when benchmark_name/run_id are absent
        # (test/validation scenarios) or when --reuse-memory-account is set.
        benchmark_name = config.get("benchmark_name", "")
        run_id = config.get("run_id", "")
        reuse = config.get("reuse_memory_account", False)
        keep = config.get("keep_memory_account", False)

        if benchmark_name and run_id and not reuse:
            label = f"eval-{benchmark_name}-{run_id}"[:120]
            self.client.provision_isolated_identity(label)
            if not keep:
                _PENDING_CLEANUPS.append(self.client)

    def inject_memories(self, memories: list[dict], session_id: str = "") -> str:
        if not session_id:
            session_id = self.client.open_session(title="inject")
        for mem in memories:
            text = str(mem.get("text") or "")
            if text:
                self.client.add_message(
                    session_id,
                    "user",
                    text,
                    created_at=str(mem.get("time") or ""),
                )
        archive_id = self.client.commit_session(session_id)
        commit = self.client.poll_commit(
            session_id,
            archive_id,
            timeout_s=self._commit_timeout_s,
            poll_interval_s=self._commit_poll_interval_s,
        )
        if commit.status != "completed":
            raise RuntimeError(
                f"memory injection failed: status={commit.status} error={commit.error}"
            )
        return session_id

    def teardown(self) -> None:
        # Identity cleanup is handled by cleanup_pending_identities()
        # (atexit handler + eval.py finally block).
        pass


def cleanup_pending_identities() -> None:
    """Delete ephemeral benchmark tenants while their EchoMem service is online."""
    while _PENDING_CLEANUPS:
        client = _PENDING_CLEANUPS.pop()
        try:
            client.delete_current_identity()
        except Exception as exc:
            logger.warning("Failed to delete ephemeral EchoMem identity: %s", exc)


atexit.register(cleanup_pending_identities)


PLUGIN = EchoMemoryPlugin()
