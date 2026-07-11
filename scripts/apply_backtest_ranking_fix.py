from __future__ import annotations

import json
from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


path = Path("src/backtest_runner.py")
source = path.read_text(encoding="utf-8")
source = replace_once(
    source,
    "from typing import Optional",
    "from typing import Optional, Protocol",
    "typing import",
)
source = replace_once(
    source,
    "from .strategies import StrategyRegistry",
    "from .strategies import StrategyRegistry, StrategyResult",
    "strategy imports",
)
source = replace_once(
    source,
    '''    avg_cost: float = 0.0  # SELL時のみ使用\n\n\nBM2559 = "JP.2559"''',
    '''    avg_cost: float = 0.0  # SELL時のみ使用\n\n\nclass _StrategyEvaluator(Protocol):\n    """Minimal strategy interface required for candidate ranking."""\n\n    def evaluate(\n        self,\n        indicators: StockIndicators,\n        benchmark_returns: Optional[dict] = None,\n    ) -> StrategyResult:\n        ...\n\n\ndef _rank_buy_candidates(\n    valid_pairs: list[tuple[str, StockIndicators]],\n    strategy: _StrategyEvaluator,\n) -> list[tuple[str, StockIndicators]]:\n    """Evaluate every candidate, then rank BUY signals deterministically.\n\n    Database insertion order must never decide which symbols consume limited\n    position slots. Higher strategy scores rank first; symbol code is the stable\n    tie-breaker. WATCH/EXCLUDE results are removed before slot allocation.\n    """\n    evaluated: list[tuple[str, StockIndicators, StrategyResult]] = []\n    for code, indicators in valid_pairs:\n        result = strategy.evaluate(indicators)\n        if result.signal_type == "BUY_CANDIDATE":\n            evaluated.append((code, indicators, result))\n\n    evaluated.sort(key=lambda item: (-item[2].score, item[0]))\n    return [(code, indicators) for code, indicators, _ in evaluated]\n\n\nBM2559 = "JP.2559"''',
    "ranking helper insertion",
)
source = replace_once(
    source,
    "            valid_pairs = []",
    "            valid_pairs: list[tuple[str, StockIndicators]] = []",
    "valid pair typing",
)
source = replace_once(
    source,
    "                valid_pairs.append((sym, ind))",
    "                valid_pairs.append((code, ind))",
    "valid pair contents",
)
source = replace_once(
    source,
    '''            for sym, ind in valid_pairs:\n                if slots_available <= 0:\n                    break\n                code = sym["code"]\n\n                available_cash = self.cash - self.reserved_cash\n                if ind.close and ind.close > available_cash:\n                    continue\n\n                result = strategy.evaluate(ind)\n                if result.signal_type != "BUY_CANDIDATE":\n                    continue\n\n                next_bar = self._next_open_bar(code, day)''',
    '''            ranked_candidates = _rank_buy_candidates(valid_pairs, strategy)\n            for code, ind in ranked_candidates:\n                if slots_available <= 0:\n                    break\n\n                available_cash = self.cash - self.reserved_cash\n                if ind.close and ind.close > available_cash:\n                    continue\n\n                next_bar = self._next_open_bar(code, day)''',
    "ranked selection loop",
)
path.write_text(source, encoding="utf-8")


test_source = '''"""Regression tests for deterministic backtest candidate ranking."""\n\nfrom src.backtest_runner import _rank_buy_candidates\nfrom src.indicators import StockIndicators\nfrom src.strategies import StrategyResult\n\n\ndef _indicator(code: str) -> StockIndicators:\n    return StockIndicators(\n        code=code,\n        name=code,\n        date="2026-07-01",\n        close=1000.0,\n        open=995.0,\n        high=1010.0,\n        low=990.0,\n        ma25=980.0,\n        history_days=30,\n    )\n\n\nclass _ScoreStrategy:\n    def __init__(\n        self,\n        scores: dict[str, float],\n        signal_types: dict[str, str] | None = None,\n    ) -> None:\n        self.scores = scores\n        self.signal_types = signal_types or {}\n\n    def evaluate(\n        self,\n        indicators: StockIndicators,\n        benchmark_returns: dict | None = None,\n    ) -> StrategyResult:\n        return StrategyResult(\n            code=indicators.code,\n            name=indicators.name,\n            date=indicators.date,\n            strategy_name="test",\n            signal_type=self.signal_types.get(\n                indicators.code,\n                "BUY_CANDIDATE",\n            ),\n            score=self.scores[indicators.code],\n            price_at_signal=indicators.close,\n        )\n\n\ndef _codes(\n    pairs: list[tuple[str, StockIndicators]],\n    strategy: _ScoreStrategy,\n) -> list[str]:\n    return [code for code, _ in _rank_buy_candidates(pairs, strategy)]\n\n\ndef test_higher_score_wins_even_when_database_order_is_lower_first() -> None:\n    pairs = [\n        ("JP.0001", _indicator("JP.0001")),\n        ("JP.0002", _indicator("JP.0002")),\n    ]\n    strategy = _ScoreStrategy({"JP.0001": 70.0, "JP.0002": 95.0})\n\n    assert _codes(pairs, strategy) == ["JP.0002", "JP.0001"]\n\n\ndef test_equal_scores_use_symbol_code_as_stable_tie_breaker() -> None:\n    pairs = [\n        ("JP.9000", _indicator("JP.9000")),\n        ("JP.1000", _indicator("JP.1000")),\n        ("JP.5000", _indicator("JP.5000")),\n    ]\n    strategy = _ScoreStrategy(\n        {"JP.9000": 80.0, "JP.1000": 80.0, "JP.5000": 80.0}\n    )\n\n    assert _codes(pairs, strategy) == ["JP.1000", "JP.5000", "JP.9000"]\n\n\ndef test_non_buy_results_do_not_consume_ranked_slots() -> None:\n    pairs = [\n        ("JP.0001", _indicator("JP.0001")),\n        ("JP.0002", _indicator("JP.0002")),\n        ("JP.0003", _indicator("JP.0003")),\n    ]\n    strategy = _ScoreStrategy(\n        {"JP.0001": 100.0, "JP.0002": 90.0, "JP.0003": 80.0},\n        {"JP.0001": "WATCH", "JP.0002": "EXCLUDE"},\n    )\n\n    assert _codes(pairs, strategy) == ["JP.0003"]\n\n\ndef test_ranking_is_independent_of_input_order() -> None:\n    pairs = [\n        ("JP.0003", _indicator("JP.0003")),\n        ("JP.0001", _indicator("JP.0001")),\n        ("JP.0002", _indicator("JP.0002")),\n    ]\n    strategy = _ScoreStrategy(\n        {"JP.0001": 75.0, "JP.0002": 95.0, "JP.0003": 85.0}\n    )\n\n    expected = ["JP.0002", "JP.0003", "JP.0001"]\n    assert _codes(pairs, strategy) == expected\n    assert _codes(list(reversed(pairs)), strategy) == expected\n'''
Path("tests/test_backtest_ranking.py").write_text(test_source, encoding="utf-8")

config_path = Path("pyrightconfig.json")
config = json.loads(config_path.read_text(encoding="utf-8"))
for item in ("src/backtest_runner.py", "tests/test_backtest_ranking.py"):
    if item not in config["include"]:
        config["include"].append(item)
config_path.write_text(
    json.dumps(config, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
