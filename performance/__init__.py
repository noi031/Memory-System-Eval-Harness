"""Generic HTTP scenario load-testing engine.

A scenario is Python code (task functions driven by the engine's worker
pool and ``ctx`` API); a load profile is YAML data (target, worker
count, duration, mix, arrival, params).  The engine has no knowledge of
a specific backend.
"""

__version__ = "0.2.0"
