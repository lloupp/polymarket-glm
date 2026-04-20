"""Configuration with Pydantic v2 — env overrides + validation + paper/live gate."""
from __future__ import annotations

import enum
from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ExecutionMode(str, enum.Enum):
    PAPER = "paper"
    LIVE = "live"


class RiskConfig(BaseModel):
    max_total_exposure_usd: float = Field(default=1500.0, gt=0)
    max_per_market_exposure_usd: float = Field(default=1000.0, gt=0)
    max_per_trade_usd: float = Field(default=500.0, gt=0)
    daily_loss_limit_usd: float = Field(default=200.0, gt=0)
    drawdown_circuit_breaker_pct: float = Field(default=0.20, gt=0, lt=1)
    kill_switch_cooldown_sec: float = Field(default=900.0, gt=0)
    drawdown_arm_period_sec: float = Field(default=1800.0, gt=0)
    drawdown_min_observations: int = Field(default=3, ge=1)


class ClobConfig(BaseModel):
    api_key: str = ""
    api_secret: str = ""
    api_passphrase: str = ""
    private_key: str = ""
    chain_id: int = 137  # Polygon mainnet
    clob_url: str = "https://clob.polymarket.com"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PGLM_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    execution_mode: ExecutionMode = ExecutionMode.PAPER
    risk: RiskConfig = RiskConfig()
    clob: ClobConfig = ClobConfig()
    paper_balance_usd: float = Field(default=10_000.0, gt=0)
    log_level: str = Field(default="INFO")
    telegram_alert_chat_id: str = ""
    telegram_alert_token: str = ""

    # Flat env vars for CLOB keys (since pydantic-settings with nested models
    # can be tricky — these override clob.* values if set)
    clob_api_key: str = ""
    clob_api_secret: str = ""
    clob_api_passphrase: str = ""
    private_key: str = ""

    @model_validator(mode="after")
    def _merge_flat_clob(self) -> "Settings":
        if self.clob_api_key:
            self.clob.api_key = self.clob_api_key
        if self.clob_api_secret:
            self.clob.api_secret = self.clob_api_secret
        if self.clob_api_passphrase:
            self.clob.api_passphrase = self.clob_api_passphrase
        if self.private_key:
            self.clob.private_key = self.private_key
        return self

    @property
    def live_ready(self) -> bool:
        return all([
            self.clob.api_key,
            self.clob.api_secret,
            self.clob.api_passphrase,
            self.clob.private_key,
        ])
