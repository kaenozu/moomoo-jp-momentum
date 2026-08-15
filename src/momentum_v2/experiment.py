from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True, slots=True)
class Experiment:
    """Reproducibility metadata; persistence is intentionally out of scope for V2-002."""

    experiment_id: str
    strategy_name: str
    start: date
    end: date
    git_sha: str
    dataset_sha256: str
    config_sha256: str
    metrics: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.experiment_id or not self.strategy_name:
            raise ValueError("experiment_id and strategy_name are required")
        if self.end < self.start:
            raise ValueError("experiment end must not precede start")
        if not self.git_sha or not self.dataset_sha256 or not self.config_sha256:
            raise ValueError("experiment provenance hashes are required")
