"""
ユニバース構築テスト

ファイルパス: tests/test_universe_builder.py
何をするか: 重複マージ、高額株watch_only、低位株excluded、ETF判定のテスト
なぜ存在するか: ユニバース構築ロジックの動作確認のため
関連ファイル: src/universe_builder.py
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.universe_builder import (
    UniverseBuilder,
    build_symbol_entry,
    classify_stock,
    is_etf,
    ROLE_TRADE_CANDIDATE,
    ROLE_WATCH_ONLY,
    ROLE_BENCHMARK,
    ROLE_EXCLUDED,
    MIN_TRADE_PRICE,
    DEFAULT_MAX_TRADE_PRICE,
)


class TestClassify:
    """銘柄分類テスト"""

    def test_normal_price_is_trade_candidate(self):
        """500〜30000円はtrade_candidate"""
        entry = {"estimated_price": 5000}
        assert classify_stock(entry) == ROLE_TRADE_CANDIDATE

    def test_high_price_is_watch_only(self):
        """上限超はwatch_only"""
        entry = {"estimated_price": DEFAULT_MAX_TRADE_PRICE + 10000}
        assert classify_stock(entry) == ROLE_WATCH_ONLY

    def test_low_price_is_excluded(self):
        """500円未満はexcluded"""
        entry = {"estimated_price": 400}
        assert classify_stock(entry) == ROLE_EXCLUDED

    def test_no_price_is_trade_candidate(self):
        """price未設定はtrade_candidate"""
        entry = {}
        assert classify_stock(entry) == ROLE_TRADE_CANDIDATE

    def test_boundary_low(self):
        """500円はtrade_candidate"""
        entry = {"estimated_price": MIN_TRADE_PRICE}
        assert classify_stock(entry) == ROLE_TRADE_CANDIDATE

    def test_boundary_high(self):
        """上限はtrade_candidate"""
        entry = {"estimated_price": DEFAULT_MAX_TRADE_PRICE}
        assert classify_stock(entry) == ROLE_TRADE_CANDIDATE

    def test_boundary_high_plus_one(self):
        """上限+1はwatch_only"""
        entry = {"estimated_price": DEFAULT_MAX_TRADE_PRICE + 1}
        assert classify_stock(entry) == ROLE_WATCH_ONLY


class TestIsETF:
    """ETF判定テスト"""

    def test_etf_sector(self):
        """sector=ETFはetf扱い"""
        assert is_etf({"sector": "ETF"})

    def test_etf_type(self):
        """type=etfはetf扱い"""
        assert is_etf({"type": "etf"})

    def test_stock_not_etf(self):
        """stockはetf扱いにならない"""
        assert not is_etf({"type": "stock", "sector": "電気機器"})


class TestBuildSymbolEntry:
    """銘柄エントリ構築テスト"""

    def test_normal_stock(self):
        """通常株はtrade_candidate"""
        entry = build_symbol_entry("JP.0001", "テスト", "電気機器", estimated_price=5000)
        assert entry["role"] == ROLE_TRADE_CANDIDATE
        assert entry["tradable"] is True
        assert entry["type"] == "stock"

    def test_expensive_stock(self):
        """高額株はwatch_only"""
        entry = build_symbol_entry("JP.0001", "テスト高額", "電気機器", estimated_price=35000,
                                   max_trade_price=20000)
        assert entry["role"] == ROLE_WATCH_ONLY
        assert entry["tradable"] is False
        assert "高額株" in entry["notes"]

    def test_low_price_stock(self):
        """低位株はexcluded"""
        entry = build_symbol_entry("JP.0001", "テスト低位", "電気機器", estimated_price=300)
        assert entry["role"] == ROLE_EXCLUDED
        assert entry["tradable"] is False
        assert "低位株" in entry["notes"]

    def test_etf_sector(self):
        """ETFはbenchmark扱い"""
        entry = build_symbol_entry("JP.0001", "テストETF", "ETF", estimated_price=2000)
        assert entry["role"] == ROLE_BENCHMARK
        assert entry["tradable"] is False
        assert entry["type"] == "etf"

    def test_existing_preserved(self):
        """既存エントリがあればrole/tradable/notesを維持"""
        existing = {
            "code": "JP.0001",
            "name": "既存銘柄",
            "type": "stock",
            "role": ROLE_WATCH_ONLY,
            "tradable": False,
            "sector": "電気機器",
            "benchmark_group": None,
            "notes": "カスタムメモ",
        }
        entry = build_symbol_entry("JP.0001", "テスト", "電気機器", estimated_price=5000, existing=existing)
        assert entry["role"] == ROLE_WATCH_ONLY
        assert entry["tradable"] is False
        assert entry["notes"] == "カスタムメモ"


def _make_symbol(code, role=ROLE_TRADE_CANDIDATE, tradable=True, sector="電気機器", estimated_price=5000):
    return {
        "code": code,
        "name": f"テスト{code}",
        "type": "stock" if sector != "ETF" else "etf",
        "role": role,
        "tradable": tradable,
        "sector": sector,
        "benchmark_group": None,
        "notes": "",
        "estimated_price": estimated_price,
    }


class TestMerge:
    """マージテスト"""

    def test_merge_adds_new(self):
        """既存になければ新規追加"""
        builder = UniverseBuilder()
        # 既存2件
        existing = {"JP.001": _make_symbol("JP.001")}
        # 候補3件（うち1件は既存と重複）
        candidates = [_make_symbol("JP.001"), _make_symbol("JP.002", sector="化学"), _make_symbol("JP.003", sector="銀行")]
        merged = builder.merge(existing, candidates)
        assert len(merged) == 3
        codes = [s["code"] for s in merged]
        assert "JP.001" in codes
        assert "JP.002" in codes
        assert "JP.003" in codes

    def test_existing_role_preserved(self):
        """既存のroleが優先される"""
        builder = UniverseBuilder()
        existing_code = "JP.001"
        existing = {existing_code: _make_symbol(existing_code, role=ROLE_WATCH_ONLY)}
        # 候補側ではtrade_candidate
        candidates = [_make_symbol(existing_code)]
        merged = builder.merge(existing, candidates)
        result = {s["code"]: s for s in merged}
        assert result[existing_code]["role"] == ROLE_WATCH_ONLY

    def test_existing_tradable_preserved(self):
        """既存のtradableが優先される"""
        builder = UniverseBuilder()
        existing_code = "JP.001"
        existing = {existing_code: _make_symbol(existing_code, tradable=False)}
        candidates = [_make_symbol(existing_code, tradable=True)]
        merged = builder.merge(existing, candidates)
        result = {s["code"]: s for s in merged}
        assert result[existing_code]["tradable"] is False

    def test_existing_notes_preserved(self):
        """既存のnotesが優先される"""
        builder = UniverseBuilder()
        existing_code = "JP.001"
        existing = {existing_code:
            {**_make_symbol(existing_code), "notes": "カスタムメモ"}}
        candidates = [_make_symbol(existing_code)]
        merged = builder.merge(existing, candidates)
        result = {s["code"]: s for s in merged}
        assert result[existing_code]["notes"] == "カスタムメモ"


class TestLoadExisting:
    """既存ファイル読み込みテスト"""

    def test_load_existing_file(self):
        """JSONファイルを正しく読み込める"""
        symbols = [
            {"code": "JP.001", "name": "テスト1", "type": "stock", "role": ROLE_TRADE_CANDIDATE, "tradable": True, "sector": "電気機器"},
            {"code": "JP.002", "name": "テスト2", "type": "stock", "role": ROLE_WATCH_ONLY, "tradable": False, "sector": "化学"},
        ]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(symbols, f, ensure_ascii=False)
            temp_path = f.name
        try:
            builder = UniverseBuilder()
            result = builder.load_existing(temp_path)
            assert len(result) == 2
            assert "JP.001" in result
            assert result["JP.002"]["role"] == ROLE_WATCH_ONLY
        finally:
            os.unlink(temp_path)

    def test_load_nonexistent(self):
        """存在しないファイルは空dict"""
        builder = UniverseBuilder()
        result = builder.load_existing("nonexistent.json")
        assert result == {}

    def test_load_empty_json(self):
        """空のJSON配列は空dict"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            f.write("[]")
            temp_path = f.name
        try:
            builder = UniverseBuilder()
            result = builder.load_existing(temp_path)
            assert result == {}
        finally:
            os.unlink(temp_path)


class TestETFAvoidance:
    """ETFがstock扱いにならないテスト"""

    def test_etf_not_stock_in_defaults(self):
        """デフォルト候補のETFはtype=etf"""
        builder = UniverseBuilder()
        candidates = builder.generate_candidates(500)
        etfs = [c for c in candidates if c["sector"] == "ETF"]
        assert len(etfs) > 0
        for etf in etfs:
            assert etf.get("type") == "etf", f"ETF {etf['code']} がtype=stockです"

    def test_etf_not_stock_after_merge(self):
        """マージ後もETFはtype=etf"""
        builder = UniverseBuilder()
        existing = {}
        candidates = builder.generate_candidates(500)
        merged = builder.merge(existing, candidates)
        etfs = [s for s in merged if s.get("sector") == "ETF"]
        assert len(etfs) > 0
        for etf in etfs:
            assert etf.get("type") == "etf", f"ETF {etf['code']} がtype=stockです"


class TestSectorCounts:
    """セクター集計テスト"""

    def test_sector_counts_accuracy(self):
        """sector別件数が正しい"""
        builder = UniverseBuilder()
        candidates = builder.generate_candidates(300)
        merged = builder.merge({}, candidates)

        # セクターを集計
        sector_counts = {}
        for s in merged:
            sector = s.get("sector", "unknown")
            sector_counts[sector] = sector_counts.get(sector, 0) + 1

        # 最低8セクターある
        assert len(sector_counts) >= 8

        # 最大セクターが50%を超えない
        max_pct = max(sector_counts.values()) / len(merged) * 100
        assert max_pct < 50

        # ETFセクターが存在する
        assert "ETF" in sector_counts


class TestEmptyInput:
    """空入力テスト"""

    def test_empty_candidates(self):
        """空の候補リストでもクラッシュしない"""
        builder = UniverseBuilder()
        existing = {}
        symbols = builder.merge(existing, [])
        assert symbols == []

    def test_empty_existing(self):
        """既存が空でも動作する"""
        builder = UniverseBuilder()
        candidates = [_make_symbol("JP.001")]
        symbols = builder.merge({}, candidates)
        assert len(symbols) == 1

    def test_nonexistent_existing_path(self):
        """存在しない既存パスでも動作する"""
        builder = UniverseBuilder(existing_path="nonexistent.json")
        existing = builder.load_existing()
        assert existing == {}


class TestCIAllPass:
    """CI全件パス確認"""

    def test_ci_generate_works(self):
        """CI環境でgenerate_candidatesが動作する"""
        builder = UniverseBuilder()
        candidates = builder.generate_candidates(100)
        assert len(candidates) == 100

    def test_ci_merge_works(self):
        """CI環境でmergeが動作する"""
        builder = UniverseBuilder()
        existing = {"JP.001": _make_symbol("JP.001")}
        candidates = [_make_symbol("JP.001"), _make_symbol("JP.002", sector="化学")]
        merged = builder.merge(existing, candidates)
        assert len(merged) == 2

    def test_ci_save_and_load(self):
        """CI環境でsave→loadが動作する"""
        builder = UniverseBuilder()
        symbols = [_make_symbol("JP.001")]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            temp_path = f.name
        try:
            builder.save(symbols, temp_path)
            loaded = builder.load_existing(temp_path)
            assert len(loaded) == 1
            assert "JP.001" in loaded
        finally:
            os.unlink(temp_path)


class TestRoleCountOutput:
    """role別件数出力テスト"""

    def test_print_diagnostics_has_counts(self, capsys):
        """print_diagnosticsが正しい集計を表示する"""
        builder = UniverseBuilder()
        symbols = [
            _make_symbol("JP.001"),
            _make_symbol("JP.002", role=ROLE_WATCH_ONLY, tradable=False, estimated_price=35000),
            _make_symbol("JP.003", sector="ETF", role=ROLE_BENCHMARK, tradable=False, estimated_price=2000),
        ]
        builder.print_diagnostics(symbols)
        captured = capsys.readouterr()
        assert ROLE_TRADE_CANDIDATE in captured.out
        assert ROLE_WATCH_ONLY in captured.out
        assert ROLE_BENCHMARK in captured.out


class TestExportCSV:
    """CSV出力テスト"""

    def test_export_csv_creates_file(self):
        """CSV出力でファイルが作成される"""
        builder = UniverseBuilder()
        symbols = [_make_symbol("JP.001")]
        with tempfile.TemporaryDirectory() as tmpdir:
            path = builder.export_summary_csv(symbols, tmpdir)
            assert os.path.exists(path)
            with open(path, encoding="utf-8-sig") as f:
                content = f.read()
            assert "total_symbols" in content
            assert "1" in content  # 1銘柄分

    def test_export_csv_counts(self):
        """CSVのrole別件数が正しい"""
        builder = UniverseBuilder()
        symbols = [
            _make_symbol("JP.001"),
            _make_symbol("JP.002", role=ROLE_WATCH_ONLY, tradable=False, estimated_price=35000, sector="化学"),
            _make_symbol("JP.003", sector="ETF", role=ROLE_BENCHMARK, tradable=False, estimated_price=2000),
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            path = builder.export_summary_csv(symbols, tmpdir)
            with open(path, encoding="utf-8-sig") as f:
                import csv
                reader = csv.reader(f)
                rows = {r[0]: r[1] for r in reader}
            assert int(rows["total_symbols"]) == 3
            assert int(rows["role:trade_candidate"]) == 1
            assert int(rows["role:watch_only"]) == 1
            assert int(rows["role:benchmark"]) == 1
