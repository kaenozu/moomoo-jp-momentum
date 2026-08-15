import json
from pathlib import Path


FIXTURE = Path(__file__).parent / "fixtures" / "v2_characterization" / "basic_baseline.json"


def load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_v1_baseline_fixture_captures_order_fill_and_ledger_trace() -> None:
    fixture = load_fixture()

    assert fixture["schema_version"] == "v2-001"
    assert fixture["execution"]["fill_mode"] == "next_day_open"
    assert fixture["orders"] == [
        {
            "id": "order-1",
            "signal_date": "2026-01-05",
            "code": "JP.TEST",
            "side": "BUY",
            "quantity": 10,
            "status": "FILLED",
        },
        {
            "id": "order-2",
            "signal_date": "2026-01-07",
            "code": "JP.TEST",
            "side": "SELL",
            "quantity": 10,
            "status": "FILLED",
        },
    ]
    assert fixture["fills"] == [
        {
            "order_id": "order-1",
            "filled_at": "2026-01-06",
            "price": 101.0,
            "quantity": 10,
        },
        {
            "order_id": "order-2",
            "filled_at": "2026-01-08",
            "price": 109.0,
            "quantity": 10,
        },
    ]
    assert fixture["ledger"]["cash"] == [100000.0, 98990.0, 99980.0]
    assert fixture["ledger"]["positions"] == [
        {"date": "2026-01-05", "code": "JP.TEST", "quantity": 0, "avg_cost": 0.0},
        {"date": "2026-01-06", "code": "JP.TEST", "quantity": 10, "avg_cost": 101.0},
        {"date": "2026-01-08", "code": "JP.TEST", "quantity": 0, "avg_cost": 0.0},
    ]
    assert fixture["equity"] == [
        {"date": "2026-01-05", "total_equity": 100000.0},
        {"date": "2026-01-06", "total_equity": 100000.0},
        {"date": "2026-01-07", "total_equity": 100080.0},
        {"date": "2026-01-08", "total_equity": 99980.0},
    ]


def test_v1_baseline_fixture_metrics_are_not_return_only() -> None:
    metrics = load_fixture()["metrics"]

    assert set(metrics) == {
        "cagr",
        "excess_cagr",
        "sharpe",
        "sortino",
        "max_drawdown",
        "calmar",
        "turnover",
        "exposure",
    }
    assert metrics["turnover"] == 0.02
    assert metrics["exposure"] == 0.5

