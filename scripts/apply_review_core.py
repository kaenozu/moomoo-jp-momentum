"""One-time, readable migration for core full-source review fixes."""

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
    end = len(lines)
    next_def = re.compile(r"^" + (" " * indent) + r"(?:def|class)\s+")
    for index in range(start + 1, len(lines)):
        if lines[index].strip() and next_def.match(lines[index]):
            end = index
            break
    write(path, "".join(lines[:start]) + source.rstrip() + "\n\n" + "".join(lines[end:]))


replace_function(
    "src/signals.py",
    "_is_etf",
    '''    def _is_etf(self, code: str) -> bool:
        """設定に明示されたETFコードだけをETFとして扱う。"""
        configured = self.config.get("strategies.etf_rotation.codes", None)
        if configured is None:
            configured = ["JP.2559", "JP.1306", "JP.1320", "JP.2558", "JP.2563"]
        return code in {str(item) for item in configured}
''',
)

replace_function(
    "src/screener.py",
    "save_signals_to_db",
    '''    def save_signals_to_db(self, candidates: list[Candidate]) -> int:
        """benchmarkを除き、signals.idを維持してUPSERTする。"""
        rows = [item for item in candidates if item.signal_type != "BENCHMARK"]
        if not rows:
            return 0
        now = datetime.now().isoformat()
        sql = """
            INSERT INTO signals
            (code, date, signal_type, strategy_name, score, reason,
             risk_warnings, price_at_signal, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(strategy_name, code, date) DO UPDATE SET
                signal_type = excluded.signal_type,
                score = excluded.score,
                reason = excluded.reason,
                risk_warnings = excluded.risk_warnings,
                price_at_signal = excluded.price_at_signal,
                created_at = excluded.created_at
        """
        params = [
            (item.code, item.date, item.signal_type, item.strategy_name,
             item.score, item.reason, item.risk_warnings, item.close, now)
            for item in rows
        ]
        with sqlite3.connect(self.db_path, timeout=5.0) as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.executemany(sql, params)
        return len(rows)
''',
)

scoring = read("src/scoring.py")
old = '''        scoring_config = config.get("scoring", {})
        self.enable_risk_penalty = scoring_config.get("enable_risk_penalty", True)
'''
new = '''        scoring_config = config.get("scoring", {})
        self.enable_risk_penalty = scoring_config.get("enable_risk_penalty", True)
        configured_weights = scoring_config.get("weights", {})
        self.weights = {
            "trend": float(configured_weights.get("trend", 30)),
            "volume": float(configured_weights.get("volume", 20)),
            "relative_strength": float(configured_weights.get("relative_strength", 25)),
            "liquidity": float(configured_weights.get("liquidity", 15)),
            "high_20d": float(configured_weights.get("high_20d", 10)),
            "risk_warning": abs(float(configured_weights.get("risk_warning", -30))),
        }
'''
if old in scoring:
    scoring = scoring.replace(old, new, 1)
scoring = scoring.replace(
    "        return score\n\n    def score_volume",
    "        return score / 30.0 * self.weights[\"trend\"]\n\n    def score_volume",
    1,
)
scoring = scoring.replace(
    "        return max(0.0, min(20.0, score))",
    "        return max(0.0, min(20.0, score)) / 20.0 * self.weights[\"volume\"]",
    1,
)
scoring = scoring.replace(
    "        return min(score, 25.0)",
    "        return min(score, 25.0) / 25.0 * self.weights[\"relative_strength\"]",
    1,
)
scoring = scoring.replace(
    "        return min(score, 15.0)",
    "        return min(score, 15.0) / 15.0 * self.weights[\"liquidity\"]",
    1,
)
scoring = scoring.replace(
    "        return min(score, 10.0)",
    "        return min(score, 10.0) / 10.0 * self.weights[\"high_20d\"]",
    1,
)
scoring = scoring.replace(
    "        return max(penalty, -30.0)",
    "        return max(penalty, -30.0) / 30.0 * self.weights[\"risk_warning\"]",
    1,
)
write("src/scoring.py", scoring)

scheduler = read("scheduler.py").replace(
    '_run_script(["generate_reports.py", "--weekly"], timeout=600, name="週次レポート")',
    '_run_script(["strategy_compare.py", "--csv", "--html"], timeout=600, name="週次レポート")',
)
write("scheduler.py", scheduler)

requirements = read("requirements.txt")
if "yfinance" not in requirements.lower():
    requirements = requirements.rstrip() + "\n\n# 補完マーケットデータ\nyfinance>=0.2.65,<1.0\n"
write("requirements.txt", requirements)

for path in ("config.example.yaml", "config.yaml"):
    if not (ROOT / path).exists():
        continue
    config = read(path).replace('    close: "15:00"', '    close: "15:30"')
    config = re.sub(r"(latest_bar_count:\s*)\d+", r"\g<1>120", config)
    if "daily_cycle:" not in config:
        config += '\ndaily_cycle:\n  markets: ["JP"]\n  fetch_mode: "latest"\n  latest_bar_count: 120\n'
    if "backtest:" not in config:
        config += '\nbacktest:\n  market: "JP"\n'
    if "strategies:" not in config:
        config += '\nstrategies:\n  etf_rotation:\n    codes: ["JP.2559", "JP.1306", "JP.1320", "JP.2558", "JP.2563"]\n'
    write(path, config)

split_test = ROOT / "tests/test_split_adjustment.py"
if split_test.exists():
    text = split_test.read_text(encoding="utf-8")
    text = text.replace(
        '[("2026-03-31", 2500.0), ("2026-04-01", 252.0)]',
        '[("2026-03-31", 250.0), ("2026-04-01", 252.0)]',
    )
    text = text.replace(
        '[("2026-06-08", 20000.0), ("2026-06-09", 2010.0)]',
        '[("2026-06-08", 2000.0), ("2026-06-09", 2010.0)]',
    )
    split_test.write_text(text, encoding="utf-8")

write(
    "pyrightconfig.json",
    '''{
  "include": ["run_daily_cycle.py", "scheduler.py", "src", "tests"],
  "exclude": ["scripts"],
  "pythonVersion": "3.11",
  "typeCheckingMode": "basic"
}
''',
)

print("core review fixes applied")
