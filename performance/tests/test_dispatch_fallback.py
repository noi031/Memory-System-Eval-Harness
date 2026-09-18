"""Target dispatch fallback (dispatch.py) unit tests.

``run_target`` prefers ``targets/<name>/main.py`` and falls back to
``targets/<name>/observation_run.py`` when the former does not exist, so
``run.py --target echomem`` keeps routing to the live entry after the old
objective-suite entry was removed.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace

from performance import dispatch as dispatch_module


def test_dispatch_prefers_main_entry(monkeypatch):
    calls = []

    def fake_import(name):
        calls.append(name)
        if name == "performance.targets.echomem.main":
            return SimpleNamespace(main=lambda argv: 7)
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(importlib, "import_module", fake_import)
    monkeypatch.setattr(Path, "is_file", lambda self: self.name == "main.py")
    assert dispatch_module.run_target("echomem", ["--help"]) == 7
    assert calls == ["performance.targets.echomem.main"]


def test_dispatch_falls_back_to_observation_run(monkeypatch):
    calls = []

    def fake_import(name):
        calls.append(name)
        if name == "performance.targets.echomem.observation_run":
            return SimpleNamespace(main=lambda argv: 3)
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(importlib, "import_module", fake_import)
    monkeypatch.setattr(Path, "is_file",
                        lambda self: self.name == "observation_run.py")
    assert dispatch_module.run_target("echomem", []) == 3
    assert calls == ["performance.targets.echomem.observation_run"]


def test_dispatch_reports_missing_entry(monkeypatch, capsys):
    monkeypatch.setattr(Path, "is_file", lambda self: False)
    assert dispatch_module.run_target("echomem", []) == 2
    assert "no main() entry point" in capsys.readouterr().err


def test_dispatch_rejects_unknown_target(capsys):
    assert dispatch_module.run_target("no-such-target", []) == 2
    assert "unknown target system" in capsys.readouterr().err
