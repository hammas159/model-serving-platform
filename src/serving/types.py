"""Shared value objects."""

from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field
from typing import Any


class Stage(enum.StrEnum):
    """Where a model version sits in its lifecycle.

    CHAMPION is the version serving production traffic. CHALLENGER is competing for
    that slot. SHADOW receives a copy of traffic but its predictions are never
    returned - it is being measured, not trusted.
    """

    CHAMPION = "champion"
    CHALLENGER = "challenger"
    SHADOW = "shadow"
    ARCHIVED = "archived"


@dataclass
class ModelVersion:
    name: str
    version: int
    stage: Stage = Stage.ARCHIVED
    # Fraction of traffic, 0-1. Only meaningful for CHALLENGER.
    traffic: float = 0.0
    metrics: dict[str, float] = field(default_factory=dict)
    registered_at: float = field(default_factory=time.time)
    tags: dict[str, str] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.name}:v{self.version}"


@dataclass
class Prediction:
    model: str
    version: int
    output: Any
    latency_ms: float
    stage: Stage = Stage.CHAMPION
    # Present when a shadow model also scored this request. Never returned to the
    # caller as the answer - recorded for comparison only.
    shadow: dict | None = None
    request_id: str = ""
    error: str = ""
