"""Enums for the model lifecycle state machine.

The whole point of the llmops redesign is that there is *one* place that defines
what states a model can be in and *one* component (the reconciler) that decides
which state a model is actually in. These enums are that vocabulary.
"""
from enum import Enum


class ModelState(str, Enum):
    """Observed state, derived by the reconciler from process + health probe.

    Transitions:
        STOPPED  -> STARTING (start requested, process spawned)
        STARTING -> READY    (health probe returns 200)
        STARTING -> FAILED   (process exited, or startup timed out)
        READY    -> FAILED   (process exited unexpectedly)
        READY    -> STOPPING -> STOPPED (stop requested)
        FAILED   -> STARTING (start requested again)
    """

    STOPPED = "stopped"
    STARTING = "starting"
    READY = "ready"
    FAILED = "failed"
    STOPPING = "stopping"


class ModelKind(str, Enum):
    """What sort of backend an instance is, picks the launcher + probe."""

    LLM = "llm"
    EMBEDDING = "embedding"


class Desired(str, Enum):
    """The state the user has asked for; compared against the observed state."""

    RUNNING = "running"
    STOPPED = "stopped"
