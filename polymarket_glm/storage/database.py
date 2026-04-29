"""Storage layer — SQLite database for trades, signals, prices, and markets."""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS markets (
    condition_id TEXT NOT NULL,
    market_id TEXT PRIMARY KEY,
    question TEXT,
    outcomes TEXT,
    outcome_prices TEXT,
    tokens TEXT,
    active INTEGER DEFAULT 1,
    closed INTEGER DEFAULT 0,
    neg_risk INTEGER DEFAULT 0,
    volume REAL DEFAULT 0,
    slug TEXT DEFAULT '',
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS trades (
    trade_id TEXT PRIMARY KEY,
    market_id TEXT NOT NULL,
    side TEXT NOT NULL,
    outcome TEXT NOT NULL,
    price REAL,
    size REAL,
    fee REAL DEFAULT 0,
    total_cost REAL DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (market_id) REFERENCES markets(market_id)
);

CREATE TABLE IF NOT EXISTS signals (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 market_id TEXT NOT NULL,
 signal_type TEXT NOT NULL,
 edge REAL,
 estimated_prob REAL,
 market_price REAL,
 size_usd REAL,
 kelly_raw REAL DEFAULT 0,
 kelly_sized REAL DEFAULT 0,
 outcome TEXT DEFAULT NULL,
 confidence TEXT DEFAULT NULL,
 ev REAL DEFAULT NULL,
 created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS audit_log (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 market_id TEXT NOT NULL,
 question TEXT DEFAULT '',
 decision TEXT NOT NULL,
 reason TEXT DEFAULT '',
 signal_type TEXT DEFAULT '',
 edge REAL DEFAULT 0,
 estimated_prob REAL DEFAULT 0,
 market_price REAL DEFAULT 0,
 confidence TEXT DEFAULT NULL,
 ev REAL DEFAULT NULL,
 risk_verdict TEXT DEFAULT '',
 risk_reason TEXT DEFAULT '',
 portfolio_cash REAL DEFAULT 0,
 portfolio_positions_value REAL DEFAULT 0,
 portfolio_total REAL DEFAULT 0,
 context_available INTEGER DEFAULT 0,
 created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS prices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id TEXT NOT NULL,
    outcome TEXT NOT NULL,
    price REAL,
    volume REAL DEFAULT 0,
    timestamp TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_trades_market ON trades(market_id);
CREATE INDEX IF NOT EXISTS idx_signals_market ON signals(market_id);
CREATE INDEX IF NOT EXISTS idx_prices_market ON prices(market_id);
CREATE INDEX IF NOT EXISTS idx_audit_market ON audit_log(market_id);
CREATE INDEX IF NOT EXISTS idx_audit_decision ON audit_log(decision);
"""


class Database:
    """SQLite storage for polymarket-glm data."""

    def __init__(self, path: str = "polymarket_glm.db"):
        self._path = path
        self._conn: sqlite3.Connection | None = None

    def initialize(self) -> None:
        """Open connection and create tables."""
        self._conn = sqlite3.connect(self._path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        logger.info("Database initialized: %s", self._path)

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def _ensure_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self.initialize()
        return self._conn

    # ── Markets ─────────────────────────────────────────────────

    def save_market(self, *, condition_id: str, market_id: str, question: str,
                    outcomes: str, outcome_prices: str, tokens: str,
                    volume: float = 0, **kwargs: Any) -> None:
        conn = self._ensure_conn()
        conn.execute("""
            INSERT OR REPLACE INTO markets
            (condition_id, market_id, question, outcomes, outcome_prices, tokens, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (condition_id, market_id, question, outcomes, outcome_prices, tokens, volume))
        conn.commit()

    def get_markets(self, *, limit: int = 100, offset: int = 0) -> list[dict]:
        conn = self._ensure_conn()
        rows = conn.execute(
            "SELECT * FROM markets ORDER BY volume DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Trades ──────────────────────────────────────────────────

    def save_trade(self, *, trade_id: str, market_id: str, side: str,
                   outcome: str, price: float, size: float,
                   fee: float = 0, **kwargs: Any) -> None:
        conn = self._ensure_conn()
        total_cost = price * size + fee
        conn.execute("""
            INSERT OR IGNORE INTO trades
            (trade_id, market_id, side, outcome, price, size, fee, total_cost)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (trade_id, market_id, side, outcome, price, size, fee, total_cost))
        conn.commit()

    def get_trades(self, market_id: str | None = None, *,
                   limit: int = 100) -> list[dict]:
        conn = self._ensure_conn()
        if market_id:
            rows = conn.execute(
                "SELECT * FROM trades WHERE market_id = ? ORDER BY created_at DESC LIMIT ?",
                (market_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM trades ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Signals ─────────────────────────────────────────────────

 def save_signal(self, *, market_id: str, signal_type: str, edge: float,
 estimated_prob: float, market_price: float, size_usd: float,
 kelly_raw: float = 0, kelly_sized: float = 0,
 outcome: str | None = None,
 confidence: str | None = None,
 ev: float | None = None,
 **kwargs: Any) -> None:
 conn = self._ensure_conn()
 conn.execute("""
 INSERT INTO signals
 (market_id, signal_type, edge, estimated_prob, market_price,
 size_usd, kelly_raw, kelly_sized, outcome, confidence, ev)
 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
 """, (market_id, signal_type, edge, estimated_prob, market_price,
 size_usd, kelly_raw, kelly_sized, outcome, confidence, ev))
 conn.commit()

    def get_signals(self, market_id: str | None = None, *,
                    limit: int = 100) -> list[dict]:
        conn = self._ensure_conn()
        if market_id:
            rows = conn.execute(
                "SELECT * FROM signals WHERE market_id = ? ORDER BY created_at DESC LIMIT ?",
                (market_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM signals ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Prices ──────────────────────────────────────────────────

    def save_price(self, *, market_id: str, outcome: str,
                   price: float, volume: float = 0) -> None:
        conn = self._ensure_conn()
        conn.execute("""
            INSERT INTO prices (market_id, outcome, price, volume)
            VALUES (?, ?, ?, ?)
        """, (market_id, outcome, price, volume))
        conn.commit()

    def get_prices(self, market_id: str, *,
                   limit: int = 100) -> list[dict]:
        conn = self._ensure_conn()
        rows = conn.execute(
            "SELECT * FROM prices WHERE market_id = ? ORDER BY timestamp DESC LIMIT ?",
            (market_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]
