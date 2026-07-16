"""Apply focused fixes identified by the first full pytest run."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    (ROOT / path).write_text(text, encoding="utf-8")


def replace_function(path: str, name: str, source: str) -> None:
    text = read(path)
    lines = text.splitlines(keepends=True)
    pattern = re.compile(rf"^(\s*)def {re.escape(name)}\s*\(")
    start = indent = None
    for index, line in enumerate(lines):
        match = pattern.match(line)
        if match:
            start = index
            indent = len(match.group(1))
            break
    if start is None or indent is None:
        raise RuntimeError(f"{path}: {name} not found")
    next_def = re.compile(r"^" + (" " * indent) + r"(?:def|class)\s+")
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if lines[index].strip() and next_def.match(lines[index]):
            end = index
            break
    write(path, "".join(lines[:start]) + source.rstrip() + "\n\n" + "".join(lines[end:]))


quote_service = read("src/quote_service.py")
quote_service = quote_service.replace(
    "            if next_page_key is None or len(data) < batch_size:\n                break\n",
    "            if next_page_key is None:\n                break\n",
)
write("src/quote_service.py", quote_service)

replace_function(
    "src/screener.py",
    "_row_to_indicators",
    '''    def _row_to_indicators(self, row: pd.Series) -> StockIndicators:
        code = str(_none_if_nan(row.get("code")) or "")
        date = str(_none_if_nan(row.get("date")) or "")
        close = _none_if_nan(row.get("close"))
        if close is None:
            raise ValueError(f"close is missing: code={code}, date={date}")
        return StockIndicators(
            code=code,
            name=_none_if_nan(row.get("name")),
            date=date,
            close=float(close),
            open=0.0,
            high=0.0,
            low=0.0,
            ma5=_none_if_nan(row.get("ma5")),
            ma25=_none_if_nan(row.get("ma25")),
            volume=int(_none_if_nan(row.get("volume")) or 0),
            volume_ma20=_none_if_nan(row.get("volume_ma20")),
            volume_ratio=_none_if_nan(row.get("volume_ratio")),
            turnover=_none_if_nan(row.get("turnover")) or 0,
            high_20d=_none_if_nan(row.get("high_20d")),
            high_20d_distance=_none_if_nan(row.get("distance_from_high_20d")),
            daily_return=_none_if_nan(row.get("daily_return")),
            return_5d=_none_if_nan(row.get("return_5d")),
            return_20d=_none_if_nan(row.get("return_20d")),
            return_60d=_none_if_nan(row.get("return_60d")),
            return_5d_vs_benchmark=_none_if_nan(row.get("return_5d_vs_benchmark")),
            return_20d_vs_benchmark=_none_if_nan(row.get("return_20d_vs_benchmark")),
            return_60d_vs_benchmark=_none_if_nan(row.get("return_60d_vs_benchmark")),
            relative_strength_rank=_none_if_nan(row.get("relative_strength_rank")),
            history_days=int(_none_if_nan(row.get("history_days")) or 0),
            volume_ratio_percentile=_none_if_nan(row.get("volume_ratio_percentile")),
            volume_ratio_rank=_none_if_nan(row.get("volume_ratio_rank")),
            relative_volume_ratio=_none_if_nan(row.get("relative_volume_ratio")),
            market_median_volume_ratio=_none_if_nan(row.get("market_median_volume_ratio")),
        )
''',
)

replace_function(
    "src/benchmark.py",
    "save_benchmark_prices",
    '''    def save_benchmark_prices(self, code: str, df: pd.DataFrame) -> int:
        """Save benchmark prices without precomputing unadjusted returns."""
        count = 0
        now = datetime.now().isoformat()
        with self._get_connection() as conn:
            for _, row in df.iterrows():
                date = str(row.get("time_key", ""))[:10]
                close = row.get("close")
                try:
                    conn.execute(
                        """
                        INSERT INTO benchmark_prices
                        (benchmark_code, date, close, daily_return, created_at, updated_at)
                        VALUES (?, ?, ?, NULL, ?, ?)
                        ON CONFLICT(benchmark_code, date) DO UPDATE SET
                            close = excluded.close,
                            daily_return = NULL,
                            updated_at = excluded.updated_at
                        """,
                        (code, date, close, now, now),
                    )
                    count += 1
                except sqlite3.Error as exc:
                    logger.error("ベンチマーク保存エラー: %s %s - %s", code, date, exc)
        return count
''',
)

test_core = read("tests/test_core.py")
needle = '''        store = DataStore(config)

        df = pd.DataFrame([{
            "time_key": "2026-07-01",
'''
replacement = '''        store = DataStore(config)
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO symbols(code, name) VALUES('JP.0001', 'テスト')"
            )

        df = pd.DataFrame([{
            "time_key": "2026-07-01",
'''
if needle in test_core:
    test_core = test_core.replace(needle, replacement, 1)
write("tests/test_core.py", test_core)

print("first regression follow-ups applied")
