# Go-Live Checklist & Runbook

## Pre-Launch Checklist

### 1. Environment
- [ ] `.env` configured with:
  - `TELEGRAM_BOT_TOKEN` — set
  - `CLOB_API_KEY` — set (when available)
  - `CLOB_API_SECRET` — set (when available)
  - `CLOB_API_PASSPHRASE` — set (when available)
  - `PRIVATE_KEY` — set (when available)
- [ ] Python 3.11+ installed and venv active
- [ ] All dependencies installed: `pip install -e .`
- [ ] All tests green: `pytest -q` → 283 passed

### 2. Safety Checks
- [ ] `RiskConfig` defaults are conservative:
  - `max_total_exposure_usd = 500.0`
  - `max_per_market_exposure_usd = 200.0`
  - `max_per_trade_usd = 50.0`
  - `daily_loss_limit_usd = 30.0`
  - `drawdown_circuit_breaker_pct = 0.10`
- [ ] Kill switch works: `/killswitch` in Telegram bot
- [ ] Health check loop detection active
- [ ] Web dashboard accessible via Tailscale

### 3. Mode Selection
- [ ] `ExecutionMode.PAPER` → no real API calls (default)
- [ ] `ExecutionMode.LIVE` with `dry_run=True` → simulates CLOB API
- [ ] `ExecutionMode.LIVE` with `dry_run=False` → **REAL MONEY** (requires API keys)

### 4. Monitoring
- [ ] Telegram bot responds to `/status`, `/risk`, `/positions`
- [ ] Web dashboard on `http://<tailscale-ip>:8080/health`
- [ ] systemd service unit generated and ready

---

## Runbook: Starting the System

### Dry-Run Mode (No API Keys Required)

```bash
cd /home/ubuntu/polymarket-glm
source .venv/bin/activate

# 1. Set Telegram bot token
export TELEGRAM_BOT_TOKEN="your-token-here"

# 2. Run in dry-run mode (no real trades, simulated CLOB)
python -m polymarket_glm.engine \
  --mode live \
  --dry-run \
  --poll-interval 60
```

### Paper Mode (Default)

```bash
# Paper mode = no CLOB API calls at all, just signal generation + logging
python -m polymarket_glm.engine --mode paper
```

### Live Mode (REAL MONEY — Requires API Keys)

```bash
# ⚠️  ONLY AFTER CLOB API KEYS ARE CONFIGURED AND FUNDED
export CLOB_API_KEY="..."
export CLOB_API_SECRET="..."
export CLOB_API_PASSPHRASE="..."
export PRIVATE_KEY="0x..."

python -m polymarket_glm.engine --mode live --poll-interval 60
```

---

## Runbook: Emergency Procedures

### Kill Switch Activation

**Via Telegram:**
```
/killswitch
```

**Via code:**
```python
from polymarket_glm.risk.controller import RiskController
risk = RiskController()
risk.activate_kill_switch("manual emergency stop")
```

**What happens:**
- All new trades are blocked with `RiskVerdict.KILL_SWITCH`
- Cooldown period: 3600 seconds (1 hour) by default
- After cooldown, trading resumes automatically

### Health Check Failure

If the system detects a stuck loop (>5 min no heartbeat):
1. Auto-recovery attempts restart
2. Telegram alert sent via `/status`
3. Web dashboard shows `unhealthy` state

### Daily Loss Limit Hit

When `daily_loss_limit_usd` is reached:
- All trades blocked with `RiskVerdict.DENY_DAILY_LIMIT`
- Resets at midnight UTC via `risk.reset_daily()`

---

## Runbook: Monitoring Commands (Telegram)

| Command | Description |
|---------|-------------|
| `/status` | System health, uptime, last poll time |
| `/risk` | Current risk state, exposure, daily loss |
| `/killswitch` | Activate emergency kill switch |
| `/positions` | Current open positions and exposure |

---

## Risk Parameters Reference

| Parameter | Default | Description |
|-----------|---------|-------------|
| `max_total_exposure_usd` | $500 | Max total capital across all markets |
| `max_per_market_exposure_usd` | $200 | Max exposure per single market |
| `max_per_trade_usd` | $50 | Max single trade size |
| `daily_loss_limit_usd` | $30 | Max daily loss before halt |
| `drawdown_circuit_breaker_pct` | 10% | Balance drawdown % triggers kill switch |
| `kill_switch_cooldown_sec` | 3600 | Kill switch cooldown before re-enabling |

---

## Deployment as systemd Service

```bash
# Generate service unit file
python -c "
from polymarket_glm.ops.service import ServiceConfig
svc = ServiceConfig(name='polymarket-glm', working_dir='/home/ubuntu/polymarket-glm')
print(svc.render_unit())
" > /tmp/polymarket-glm.service

# Install and enable
sudo mv /tmp/polymarket-glm.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable polymarket-glm
sudo systemctl start polymarket-glm

# Check status
sudo systemctl status polymarket-glm
```

---

## Post-Launch Validation

1. ✅ All 283 tests pass
2. ✅ Telegram bot responds to all commands
3. ✅ Dry-run pipeline: signal → risk → execution works
4. ✅ Kill switch blocks trades in dry-run
5. ✅ Daily loss limit blocks trades
6. ✅ Web dashboard accessible
7. ✅ Health check detects stuck loops

---

## CLOB API Key Setup

See `docs/CLOB_API_SETUP.md` for step-by-step instructions to obtain and configure Polymarket CLOB API credentials.
