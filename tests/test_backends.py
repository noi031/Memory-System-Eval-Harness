from __future__ import annotations

import unittest

from backends import (
    BackendConfig,
    available_backends,
    get_backend_plugin,
)
from backends.echomemory import EchoMemClient


class BackendRegistryTests(unittest.TestCase):
    def test_registers_only_echomemory(self):
        rows = available_backends()

        self.assertEqual(["echomemory"], [row["id"] for row in rows])
        self.assertTrue(all(row["contract"]["ok"] for row in rows))

    def test_echomemory_plugin_creates_existing_client(self):
        client = get_backend_plugin("echomemory").create_client(
            BackendConfig(base_url="http://example.test", api_key="secret")
        )

        self.assertIsInstance(client, EchoMemClient)
        self.assertEqual("secret", client.auth_key)


if __name__ == "__main__":
    unittest.main()
