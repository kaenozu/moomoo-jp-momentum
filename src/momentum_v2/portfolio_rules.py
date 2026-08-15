from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Exposure:
    target: float = 1.0

    def __post_init__(self) -> None:
        if not 0.0 <= self.target <= 1.0:
            raise ValueError("exposure target must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class RiskPolicy:
    max_positions: int = 10
    max_exposure: float = 1.0

    def __post_init__(self) -> None:
        if self.max_positions < 1:
            raise ValueError("max_positions must be positive")
        if not 0.0 <= self.max_exposure <= 1.0:
            raise ValueError("max_exposure must be between 0 and 1")
