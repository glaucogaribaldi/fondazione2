import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True)
class ExecutionSettings:
    fee_bps: float = 60
    slippage_bps: float = 5


class PaperLedger:
    def __init__(self, path: str, lane_ids: list[str], initial_capital: float):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self._initialize(lane_ids, initial_capital)

    def _initialize(self, lane_ids: list[str], initial_capital: float) -> None:
        with self.lock, self.connection:
            self.connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                PRAGMA foreign_keys=ON;
                CREATE TABLE IF NOT EXISTS portfolios (
                    lane_id TEXT PRIMARY KEY,
                    initial_capital REAL NOT NULL,
                    cash REAL NOT NULL,
                    equity REAL NOT NULL,
                    peak_equity REAL NOT NULL,
                    max_drawdown_pct REAL NOT NULL DEFAULT 0,
                    realized_pnl REAL NOT NULL DEFAULT 0,
                    fees REAL NOT NULL DEFAULT 0,
                    day_date TEXT NOT NULL,
                    day_start_equity REAL NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS positions (
                    lane_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    quantity REAL NOT NULL DEFAULT 0,
                    average_price REAL NOT NULL DEFAULT 0,
                    last_price REAL NOT NULL DEFAULT 0,
                    PRIMARY KEY (lane_id, symbol),
                    FOREIGN KEY (lane_id) REFERENCES portfolios(lane_id)
                );
                CREATE TABLE IF NOT EXISTS arena_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT NOT NULL,
                    lane_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    action TEXT NOT NULL,
                    approved INTEGER NOT NULL,
                    fill_price REAL,
                    quantity REAL NOT NULL DEFAULT 0,
                    fee REAL NOT NULL DEFAULT 0,
                    reason_codes TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(request_id, lane_id)
                );
                """
            )
            now = datetime.now(UTC).isoformat()
            today = datetime.now(UTC).date().isoformat()
            columns = {
                row[1] for row in self.connection.execute("PRAGMA table_info(portfolios)")
            }
            if "day_date" not in columns:
                self.connection.execute("ALTER TABLE portfolios ADD COLUMN day_date TEXT")
                self.connection.execute(
                    "UPDATE portfolios SET day_date = ? WHERE day_date IS NULL", (today,)
                )
            if "day_start_equity" not in columns:
                self.connection.execute("ALTER TABLE portfolios ADD COLUMN day_start_equity REAL")
                self.connection.execute(
                    "UPDATE portfolios SET day_start_equity = equity WHERE day_start_equity IS NULL"
                )
            for lane_id in lane_ids:
                self.connection.execute(
                    """
                    INSERT OR IGNORE INTO portfolios
                    (lane_id, initial_capital, cash, equity, peak_equity,
                     day_date, day_start_equity, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        lane_id,
                        initial_capital,
                        initial_capital,
                        initial_capital,
                        initial_capital,
                        today,
                        initial_capital,
                        now,
                    ),
                )

    def _mark(self, lane_id: str, symbol: str, price: float) -> dict:
        self.connection.execute(
            "UPDATE positions SET last_price = ? WHERE lane_id = ? AND symbol = ?",
            (price, lane_id, symbol),
        )
        portfolio = self.connection.execute(
            "SELECT * FROM portfolios WHERE lane_id = ?", (lane_id,)
        ).fetchone()
        today = datetime.now(UTC).date().isoformat()
        if portfolio["day_date"] != today:
            self.connection.execute(
                "UPDATE portfolios SET day_date = ?, day_start_equity = equity WHERE lane_id = ?",
                (today, lane_id),
            )
            portfolio = self.connection.execute(
                "SELECT * FROM portfolios WHERE lane_id = ?", (lane_id,)
            ).fetchone()
        positions_value = self.connection.execute(
            "SELECT COALESCE(SUM(quantity * last_price), 0) FROM positions WHERE lane_id = ?",
            (lane_id,),
        ).fetchone()[0]
        equity = float(portfolio["cash"]) + float(positions_value)
        peak = max(float(portfolio["peak_equity"]), equity)
        drawdown = ((peak - equity) / peak * 100) if peak else 0
        max_drawdown = max(float(portfolio["max_drawdown_pct"]), drawdown)
        self.connection.execute(
            """
            UPDATE portfolios
            SET equity = ?, peak_equity = ?, max_drawdown_pct = ?, updated_at = ?
            WHERE lane_id = ?
            """,
            (equity, peak, max_drawdown, datetime.now(UTC).isoformat(), lane_id),
        )
        return self._portfolio(lane_id)

    def _portfolio(self, lane_id: str) -> dict:
        row = self.connection.execute(
            "SELECT * FROM portfolios WHERE lane_id = ?", (lane_id,)
        ).fetchone()
        positions = self.connection.execute(
            """
            SELECT symbol, quantity, average_price, last_price
            FROM positions WHERE lane_id = ? AND quantity > 0
            """,
            (lane_id,),
        ).fetchall()
        result = dict(row)
        result["positions"] = [dict(position) for position in positions]
        result["return_pct"] = (
            (result["equity"] / result["initial_capital"] - 1) * 100
            if result["initial_capital"]
            else 0
        )
        return result

    def snapshot(self, lane_id: str, symbol: str, price: float) -> dict:
        with self.lock, self.connection:
            portfolio = self._mark(lane_id, symbol, price)
            position = next(
                (item for item in portfolio["positions"] if item["symbol"] == symbol), None
            )
            position_value = position["quantity"] * price if position else 0
            current_position_pct = (
                position_value / portfolio["equity"] * 100 if portfolio["equity"] else 0
            )
            return {
                "equity": portfolio["equity"],
                "cash": portfolio["cash"],
                "daily_pnl_pct": (
                    (portfolio["equity"] / portfolio["day_start_equity"] - 1) * 100
                    if portfolio["day_start_equity"]
                    else 0
                ),
                "open_positions": len(portfolio["positions"]),
                "current_position_pct": current_position_pct,
                "last_trade_at": self._last_trade_at(lane_id),
            }

    def _last_trade_at(self, lane_id: str) -> str | None:
        row = self.connection.execute(
            """
            SELECT created_at FROM arena_events
            WHERE lane_id = ? AND quantity > 0
            ORDER BY id DESC LIMIT 1
            """,
            (lane_id,),
        ).fetchone()
        return row[0] if row else None

    def execute(
        self,
        request_id: str,
        lane_id: str,
        symbol: str,
        decision: dict,
        bid: float,
        ask: float,
        settings: ExecutionSettings,
    ) -> dict:
        with self.lock, self.connection:
            existing = self.connection.execute(
                "SELECT * FROM arena_events WHERE request_id = ? AND lane_id = ?",
                (request_id, lane_id),
            ).fetchone()
            if existing:
                return {
                    "event": dict(existing),
                    "portfolio": self._mark(lane_id, symbol, (bid + ask) / 2),
                }

            portfolio = self._mark(lane_id, symbol, (bid + ask) / 2)
            action = decision["decision"] if decision.get("approved_by_risk_engine") else "HOLD"
            allocation_pct = float(decision.get("allocation_pct", 0))
            fee_rate = settings.fee_bps / 10_000
            slippage_rate = settings.slippage_bps / 10_000
            fill_price = None
            quantity = 0.0
            fee = 0.0
            realized_pnl = 0.0

            position = self.connection.execute(
                "SELECT * FROM positions WHERE lane_id = ? AND symbol = ?", (lane_id, symbol)
            ).fetchone()
            held_quantity = float(position["quantity"]) if position else 0.0
            average_price = float(position["average_price"]) if position else 0.0

            if action == "BUY" and allocation_pct > 0:
                fill_price = ask * (1 + slippage_rate)
                target_notional = min(
                    portfolio["equity"] * allocation_pct / 100,
                    portfolio["cash"] / (1 + fee_rate),
                )
                quantity = target_notional / fill_price if fill_price else 0
                fee = target_notional * fee_rate
                new_quantity = held_quantity + quantity
                new_average = (
                    (held_quantity * average_price + quantity * fill_price) / new_quantity
                    if new_quantity
                    else 0
                )
                self.connection.execute(
                    "UPDATE portfolios SET cash = cash - ?, fees = fees + ? WHERE lane_id = ?",
                    (target_notional + fee, fee, lane_id),
                )
                self.connection.execute(
                    """
                    INSERT INTO positions (lane_id, symbol, quantity, average_price, last_price)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(lane_id, symbol) DO UPDATE SET
                    quantity = excluded.quantity,
                    average_price = excluded.average_price,
                    last_price = excluded.last_price
                    """,
                    (lane_id, symbol, new_quantity, new_average, fill_price),
                )
            elif action == "SELL" and allocation_pct > 0 and held_quantity > 0:
                fill_price = bid * (1 - slippage_rate)
                target_quantity = portfolio["equity"] * allocation_pct / 100 / fill_price
                quantity = min(held_quantity, target_quantity)
                proceeds = quantity * fill_price
                fee = proceeds * fee_rate
                realized_pnl = quantity * (fill_price - average_price) - fee
                remaining = held_quantity - quantity
                self.connection.execute(
                    """
                    UPDATE portfolios
                    SET cash = cash + ?, fees = fees + ?, realized_pnl = realized_pnl + ?
                    WHERE lane_id = ?
                    """,
                    (proceeds - fee, fee, realized_pnl, lane_id),
                )
                self.connection.execute(
                    """
                    UPDATE positions SET quantity = ?, last_price = ?
                    WHERE lane_id = ? AND symbol = ?
                    """,
                    (remaining, fill_price, lane_id, symbol),
                )
            elif action == "SELL":
                action = "HOLD"
                decision.setdefault("reason_codes", []).append("NO_POSITION_TO_SELL")

            event_time = datetime.now(UTC).isoformat()
            self.connection.execute(
                """
                INSERT INTO arena_events
                (request_id, lane_id, symbol, action, approved, fill_price,
                 quantity, fee, reason_codes, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request_id,
                    lane_id,
                    symbol,
                    action,
                    int(bool(decision.get("approved_by_risk_engine"))),
                    fill_price,
                    quantity,
                    fee,
                    json.dumps(decision.get("reason_codes", [])),
                    event_time,
                ),
            )
            event = self.connection.execute(
                "SELECT * FROM arena_events WHERE request_id = ? AND lane_id = ?",
                (request_id, lane_id),
            ).fetchone()
            updated = self._mark(lane_id, symbol, (bid + ask) / 2)
            return {"event": dict(event), "portfolio": updated, "realized_pnl": realized_pnl}

    def ranking(self) -> list[dict]:
        with self.lock:
            rows = [
                self._portfolio(row[0])
                for row in self.connection.execute("SELECT lane_id FROM portfolios")
            ]
            for row in rows:
                fee_pct = row["fees"] / row["initial_capital"] * 100
                row["score"] = row["return_pct"] - 2 * row["max_drawdown_pct"] - fee_pct
            rows.sort(key=lambda item: item["score"], reverse=True)
            return [{"rank": index, **row} for index, row in enumerate(rows, start=1)]

    def events(self, limit: int = 100) -> list[dict]:
        with self.lock:
            rows = self.connection.execute(
                "SELECT * FROM arena_events ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(row) for row in rows]
