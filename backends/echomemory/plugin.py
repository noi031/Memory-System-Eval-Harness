"""EchoMemory backend plugin."""

from __future__ import annotations

from backends.base import (
    BackendCapability,
    BackendConfig,
    BackendDescriptor,
)

from .client import EchoMemClient


class EchoMemoryPlugin:
    descriptor = BackendDescriptor(
        id="echomemory",
        name="EchoMemory",
        status="active",
        description="EchoMemory HTTP backend for isolated import, retrieval, and QA.",
        capabilities=(
            BackendCapability("session_write", "Create sessions and append messages."),
            BackendCapability("commit_session", "Commit sessions and poll extraction."),
            BackendCapability("relevant_memory", "Retrieve account-scoped memory."),
            BackendCapability("content_read", "Read full content by EchoMemory URI."),
            BackendCapability("identity_isolation", "Provision and delete evaluation identities."),
        ),
    )

    def create_client(self, config: BackendConfig) -> EchoMemClient:
        return EchoMemClient(
            base_url=config.base_url,
            auth_key=config.api_key,
            account=config.account,
            user_id=config.user_id,
            agent_id=config.agent_id,
            workspace=config.workspace,
            timeout_s=config.timeout_s,
            max_retries=config.max_retries,
        )


PLUGIN = EchoMemoryPlugin()
