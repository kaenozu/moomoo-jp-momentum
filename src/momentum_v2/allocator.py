from __future__ import annotations

from dataclasses import dataclass

from .portfolio_rules import Exposure, RiskPolicy


@dataclass(frozen=True, slots=True)
class EqualWeightAllocator:
    exposure: Exposure = Exposure()
    risk: RiskPolicy = RiskPolicy()

    def weights(self, scores: dict[str, float]) -> dict[str, float]:
        eligible = sorted(
            (code for code, score in scores.items() if score > 0),
            key=lambda code: (-scores[code], code),
        )
        selected = eligible[: self.risk.max_positions]
        if not selected:
            return {}
        target = min(self.exposure.target, self.risk.max_exposure)
        weight = target / len(selected)
        return {code: weight for code in selected}


@dataclass(frozen=True, slots=True)
class ScoreWeightAllocator(EqualWeightAllocator):
    def weights(self, scores: dict[str, float]) -> dict[str, float]:
        eligible = sorted(
            (code for code, score in scores.items() if score > 0),
            key=lambda code: (-scores[code], code),
        )
        selected = eligible[: self.risk.max_positions]
        total = sum(scores[code] for code in selected)
        if not selected or total <= 0:
            return {}
        target = min(self.exposure.target, self.risk.max_exposure)
        return {code: target * scores[code] / total for code in selected}
