"""
候補ランキング共通ロジック。

ファイルパス: src/ranking.py
何をするか: 候補をscore降順、code昇順で決定的に並べる
なぜ存在するか: バックテスト、スクリーニング、フォワード検証の選択順を一致させるため
関連ファイル: backtest_runner.py, screener.py, strategy_runner.py, scoring.py
"""

from collections.abc import Callable, Iterable
from typing import Protocol, TypeVar


class ScoredCandidate(Protocol):
    """共通ランキングに必要な候補属性。"""

    code: str
    score: float


CandidateT = TypeVar("CandidateT", bound=ScoredCandidate)
ItemT = TypeVar("ItemT")


def score_desc_code_asc_key(score: float | None, code: str) -> tuple[float, str]:
    """score降順、code昇順になる安定ソートキーを返す。"""
    normalized_score = float(score) if score is not None else 0.0
    return (-normalized_score, code)


def sort_scored_candidates(candidates: Iterable[CandidateT]) -> list[CandidateT]:
    """候補オブジェクトを共通ランキング規則で並べる。"""
    return sorted(
        candidates,
        key=lambda candidate: score_desc_code_asc_key(
            candidate.score,
            candidate.code,
        ),
    )


def sort_items_by_score(
    items: Iterable[ItemT],
    score_getter: Callable[[ItemT], float | None],
    code_getter: Callable[[ItemT], str],
) -> list[ItemT]:
    """任意の候補表現を共通ランキング規則で並べる。"""
    return sorted(
        items,
        key=lambda item: score_desc_code_asc_key(
            score_getter(item),
            code_getter(item),
        ),
    )
