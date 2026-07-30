"""Registry for memory backend plugins."""

from __future__ import annotations

from typing import Any

from .base import BackendConfig, MemoryBackendPlugin, validate_backend


def get_backend_plugin(backend_id: str) -> MemoryBackendPlugin:
    normalized = str(backend_id or "").strip().lower()
    if normalized == "echomemory":
        from .echomemory.plugin import PLUGIN

        return PLUGIN
    raise ValueError(f"unknown memory backend: {backend_id}")


def create_backend_client(backend_id: str, **kwargs: Any):
    return get_backend_plugin(backend_id).create_client(BackendConfig(**kwargs))


def available_backends() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for backend_id in ("echomemory",):
        plugin = get_backend_plugin(backend_id)
        row = plugin.descriptor.public()
        row["contract"] = validate_backend(plugin)
        rows.append(row)
    return rows
