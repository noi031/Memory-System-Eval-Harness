"""OpenViking memory plugin: CLI args, client creation, memory injection.

Design intent: mirrors the EchoMemory plugin structure -- owns its own CLI
arguments, creates self.client in setup(), and implements inject_memories
for the simple open/add/commit/poll primitive used by dynamic workflows.

OpenViking has no has_archives() check, so inject_memories always commits.
teardown() is a no-op -- OpenViking has no server-side tenant deletion.
"""

from __future__ import annotations

import argparse
import os

from memories.base import (
    MemoryCapability,
    MemoryDescriptor,
    MemoryPlugin,
)

from .client import OpenVikingClient


class OpenVikingPlugin(MemoryPlugin):
    descriptor = MemoryDescriptor(
        id="openviking",
        name="OpenViking",
        status="active",
        description="OpenViking HTTP backend for session import, retrieval, and local workspace file access.",
        capabilities=(
            MemoryCapability("session_write", "Create sessions and append messages."),
            MemoryCapability("commit_session", "Commit sessions and poll extraction tasks."),
            MemoryCapability("relevant_memory", "Retrieve account-scoped memory via /api/v1/search/find."),
            MemoryCapability("content_read", "Read full content by viking:// URI from local workspace."),
        ),
    )

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        g = parser.add_argument_group("OpenViking")
        g.add_argument(
            "--echomem-url",
            default=os.getenv("OPENVIKING_BASE_URL", "http://127.0.0.1:19080"),
            help="OpenViking HTTP base URL",
        )
        g.add_argument("--echomem-auth-key", default=os.getenv("OPENVIKING_API_KEY", ""), help="OpenViking API key")
        g.add_argument("--account", default=os.getenv("ECHOMEM_ACCOUNT", "default"))
        g.add_argument("--user-id", default=os.getenv("ECHOMEM_USER_ID", "default"))
        g.add_argument("--agent-id", default=os.getenv("ECHOMEM_AGENT_ID", "default"))
        g.add_argument("--workspace", default=os.getenv("ECHOMEM_WORKSPACE", ""), help="OpenViking workspace path")
        identity = g.add_mutually_exclusive_group()
        identity.add_argument(
            "--reuse-memory-account",
            action="store_true",
            help="Reuse the configured identity instead of isolating this evaluation run",
        )
        identity.add_argument(
            "--keep-memory-account",
            action="store_true",
            help="Keep the isolated account after evaluation for diagnostics",
        )

    def setup(self, config: dict) -> None:
        self._commit_timeout_s = float(config.get("commit_timeout_s", 0.0))
        self._commit_poll_interval_s = float(config.get("commit_poll_interval_s", 2.0))

        self.client = OpenVikingClient(
            base_url=config.get("echomem_url", "http://127.0.0.1:19080"),
            api_key=config.get("echomem_auth_key", ""),
            account=config.get("account", "default"),
            user_id=config.get("user_id", "default"),
            agent_id=config.get("agent_id", "default"),
            workspace=config.get("workspace", ""),
            timeout_s=float(config.get("timeout_s", 60.0)),
            max_retries=int(config.get("max_retries", 3)),
        )

        # Identity isolation: OpenViking generates a unique account name
        # (no server-side tenant creation). Skip when benchmark_name/run_id
        # are absent or --reuse-memory-account is set.
        benchmark_name = config.get("benchmark_name", "")
        run_id = config.get("run_id", "")
        reuse = config.get("reuse_memory_account", False)

        if benchmark_name and run_id and not reuse:
            label = f"eval-{benchmark_name}-{run_id}"
            self.client.provision_isolated_identity(label)

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
        pass


PLUGIN = OpenVikingPlugin()
