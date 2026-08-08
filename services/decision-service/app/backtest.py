import os
import sqlite3
import psycopg2
import hashlib
import json
import uuid
import random
from datetime import datetime, UTC, timedelta
from typing import List, Dict, Any, Optional
from .products import registry, get_product_mapping
from .executor import DatabaseConnection
from .models import DecisionRequest, PortfolioSnapshot, MarketSnapshot, Candle, ExecutionIntent, DecisionResponse, Proposal
from .risk import evaluate_risk
from .config import load_risk_settings, load_lane_settings

def get_current_code_sha() -> str:
    import subprocess
    try:
        if os.environ.get("CODE_SHA"):
            return os.environ.get("CODE_SHA")
        root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        return subprocess.check_output(["git", "-C", root_dir, "rev-parse", "HEAD"], stderr=subprocess.DEVNULL).decode("utf-8").strip()
    except Exception:
        return "5987ba424f61edb2752a4e85046213693414c9e7"

class HistoricalDataset:
    """
    Immutable Dataset Contract (Blocker F / B4).
    Guarantees strict no-lookahead by filtering observations >= T (Replay Clock time).
    """
    def __init__(
        self,
        canonical_symbols: List[str],
        timeframe: int,
        start_time: datetime,
        end_time: datetime,
        as_of: datetime,
        candles_list: List[dict],
        code_sha: str = "unknown",
        config_hash: str = "v1"
    ):
        self.canonical_symbols = sorted(canonical_symbols)
        self.timeframe = timeframe
        self.start_time = start_time
        self.end_time = end_time
        self.as_of = as_of
        self.candles = candles_list
        self.dataset_hash = self._calculate_dataset_hash()

        # Build deterministic dataset_id (Blocker B4)
        metadata_str = (
            f"{self.dataset_hash}|"
            f"v1|"  # universe_version
            f"{','.join(self.canonical_symbols)}|"
            f"{self.timeframe}|"
            f"{self.start_time.isoformat()}|"
            f"{self.end_time.isoformat()}|"
            f"{self.as_of.isoformat()}|"
            f"v1|"  # preprocessing_version
            f"{config_hash}|"
            f"{code_sha}"
        )
        self.dataset_id = f"ds-{hashlib.sha256(metadata_str.encode('utf-8')).hexdigest()[:16]}"

    def _calculate_dataset_hash(self) -> str:
        # Generate a deterministic hash based on observations, parameters, and metadata
        candle_data = [
            f"{c['product_id']}_{c['candle_open']}_{c['open']}_{c['high']}_{c['low']}_{c['close']}_{c['volume']}"
            for c in self.candles
        ]
        raw_str = "|".join(candle_data) + f"|{self.timeframe}|{self.start_time.isoformat()}|{self.end_time.isoformat()}"
        return hashlib.sha256(raw_str.encode("utf-8")).hexdigest()

    def get_as_of(self, t: datetime) -> List[dict]:
        """
        Hard No-Lookahead Accessor (Blocker F): rejects or excludes any observations with timestamp >= T.
        """
        filtered = []
        for c in self.candles:
            try:
                c_open = datetime.fromisoformat(c["candle_open"])
                if c_open.tzinfo is None:
                    c_open = c_open.replace(tzinfo=UTC)
            except Exception:
                continue

            # Hard constraint check: candle open must be strictly before current clock time T (No lookahead!)
            if c_open < t:
                filtered.append(c)
        return filtered

class CoinbaseReplayEngine:
    """
    Deterministic Backtest and Replay Engine (Blocker G / H / A5 / A6).
    Reuses existing portfolio/risk contracts while running in isolated namespaces to prevent paper-state contamination.
    """
    def __init__(self, db_url: str | None = None):
        self.db = DatabaseConnection(db_url)

    def _get_db_cursor_context(self):
        """Unified cursor context manager for SQLite/PostgreSQL."""
        if self.db.use_sqlite:
            class SQLiteContext:
                def __init__(self, conn):
                    self.conn = conn
                    self.cur = None
                def __enter__(self):
                    self.cur = self.conn.cursor()
                    return self.cur
                def __exit__(self, exc_type, exc_val, exc_tb):
                    try:
                        if exc_type is None:
                            self.conn.commit()
                    finally:
                        if self.cur:
                            self.cur.close()
            return SQLiteContext(self.db.sqlite_conn)
        else:
            class PostgresContext:
                def __init__(self, db_url):
                    self.db_url = db_url
                    self.conn = None
                    self.cur = None
                def __enter__(self):
                    import psycopg2
                    from psycopg2.extras import RealDictCursor
                    self.conn = psycopg2.connect(self.db_url)
                    self.conn.set_session(isolation_level='SERIALIZABLE', autocommit=False)
                    self.cur = self.conn.cursor(cursor_factory=RealDictCursor)
                    return self.cur
                def __exit__(self, exc_type, exc_val, exc_tb):
                    try:
                        if exc_type is not None:
                            self.conn.rollback()
                        else:
                            self.conn.commit()
                    finally:
                        if self.cur:
                            self.cur.close()
                        if self.conn:
                            self.conn.close()
            return PostgresContext(self.db.db_url)

    def load_dataset_from_db(
        self,
        symbols: List[str],
        granularity: int,
        start_time: datetime,
        end_time: datetime,
        as_of: datetime,
        code_sha: str | None = None,
        config_hash: str = "v1"
    ) -> HistoricalDataset:
        """
        Loads and canonicalizes historical candles from the database to construct the immutable Dataset Contract.
        """
        candles_list = []
        product_ids = [get_product_mapping(s).execution_product_id for s in symbols]
        
        with self._get_db_cursor_context() as cur:
            if self.db.use_sqlite:
                placeholders = ",".join(["?"] * len(product_ids))
                rows = cur.execute(
                    f"SELECT product_id, canonical_symbol, granularity, candle_open, open, high, low, close, volume, quality_state "
                    f"FROM historical_candles WHERE product_id IN ({placeholders}) AND granularity = ? AND candle_open >= ? AND candle_open < ? AND quality_state = 'VALID' "
                    f"ORDER BY candle_open ASC",
                    (*product_ids, granularity, start_time.isoformat(), end_time.isoformat())
                ).fetchall()
                for r in rows:
                    candles_list.append({
                        "product_id": r[0],
                        "canonical_symbol": r[1],
                        "granularity": r[2],
                        "candle_open": r[3],
                        "open": float(r[4]),
                        "high": float(r[5]),
                        "low": float(r[6]),
                        "close": float(r[7]),
                        "volume": float(r[8]),
                        "quality_state": r[9]
                    })
            else:
                cur.execute(
                    "SELECT product_id, canonical_symbol, granularity, candle_open, open, high, low, close, volume, quality_state "
                    "FROM historical_candles WHERE product_id = ANY(%s) AND granularity = %s AND candle_open >= %s AND candle_open < %s AND quality_state = 'VALID' "
                    "ORDER BY candle_open ASC",
                    (product_ids, granularity, start_time, end_time)
                )
                rows = cur.fetchall()
                for r in rows:
                    candles_list.append({
                        "product_id": r["product_id"],
                        "canonical_symbol": r["canonical_symbol"],
                        "granularity": r["granularity"],
                        "candle_open": r["candle_open"].isoformat(),
                        "open": float(r["open"]),
                        "high": float(r["high"]),
                        "low": float(r["low"]),
                        "close": float(r["close"]),
                        "volume": float(r["volume"]),
                        "quality_state": r["quality_state"]
                    })

        if code_sha is None:
            code_sha = get_current_code_sha()

        return HistoricalDataset(
            canonical_symbols=symbols,
            timeframe=granularity,
            start_time=start_time,
            end_time=end_time,
            as_of=as_of,
            candles_list=candles_list,
            code_sha=code_sha,
            config_hash=config_hash
        )

    def run_backtest(
        self,
        dataset: HistoricalDataset,
        initial_cash: float = 10000.0,
        fee_rate: float = 0.0060,
        slippage_rate: float = 0.0005,
        seed: int = 42,
        code_sha: str = "unknown"
    ) -> dict:
        """
        Executes a chronological, deterministic backtest replay.
        Uses isolated memory state and namespace to prevent paper-state contamination (Blocker G).
        """
        run_id = f"run-{uuid.uuid4()}"
        print(f"Replay: Starting run {run_id} on dataset {dataset.dataset_id}")

        # Seed the random number generator deterministically to simulate slippage! (Blocker A7)
        rng = random.Random(seed)

        # Blocker A5: Real dataset persistence into dataset_versions table before starting!
        self._save_dataset_version(dataset, code_sha)

        # Blocker A6: PostgreSQL Lifecycle — create the replay_runs row BEFORE execution rows!
        config_hash = hashlib.sha256(f"{initial_cash}|{fee_rate}|{slippage_rate}|{seed}".encode("utf-8")).hexdigest()
        self._save_replay_run_metadata(
            run_id=run_id,
            dataset_id=dataset.dataset_id,
            config_hash=config_hash,
            code_sha=code_sha,
            seed=seed,
            fee_rate=fee_rate,
            slippage_rate=slippage_rate,
            start_time=dataset.start_time,
            end_time=dataset.end_time,
            result_digest="IN_PROGRESS"
        )

        # Isolated memory state
        cash = initial_cash
        equity = initial_cash
        positions = {}  # symbol -> {"quantity": float, "entry_price": float}
        
        # Track metrics
        peak_equity = initial_cash
        drawdown = 0.0
        trades_count = 0
        realized_pnl = 0.0
        unrealized_pnl = 0.0
        fees_paid = 0.0

        # Deterministic Replay Clock (Blocker G)
        current_time = dataset.start_time
        timeframe = dataset.timeframe

        while current_time < dataset.end_time:
            # 1. Fetch No-Lookahead historical observations as of current time T
            historical_obs = dataset.get_as_of(current_time)
            
            # For each symbol, build a simulated market snapshot
            for sym in dataset.canonical_symbols:
                p_mapping = get_product_mapping(sym)
                prod_id = p_mapping.execution_product_id
                
                # Get the latest candle strictly before current_time T for this product
                sym_obs = [c for c in historical_obs if c["product_id"] == prod_id]
                if not sym_obs:
                    continue  # No historical mark yet
                
                latest_candle = sym_obs[-1]
                price = latest_candle["close"]
                
                # Blocker A6: Correct Accounting — equity = cash + market value of open positions!
                market_value = 0.0
                unrealized_pnl = 0.0
                for pos_sym, pos in positions.items():
                    if pos["quantity"] > 0:
                        if pos_sym == sym:
                            pos_price = price
                        else:
                            other_prod = get_product_mapping(pos_sym).execution_product_id
                            other_obs = [c for c in historical_obs if c["product_id"] == other_prod]
                            pos_price = other_obs[-1]["close"] if other_obs else pos["entry_price"]
                        market_value += pos["quantity"] * pos_price
                        unrealized_pnl += pos["quantity"] * (pos_price - pos["entry_price"])
                
                equity = cash + market_value
                peak_equity = max(peak_equity, equity)
                drawdown = ((peak_equity - equity) / peak_equity) * 100.0 if peak_equity > 0.0 else 0.0

                # 2. Strategy Stub: a deterministic rule to certify the infrastructure (Blocker G)
                # Deterministic stub: BUY if price dropped, SELL if stop/take exceeded
                action = "NO_TRADE"
                pos = positions.get(sym)
                
                if not pos or pos["quantity"] == 0:
                    # Deterministic buy: if the last 3 candles show a down-trend, BUY!
                    if len(sym_obs) >= 3:
                        if sym_obs[-1]["close"] < sym_obs[-2]["close"] < sym_obs[-3]["close"]:
                            action = "OPEN"
                else:
                    # Deterministic protective exits: stop-loss or take-profit triggered
                    if price <= pos["entry_price"] * 0.98:  # 2% stop-loss
                        action = "CLOSE"
                    elif price >= pos["entry_price"] * 1.05:  # 5% take-profit
                        action = "CLOSE"

                if action != "NO_TRADE":
                    # Evaluate using deterministic Risk rules
                    side = "BUY" if action == "OPEN" else "SELL"
                    qty = 0.0
                    
                    if action == "OPEN":
                        # Spend 10% of cash
                        allocated = cash * 0.1
                        # Blocker A7: apply randomized deterministic slippage based on seed!
                        sim_slippage = slippage_rate * rng.uniform(0.9, 1.1)
                        slippage_price = price * (1.0 + sim_slippage)
                        fee = allocated * fee_rate
                        qty = (allocated - fee) / slippage_price
                        
                        if cash >= allocated and qty > 0:
                            cash -= allocated
                            fees_paid += fee
                            positions[sym] = {"quantity": qty, "entry_price": slippage_price}
                            trades_count += 1
                            
                            # Recalculate metrics after fill (Blocker B5)
                            market_value = 0.0
                            unrealized_pnl = 0.0
                            for pos_sym, pos_data in positions.items():
                                if pos_data["quantity"] > 0:
                                    if pos_sym == sym:
                                        pos_price = price
                                    else:
                                        other_prod = get_product_mapping(pos_sym).execution_product_id
                                        other_obs = [c for c in historical_obs if c["product_id"] == other_prod]
                                        pos_price = other_obs[-1]["close"] if other_obs else pos_data["entry_price"]
                                    market_value += pos_data["quantity"] * pos_price
                                    unrealized_pnl += pos_data["quantity"] * (pos_price - pos_data["entry_price"])
                            
                            equity = cash + market_value
                            peak_equity = max(peak_equity, equity)
                            drawdown = ((peak_equity - equity) / peak_equity) * 100.0 if peak_equity > 0.0 else 0.0
                            
                            self._write_replay_ledger(run_id, sym, "OPEN", "BUY", qty, slippage_price, fee, sim_slippage, unrealized_pnl, realized_pnl, equity, cash, current_time)
                    else: # CLOSE
                        qty = pos["quantity"]
                        # Blocker A7: apply randomized deterministic slippage based on seed!
                        sim_slippage = slippage_rate * rng.uniform(0.9, 1.1)
                        slippage_price = price * (1.0 - sim_slippage)
                        fee = qty * slippage_price * fee_rate
                        proceeds = (qty * slippage_price) - fee
                        
                        trade_realized = proceeds - (qty * pos["entry_price"])
                        cash += proceeds
                        realized_pnl += trade_realized
                        fees_paid += fee
                        positions[sym] = {"quantity": 0.0, "entry_price": 0.0}
                        trades_count += 1
                        
                        # Recalculate metrics after fill (Blocker B5)
                        market_value = 0.0
                        unrealized_pnl = 0.0
                        for pos_sym, pos_data in positions.items():
                            if pos_data["quantity"] > 0:
                                if pos_sym == sym:
                                    pos_price = price
                                else:
                                    other_prod = get_product_mapping(pos_sym).execution_product_id
                                    other_obs = [c for c in historical_obs if c["product_id"] == other_prod]
                                    pos_price = other_obs[-1]["close"] if other_obs else pos_data["entry_price"]
                                market_value += pos_data["quantity"] * pos_price
                                unrealized_pnl += pos_data["quantity"] * (pos_price - pos_data["entry_price"])
                        
                        equity = cash + market_value
                        peak_equity = max(peak_equity, equity)
                        drawdown = ((peak_equity - equity) / peak_equity) * 100.0 if peak_equity > 0.0 else 0.0
                        
                        self._write_replay_ledger(run_id, sym, "CLOSE", "SELL", qty, slippage_price, fee, sim_slippage, unrealized_pnl, realized_pnl, equity, cash, current_time)

            # Increment Replay Clock
            current_time += timedelta(seconds=timeframe)

        # Final MTM valuation at the end of the run (Blocker B5)
        market_value = 0.0
        unrealized_pnl = 0.0
        for pos_sym, pos_data in positions.items():
            if pos_data["quantity"] > 0:
                other_prod = get_product_mapping(pos_sym).execution_product_id
                all_candles = [c for c in dataset.candles if c["product_id"] == other_prod]
                pos_price = all_candles[-1]["close"] if all_candles else pos_data["entry_price"]
                market_value += pos_data["quantity"] * pos_price
                unrealized_pnl += pos_data["quantity"] * (pos_price - pos_data["entry_price"])
        
        equity = cash + market_value
        peak_equity = max(peak_equity, equity)
        drawdown = ((peak_equity - equity) / peak_equity) * 100.0 if peak_equity > 0.0 else 0.0

        # 3. Blocker A6: PostgreSQL Lifecycle — Calculate final digest and finalize run metadata!
        result_digest = self._calculate_run_digest(run_id)
        self._update_replay_run_digest(run_id, result_digest)

        return {
            "run_id": run_id,
            "dataset_hash": dataset.dataset_hash,
            "config_hash": config_hash,
            "trades_count": trades_count,
            "realized_pnl": realized_pnl,
            "fees_paid": fees_paid,
            "final_equity": equity,
            "max_drawdown": drawdown,
            "result_digest": result_digest
        }

    def _write_replay_ledger(
        self,
        run_id: str,
        symbol: str,
        action: str,
        side: str,
        qty: float,
        price: float,
        fee: float,
        slippage: float,
        unrealized: float,
        realized: float,
        equity: float,
        cash: float,
        timestamp: datetime
    ):
        """
        Writes trade execution to the isolated replay ledger (Blocker G).
        """
        with self._get_db_cursor_context() as cur:
            if self.db.use_sqlite:
                cur.execute("""
                    INSERT INTO replay_ledger (
                        run_id, symbol, action, side, quantity, price, fee, slippage,
                        unrealized_pnl, realized_pnl, equity, cash, timestamp
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    run_id, symbol, action, side, qty, price, fee, slippage,
                    unrealized, realized, equity, cash, timestamp.isoformat()
                ))
            else:
                cur.execute("""
                    INSERT INTO replay_ledger (
                        run_id, symbol, action, side, quantity, price, fee, slippage,
                        unrealized_pnl, realized_pnl, equity, cash, timestamp
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    run_id, symbol, action, side, qty, price, fee, slippage,
                    unrealized, realized, equity, cash, timestamp
                ))

    def _calculate_run_digest(self, run_id: str) -> str:
        """
        Computes a stable, deterministic SHA-256 digest of all execution rows in the isolated replay ledger (Blocker H).
        Same trades, seed, and config produces exactly the same digest.
        """
        trade_rows = []
        with self._get_db_cursor_context() as cur:
            if self.db.use_sqlite:
                rows = cur.execute(
                    "SELECT symbol, action, side, quantity, price, fee, realized_pnl, cash, timestamp "
                    "FROM replay_ledger WHERE run_id = ? ORDER BY id ASC", (run_id,)
                ).fetchall()
                for r in rows:
                    trade_rows.append(f"{r[0]}_{r[1]}_{r[2]}_{r[3]}_{r[4]}_{r[5]}_{r[6]}_{r[7]}_{r[8]}")
            else:
                cur.execute(
                    "SELECT symbol, action, side, quantity, price, fee, realized_pnl, cash, timestamp "
                    "FROM replay_ledger WHERE run_id = %s ORDER BY id ASC", (run_id,)
                )
                rows = cur.fetchall()
                for r in rows:
                    trade_rows.append(f"{r['symbol']}_{r['action']}_{r['side']}_{r['quantity']}_{r['price']}_{r['fee']}_{r['realized_pnl']}_{r['cash']}_{r['timestamp'].isoformat()}")
        
        raw_str = "|".join(trade_rows)
        return hashlib.sha256(raw_str.encode("utf-8")).hexdigest()

    def _save_dataset_version(self, dataset: HistoricalDataset, code_sha: str):
        """
        Persists dataset metadata into the dataset_versions table (Blocker A5).
        """
        with self._get_db_cursor_context() as cur:
            symbols_json = json.dumps(dataset.canonical_symbols)
            if self.db.use_sqlite:
                cur.execute("""
                    INSERT OR REPLACE INTO dataset_versions (
                        dataset_id, dataset_hash, universe_hash, canonical_symbols, timeframe, start_time, end_time, as_of, preprocessing_version, code_sha, config_hash, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (
                    dataset.dataset_id, dataset.dataset_hash, "v1", symbols_json, dataset.timeframe, dataset.start_time.isoformat(), dataset.end_time.isoformat(), dataset.as_of.isoformat(), "v1", code_sha, "v1"
                ))
            else:
                cur.execute("""
                    INSERT INTO dataset_versions (
                        dataset_id, dataset_hash, universe_hash, canonical_symbols, timeframe, start_time, end_time, as_of, preprocessing_version, code_sha, config_hash, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                    ON CONFLICT (dataset_id) DO NOTHING
                """, (
                    dataset.dataset_id, dataset.dataset_hash, "v1", symbols_json, dataset.timeframe, dataset.start_time, dataset.end_time, dataset.as_of, "v1", code_sha, "v1"
                ))

    def _save_replay_run_metadata(
        self,
        run_id: str,
        dataset_id: str,
        config_hash: str,
        code_sha: str,
        seed: int,
        fee_rate: float,
        slippage_rate: float,
        start_time: datetime,
        end_time: datetime,
        result_digest: str
    ):
        with self._get_db_cursor_context() as cur:
            if self.db.use_sqlite:
                cur.execute("""
                    INSERT INTO replay_runs (
                        run_id, dataset_id, config_hash, code_sha, seed, fee_rate, slippage_rate, start_time, end_time, result_digest, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (
                    run_id, dataset_id, config_hash, code_sha, seed, fee_rate, slippage_rate, start_time.isoformat(), end_time.isoformat(), result_digest
                ))
            else:
                cur.execute("""
                    INSERT INTO replay_runs (
                        run_id, dataset_id, config_hash, code_sha, seed, fee_rate, slippage_rate, start_time, end_time, result_digest, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                """, (
                    run_id, dataset_id, config_hash, code_sha, seed, fee_rate, slippage_rate, start_time, end_time, result_digest
                ))

    def _update_replay_run_digest(self, run_id: str, result_digest: str):
        """
        Updates the final result_digest for a finished replay run (Blocker A6).
        """
        with self._get_db_cursor_context() as cur:
            if self.db.use_sqlite:
                cur.execute(
                    "UPDATE replay_runs SET result_digest = ? WHERE run_id = ?",
                    (result_digest, run_id)
                )
            else:
                cur.execute(
                    "UPDATE replay_runs SET result_digest = %s WHERE run_id = %s",
                    (result_digest, run_id)
                )
