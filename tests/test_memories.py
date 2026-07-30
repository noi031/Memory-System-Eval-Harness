from __future__ import annotations

import unittest

from memories import (
    available_memories,
    get_memory_plugin,
)
from memories.echomemory import EchoMemClient
from memories.openviking import OpenVikingClient


class MemoryRegistryTests(unittest.TestCase):
    def test_registers_echomemory_openviking_and_none(self):
        rows = available_memories()

        self.assertEqual(
            ["echomemory", "openviking", "none"],
            [row["id"] for row in rows],
        )
        real_rows = [row for row in rows if row["id"] != "none"]
        self.assertTrue(all(row["contract"]["ok"] for row in real_rows))

    def test_echomemory_plugin_creates_existing_client(self):
        plugin = get_memory_plugin("echomemory")
        plugin.setup({
            "echomem_url": "http://example.test",
            "echomem_auth_key": "secret",
        })
        client = plugin.client

        self.assertIsInstance(client, EchoMemClient)
        self.assertEqual("secret", client.auth_key)

    def test_openviking_plugin_creates_client(self):
        plugin = get_memory_plugin("openviking")
        plugin.setup({
            "echomem_url": "http://example.test",
            "echomem_auth_key": "secret",
        })
        client = plugin.client

        self.assertIsInstance(client, OpenVikingClient)
        self.assertEqual("secret", client.api_key)
        self.assertEqual("secret", client.auth_key)


if __name__ == "__main__":
    unittest.main()
