"""LLM ops domain: model lifecycle ownership.

This package owns everything about *running* model backends — the state machine
(state.py), the per-model record (instance.py), the single source of truth
(registry.py), process spawning/killing (process.py), readiness probing
(probes.py), per-kind launch recipes (launchers.py), the background state
reconciler (reconciler.py), and the start/stop orchestration (manager.py).

The api/ layer is a thin adapter over ModelManager; it holds no process logic.
"""
