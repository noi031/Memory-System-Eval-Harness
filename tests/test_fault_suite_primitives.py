"""Restored probe-primitive unit tests (fault-plan dependencies).

``cursor_reconcile`` and ``recovery`` are the in-process primitives consumed
by the fault_suite probe (fault plans): cursor/message-set reconciliation
and kill-9 recovery observation. Without a real control surface or commit
evidence they must answer INCONCLUSIVE — they never claim a fault or a
recovery happened when nothing was observed.
"""

from __future__ import annotations

import threading
import unittest

from performance.ctx import ConnectionRegistry, Ctx
from performance.targets.echomem.probes import cursor_reconcile, recovery


def _ctx(params: dict) -> tuple[Ctx, list]:
    checks = []
    ctx = Ctx(
        scene="fault-suite-primitives",
        worker_id=0,
        tenant_idx=0,
        headers={},
        base_url="http://unused.invalid",
        read_timeout_s=5,
        params=params,
        duration_s=0,
        stop=threading.Event(),
        record_fn=lambda r: None,
        seq_fn=lambda: 0,
        choose_fn=lambda items: None,
        phases=[],
        checks=checks,
        registry=ConnectionRegistry(),
    )
    return ctx, checks


class CursorReconcileTests(unittest.TestCase):
    def test_without_commit_csv_is_inconclusive(self):
        ctx, checks = _ctx({})
        cursor_reconcile.run(ctx)
        check = checks[-1]
        self.assertEqual("INCONCLUSIVE", check.status)
        self.assertIn("no Commit evidence", check.reason)

    def test_values_from_payload_extracts_identities(self):
        payload = {
            "items": [{"message_id": "m1", "archive_id": "a1", "session_id": "s1"}],
            "archives": [{"archive_id": "a2"}],
        }
        messages, archives, operations = cursor_reconcile.values_from_payload(payload)
        self.assertIn("m1", messages)
        self.assertIn("a1", archives)
        self.assertIn("a2", archives)
        self.assertEqual(set(), operations)


class RecoveryProbeTests(unittest.TestCase):
    def test_without_control_surface_is_inconclusive(self):
        ctx, checks = _ctx({})
        recovery.run(ctx)
        check = checks[-1]
        self.assertEqual("INCONCLUSIVE", check.status)
        self.assertIn("pid or container is required", check.reason)


if __name__ == "__main__":
    unittest.main()
