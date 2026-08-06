# Generated research results

## US grid result validity

The committed `us_grid_*.csv` files were generated before the canonical US-grid price and accounting policy introduced on 2026-08-06.

Their numerical values are **STALE / INVALIDATED** and must not be used for investment, release, strategy-selection, or product decisions.

The invalidated generation path included material research-integrity defects:

- dividend-adjusted OHLC could be combined with separate dividend cash credits and split quantity adjustments;
- portfolio cash reservations did not consistently include all symbols and execution costs;
- the round-trip profitability threshold understated two-sided spread and slippage;
- configuration parsing was not fully fail-closed;
- run identity did not bind all market-data and configuration inputs.

A replacement result set is accepted only when all of the following are recorded together:

1. exact source commit;
2. canonical price basis `YAHOO_SPLIT_ADJUSTED_OHLC_CASH_DIVIDENDS_V2`;
3. complete data hash and date coverage;
4. symbol universe and configuration fingerprint;
5. generated artifact SHA-256 values;
6. fixed, adaptive, regime-gated, core-allocation, Buy & Hold, walk-forward, ATR sensitivity, cost sensitivity, capital-scenario, and adverse-period reruns;
7. an evidence-backed verdict produced from the regenerated data;
8. an external human or separate-agent independent review.

Until that replacement is complete, the research decision is:

`BLOCKED_RESEARCH_RESULTS_REGENERATION`

It is neither `ACCEPT`, `REJECT`, nor `PAPER_CANDIDATE`.
