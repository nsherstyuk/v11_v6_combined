# Migrate `nautilus0/data/` from Dropbox into the Mac repo

**Date:** 2026-05-10
**Status:** Done. 145/145 files copied, 315 MB / 315 MB integrity match.

## What landed

Copied three subtrees + top-level tooling from
`~/Library/CloudStorage/Dropbox/Nick/movingFromWin/nautilus0/data/`
into `~/code/ibkr_grok_wing_agent/data/`, preserving the original
Nautilus folder layout:

```
data/
├── ticks/                                  ← pre-existing, untouched
│   └── XAUUSD/                             ← v11 live tick logs
├── historical/                             ← NEW (~313 MB)
│   ├── EUR-USD_*_MINUTE_MID_EXTERNAL.csv   ← 6 EURUSD bar resolutions
│   ├── GBP-USD_*_MINUTE_MID_EXTERNAL.csv   ← 6 GBPUSD bar resolutions
│   ├── USD-CHF_*_MINUTE_MID_EXTERNAL.csv   ← 6 USDCHF bar resolutions
│   └── data/                               ← Nautilus parquet catalog
│       ├── bar/        (91 parquet files, ~220 MB)
│       ├── currency_pair/
│       └── equity/
├── catalog/                                ← NEW (~1.7 MB) Nautilus catalog
├── test_catalog/                           ← NEW (~280 KB)
├── *.py                                    ← Nautilus tooling at root
├── __init__.py
└── README.md                               ← NEW — provenance + format notes
```

`.gitignore` extended to cover `data/historical/`, `data/catalog/`,
`data/test_catalog/`. The `data/*.py` files (small, ~110 KB total) are
tracked in case someone wants to inspect / port the Nautilus tooling.

## Why this matters — and what it ISN'T

Nick wanted Windows-era research data accessible on Mac without
disrupting the active equity download writing to
`tick_vault_data/us_equities/`. The Dropbox folder is the canonical
backup of that data; the Mac copy is a working replica.

**This migrated dataset is NOT the same as `eurusd_1m_tick.csv`** that
was repaired in `docs/journal/2026-04-18_eurusd_repair_and_strategy_reverification.md`.
That file was Dukascopy tick-aggregated (with `tick_count`, `avg_spread`,
`buy_volume`, etc.) and lived at Windows `C:\nautilus0\data\1m_csv\`.
The Dropbox/data/ migration here is IBKR-MID bars (raw `o,h,l,c,volume`
with volume=0 since IBKR doesn't publish FX volume). Different shape,
different framework, not a drop-in for `v11/backtest/data_loader.py`.
v11 backtests expecting the tick-aggregated columns will need an
adapter shim or a separate canonical-data decision before this folder
becomes usable.

## What I did NOT touch

- `tick_vault_data/us_equities/` — active download writing here, PID
  67037, ~14 hr to completion. Verified alive before, during, and
  after the copy.
- `data/ticks/XAUUSD/` — v11 live tick-logging output, runtime-generated.
- v11 source code, `.env`, `~/ibc/config.ini`. None touched.

## Watchpoints

- **Don't auto-merge with `tick_vault_data/`.** The `tick_vault_data/`
  namespace is reserved for the `tick_vault` library's `.bi5` cache
  (per .gitignore comment) and the recent IBKR-equities CSV output.
  Mixing Nautilus parquets in there would muddy the namespace.
- **Repopulation recipe in `data/README.md`** if any of the gitignored
  content disappears. Dropbox is the authoritative source.
- **No backtest is wired against this folder yet.** Loading these
  CSVs into v11's backtest pipeline requires a column-mapping adapter
  (the file shape is IBKR-MID, not Dukascopy ticks). Out of scope for
  this migration; surface as future work if/when FX research resumes
  on Mac.
