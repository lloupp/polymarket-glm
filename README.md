# polymarket-glm

> Polymarket trading framework — signal engine, risk management, paper/live execution

A fully modular Python framework for trading on [Polymarket](https://polymarket.com) prediction markets. Built with TDD, Pydantic v2, and async-first architecture.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Interface Layer                       │
│              CLI (pglm) · Dashboard                      │
├─────────────────────────────────────────────────────────┤
│                      Engine                              │
│         Orchestrator: signal → risk → exec → store       │
├──────────┬──────────┬──────────┬──────────┬─────────────┤
│ Ingestion│ Strategy │  Risk    │Execution │  Storage     │
│          │          │          │          │              │
│ Market   │ Signal   │  Risk    │ Exchange │  SQLite      │
│ Fetcher  │ Engine   │Controller│ Protocol │  Database    │
│ (Gamma)  │(edge+    │(limits,  │ Paper    │              │
│          │ Kelly)   │ CB, kill)│ Live     │              │
│ Price    │          │          │ (CLOB)   │              │
│ Feed     │          │          │          │              │
│(REST+WS) │          │          │          │              │
├──────────┴──────────┴──────────┴──────────┴─────────────┤
│                   Monitoring                             │
│           AlertManager · Telegram Push                   │
└─────────────────────────────────────────────────────────┘
```

## Quick Start

```bash
# 1. Clone and install
git clone https://github.com/lloupp/polymarket-glm.git
cd polymarket-glm
pip install -e ".[dev]"

# 2. Configure environment
cp .env.example .env
# Edit .env with your settings (paper mode works out of the box)

# 3. Run tests
pytest -q

# 4. Start paper trading
pglm --mode paper scan
```

## CLI Commands

```bash
pglm status          # Show config, balance, positions
pglm scan            # Discover markets via Gamma API
pglm trade           # Process a single signal
pglm risk            # Show risk status (exposure, drawdown, kill switch)
pglm killswitch      # Emergency stop — cancels all orders
```

## Modules

### Ingestion
- **MarketFetcher** — Gamma API wrapper with filtering (sport exclusion, keyword match, min volume)
- **PriceFeed** — REST polling + WebSocket scaffold with in-memory cache and callbacks

### Strategy
- **SignalEngine** — Edge calculation (`|estimated_prob - market_price|`) + fractional Kelly position sizing

### Risk
- **RiskController** — Per-trade and total exposure limits, daily loss cap, drawdown circuit-breaker with arm period, manual + automatic kill switch

### Execution
- **ExchangeClient Protocol** — Same interface for paper and live trading
- **PaperExecutor** — Fill simulation with real Polymarket fee calculation, position tracking, balance management
- **LiveExecutor** — Real CLOB integration via `py-clob-client`, API key validation, dry-run mode

### Storage
- **Database** — SQLite with 4 tables (markets, trades, signals, prices) and indexes

### Monitoring
- **AlertManager** — In-memory buffer, callback system, Telegram push support

### Interface
- **CLI** — `pglm` command with 5 subcommands
- **Dashboard** — Terminal-rendered scaffold (positions, signals, risk status)

## Configuration

All settings via environment variables (with `.env` file support):

| Variable | Default | Description |
|----------|---------|-------------|
| `PGLM_MODE` | `paper` | `paper` or `live` |
| `PGLM_MAX_TOTAL_EXPOSURE_USD` | `1000` | Max total exposure across all positions |
| `PGLM_MAX_PER_TRADE_USD` | `100` | Max per single trade |
| `PGLM_DAILY_LOSS_LIMIT_USD` | `50` | Daily loss cap |
| `PGLM_DRAWDOWN_CIRCUIT_BREAKER_PCT` | `0.20` | Drawdown % to trigger circuit-breaker |
| `PGLM_MIN_EDGE_BPS` | `200` | Minimum edge (basis points) to generate signal |
| `PGLM_KELLY_FRACTION` | `0.25` | Fractional Kelly multiplier |

See `.env.example` for the full list.

## Risk Management

The framework implements multiple safety layers:

1. **Per-trade limit** — blocks orders exceeding `max_per_trade_usd`
2. **Total exposure limit** — blocks orders that would exceed `max_total_exposure_usd`
3. **Daily loss cap** — blocks all trading after `daily_loss_limit_usd` reached
4. **Drawdown circuit-breaker** — triggers after `drawdown_circuit_breaker_pct` loss within arm period
5. **Kill switch** — manual (CLI/API) or automatic, blocks ALL orders until explicitly reset

## Development

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run tests
pytest -q

# Run specific test file
pytest tests/test_signal_engine.py -v
```

## Project Status

| Sprint | Focus | Status |
|--------|-------|--------|
| 1–5 | Core framework (7 layers) | ✅ Complete |
| 6 | Production readiness (loop, WS, validation) | 🔄 In progress |
| 7 | Probability estimator (heuristic + LLM) | 📋 Planned |
| 8 | Backtesting + validation | 📋 Planned |
| 9 | 24/7 operations (systemd, Telegram) | 📋 Planned |
| 10 | Go-live preparation | 📋 Planned |

## License

MIT
