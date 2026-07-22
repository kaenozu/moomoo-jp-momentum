# P2 Baseline Record

**Date**: 2026-07-15
**Purpose**: 現行設定を固定し、再現可能なベースラインとして記録

## Git State

- Branch: fix/self-contained-test-environment
- Latest commit: c2fc4ed (P1完了、バックテスト結果更新)
- Uncommitted changes: None (P1 changes committed)

## Configuration

### config.yaml (backtest section)

```yaml
backtest:
  stop_loss_pct: 8.0
  max_positions: 30
  idle_cash_allocation:
    enabled: true
    benchmark_code: "US.SPY"
```

### Strategy Settings

- momentum: volume_ratio_percentile, return_5d, close_vs_ma25, ma5_vs_ma25
- quality_low_risk: volatility_20d, return_20d, close_vs_ma25, volume_ratio_percentile

### Execution Settings

- Signal generation: Daily at market close
- Order fill: T+1 next day open
- Stop loss: -8% from entry price
- Trailing stop: Activated after +5% gain

## Database State

- Total symbols: 871
- Enabled symbols: 851 (20 delisted excluded)
- Daily_bars codes: 851 (100% coverage)
- Moomoo sourced: 207 codes
- Yfinance sourced: 851 codes (all have yfinance data)
- Latest data date: 2026-07-09

## Baseline Backtest Results

### Period: 2026-05-21 ~ 2026-06-30 (40 trading days)

| Strategy | Return | vs2559 | vs1306 | Trades | Stop/Trail | DD% |
|---|---|---|---|---|---|---|
| momentum | +5.11% | +2.40% | +1.49% | 39 | 11 | 4.1% |
| quality_low_risk | +5.37% | +2.65% | +1.75% | 29 | 9 | N/A |
| etf_rotation | +1.60% | -1.11% | -2.02% | 10 | 4 | N/A |

### Benchmark Returns

- 2559 (TOPIX ETF): +2.71%
- 1306 (Nikkei 225 ETF): +3.62%

## Key Observations

1. Both momentum and quality_low_risk outperform benchmarks
2. momentum: 39 trades (high turnover)
3. quality_low_risk: 29 trades (lower turnover, slightly better return)
4. etf_rotation underperforms both benchmarks

## Reproduction Commands

```bash
# Backtest (same period)
uv run python historical_backtest.py --from 2026-05-21 --to 2026-06-30 --strategy momentum --csv
uv run python historical_backtest.py --from 2026-05-21 --to 2026-06-30 --strategy quality_low_risk --csv
uv run python historical_backtest.py --from 2026-05-21 --to 2026-06-30 --strategy etf_rotation --csv

# Extended period (P2-1)
uv run python historical_backtest.py --from 2026-01-01 --to 2026-06-30 --strategy momentum --csv
uv run python historical_backtest.py --from 2026-01-01 --to 2026-06-30 --strategy quality_low_risk --csv
```
