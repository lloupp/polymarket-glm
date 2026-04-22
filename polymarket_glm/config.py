"""Configuration with Pydantic v2 — env overrides + validation + paper/live gate."""
from __future__ import annotations

import enum
from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ExecutionMode(str, enum.Enum):
    PAPER = "paper"
    LIVE = "live"


class RiskConfig(BaseModel):
    max_total_exposure_usd: float = Field(default=500.0, gt=0)
    max_per_market_exposure_usd: float = Field(default=200.0, gt=0)
    max_per_trade_usd: float = Field(default=50.0, gt=0)
    daily_loss_limit_usd: float = Field(default=30.0, gt=0)
    drawdown_circuit_breaker_pct: float = Field(default=0.10, gt=0, lt=1)
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


class LLMRouterConfig(BaseModel):
    """Configuration for the LLM router (loaded from env vars).

    Env vars use PGLM_LLM_ROUTER__ prefix (e.g. PGLM_LLM_ROUTER__GROQ_API_KEY).
    """
    groq_api_key: str = ""
    groq_base_url: str = "https://api.groq.com/openai/v1"
    groq_model: str = "llama-3.3-70b-versatile"
    groq_rpm: int = 30

    gemini_api_key: str = ""
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai"
    gemini_model: str = "gemini-2.5-flash"
    gemini_rpm: int = 10

    github_api_key: str = ""
    github_base_url: str = "https://models.github.ai/inference"
    github_model: str = "gpt-4.1-mini"
    github_rpm: int = 15

    cerebras_api_key: str = ""
    cerebras_base_url: str = "https://api.cerebras.ai/v1"
    cerebras_model: str = "llama-3.3-70b"
    cerebras_rpm: int = 30

    mistral_api_key: str = ""
    mistral_base_url: str = "https://api.mistral.ai/v1"
    mistral_model: str = "mistral-small-latest"
    mistral_rpm: int = 60

    enabled: bool = True
    max_retries_per_provider: int = 2
    timeout_sec: float = 30.0
    temperature: float = 0.1
    max_tokens: int = 150

    @property
    def active_providers(self) -> int:
        """Count of providers with API keys configured."""
        return sum(1 for k in [
            self.groq_api_key, self.gemini_api_key, self.github_api_key,
            self.cerebras_api_key, self.mistral_api_key,
        ] if k)


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
    llm_router: LLMRouterConfig = LLMRouterConfig()
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
