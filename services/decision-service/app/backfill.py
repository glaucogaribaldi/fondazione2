import os
import sys
import sqlite3
import psycopg2
import httpx
import asyncio
from datetime import datetime, UTC, timedelta
from typing import List, Dict, Any, Optional
from .products import registry, get_product_mapping
from .executor import DatabaseConnection

class CoinbaseBackfillEngine:
    """
    Restart-safe, idempotent dynamic backfill and data-quality validation engine (Blocker B / C / D / A2 / A3 / A4).
    """
    def __init__(self, db_url: str | None = None, rate_limit_delay: float = 0.35):
        self.db = DatabaseConnection(db_url)
        self.rate_limit_delay = rate_limit_delay
        self.headers = {"User-Agent": "Fondazione2/1.0.0"}

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

    async def backfill_product(
        self,
        symbol: str,
        start_time: datetime,
        end_time: datetime,
        granularity: int = 300,
        resume: bool = True,
        dry_run: bool = False
    ) -> bool:
        """
        Backfills historical candles for a specific product and date-range (Blocker C).
        """
        if not registry.is_initialized():
            await asyncio.to_thread(registry.sync_universe)

        p = registry.get_product(symbol)
        if not p:
            raise ValueError(f"Unknown product symbol: {symbol}")

        product_id = p.product_id
        market_data_id = p.market_data_product_id  # Blocker E: use market-data ID for historical REST requests

        print(f"Backfill: Starting for {symbol} (execution: {product_id}, source: {market_data_id})")
        print(f"Range: {start_time.isoformat()} to {end_time.isoformat()}, Granularity: {granularity}s")

        if dry_run:
            print("Backfill: Dry-run active. Skipping actual download.")
            return True

        current_start = start_time

        # Check existing checkpoint if resume is True (Blocker A2 - Partial Resume)
        if resume:
            checkpoint = self._get_checkpoint(product_id, granularity, start_time, end_time)
            if checkpoint:
                if checkpoint["status"] == "COMPLETED":
                    print(f"Backfill: Checkpoint already completed for {symbol}. Skipping.")
                    return True
                # Partial resume (Blocker A2): start from the last processed timestamp!
                current_start = checkpoint["last_processed_time"]
                print(f"Backfill: Resuming from last processed checkpoint: {current_start.isoformat()}")
            else:
                self._save_checkpoint(product_id, granularity, start_time, end_time, start_time, "PENDING")
        else:
            self._save_checkpoint(product_id, granularity, start_time, end_time, start_time, "PENDING")

        # Chunk date range: Coinbase unauthenticated limit is 300 candles per request.
        chunk_seconds = 300 * granularity

        success = True
        while current_start < end_time:
            current_end = min(current_start + timedelta(seconds=chunk_seconds), end_time)
            
            try:
                print(f"Backfill: Fetching chunk {current_start.isoformat()} to {current_end.isoformat()}...")
                raw_candles = await self._fetch_coinbase_candles_raw(market_data_id, current_start, current_end, granularity)
                
                # Ingest & Validate
                if raw_candles:
                    self._ingest_and_validate_candles(p, granularity, raw_candles)
                
                # Update checkpoint
                self._save_checkpoint(product_id, granularity, start_time, end_time, current_end, "PENDING")
                
                # Bounded rate limiting to avoid HTTP 429 on unauthenticated API
                await asyncio.sleep(self.rate_limit_delay)
            except Exception as e:
                print(f"Backfill: Chunk failed for {symbol}: {e}")
                success = False
                break
                
            current_start = current_end

        if success:
            self._save_checkpoint(product_id, granularity, start_time, end_time, end_time, "COMPLETED")
            print(f"Backfill: Completed successfully for {symbol}!")
            
            # Post-backfill: Run dynamic Data Quality gap scan and targeted recovery! (Blocker D)
            await self.scan_and_recover_gaps(p, granularity, start_time, end_time)
        else:
            self._save_checkpoint(product_id, granularity, start_time, end_time, current_start, "FAILED")

        return success

    async def _fetch_coinbase_candles_raw(
        self,
        market_data_id: str,
        start: datetime,
        end: datetime,
        granularity: int
    ) -> list:
        """
        Fetches historical raw candles from Coinbase with bounded exponential backoff (Blocker A7/G).
        Supports rate limit retries (429) and transient server errors (5xx/network).
        """
        url = f"https://api.exchange.coinbase.com/products/{market_data_id}/candles"
        params = {
            "granularity": granularity,
            "start": start.isoformat(),
            "end": end.isoformat()
        }
        
        backoff = 1.0
        max_attempts = 4
        
        for attempt in range(1, max_attempts + 1):
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.get(url, params=params, headers=self.headers, timeout=15.0)
                    
                    if response.status_code == 429:
                        print(f"Backfill: Hit rate limit (429) on attempt {attempt}/{max_attempts}. Backing off {backoff}s...")
                        await asyncio.sleep(backoff)
                        backoff *= 2.0
                        continue
                        
                    if response.status_code >= 500:
                        print(f"Backfill: Hit transient server error ({response.status_code}) on attempt {attempt}/{max_attempts}. Backing off {backoff}s...")
                        await asyncio.sleep(backoff)
                        backoff *= 2.0
                        continue

                    response.raise_for_status()
                    return response.json()
            except (httpx.RequestError, httpx.HTTPStatusError) as e:
                if attempt == max_attempts:
                    raise e
                print(f"Backfill: Transient network error on attempt {attempt}/{max_attempts}: {e}. Backing off {backoff}s...")
                await asyncio.sleep(backoff)
                backoff *= 2.0
        return []

    def _ingest_and_validate_candles(self, p, granularity: int, raw_candles: list):
        """
        Idempotent database ingestion combined with strict Data Quality validation (Blocker B / D).
        Also stores and historizes the canonical mapping identity (Blocker A4).
        """
        with self._get_db_cursor_context() as cur:
            for c in raw_candles:
                ts_int = c[0]
                if self.db.use_sqlite:
                    # In SQLite, save as ISO-8601 string
                    ts_iso = datetime.fromtimestamp(ts_int, tz=UTC).isoformat()
                    ts_val = ts_iso
                else:
                    # In Postgres, save as TIMESTAMPTZ datetime object
                    ts_val = datetime.fromtimestamp(ts_int, tz=UTC)

                low, high, o, close, vol = float(c[1]), float(c[2]), float(c[3]), float(c[4]), float(c[5])
                
                # Strict Data Quality validation rules (Blocker D)
                quality_state = "VALID"
                if o <= 0 or close <= 0 or low <= 0 or high <= 0 or vol < 0:
                    quality_state = "QUARANTINED"
                elif high < low:
                    quality_state = "QUARANTINED"
                elif o < low or o > high or close < low or close > high:
                    quality_state = "QUARANTINED"
                
                if self.db.use_sqlite:
                    cur.execute("""
                        INSERT OR REPLACE INTO historical_candles (
                            product_id, canonical_symbol, granularity, candle_open,
                            open, high, low, close, volume, quality_state,
                            execution_product_id, market_data_product_id, market_data_is_proxy,
                            universe_version, source_provider, source_version
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        p.product_id, p.canonical_symbol, granularity, ts_val,
                        o, high, low, close, vol, quality_state,
                        p.execution_product_id, p.market_data_product_id, int(p.market_data_is_proxy),
                        "v1", "coinbase", "v1"
                    ))
                else:
                    cur.execute("""
                        INSERT INTO historical_candles (
                            product_id, canonical_symbol, granularity, candle_open,
                            open, high, low, close, volume, quality_state,
                            execution_product_id, market_data_product_id, market_data_is_proxy,
                            universe_version, source_provider, source_version
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (product_id, granularity, candle_open) DO UPDATE SET
                            open = EXCLUDED.open,
                            high = EXCLUDED.high,
                            low = EXCLUDED.low,
                            close = EXCLUDED.close,
                            volume = EXCLUDED.volume,
                            quality_state = EXCLUDED.quality_state,
                            execution_product_id = EXCLUDED.execution_product_id,
                            market_data_product_id = EXCLUDED.market_data_product_id,
                            market_data_is_proxy = EXCLUDED.market_data_is_proxy,
                            universe_version = EXCLUDED.universe_version,
                            source_provider = EXCLUDED.source_provider,
                            source_version = EXCLUDED.source_version;
                    """, (
                        p.product_id, p.canonical_symbol, granularity, ts_val,
                        o, high, low, close, vol, quality_state,
                        p.execution_product_id, p.market_data_product_id, p.market_data_is_proxy,
                        "v1", "coinbase", "v1"
                    ))

    async def scan_and_recover_gaps(self, p, granularity: int, start: datetime, end: datetime) -> int:
        """
        Scans for gaps, performs targeted re-fetches, and runs an exact validation on each
        individual timestamp before marking as RESOLVED or EXPLICIT_UNAVAILABLE (Blocker A3 / D).
        """
        print(f"Data Quality: Scanning for gaps in {p.canonical_symbol}...")
        
        # 1. Fetch all VALID candle open timestamps for this window
        candles = self._get_candle_timestamps(p.product_id, granularity, start, end)
        candle_set = set(candles)
        
        # 2. Iterate through expected timestamps
        gaps_detected = []
        current = start
        while current < end:
            if current not in candle_set:
                gaps_detected.append(current)
            current += timedelta(seconds=granularity)

        if not gaps_detected:
            print("Data Quality: 0 gaps detected! Data is complete.")
            return 0

        print(f"Data Quality: Detected {len(gaps_detected)} missing candle intervals.")
        
        # Save gaps as DETECTED
        self._save_gaps(p.product_id, granularity, gaps_detected, "DETECTED")

        # 3. Targeted Gap Recovery: group gaps to fetch minimal sub-windows (Blocker D)
        gap_chunks = []
        temp_chunk = []
        for g_ts in sorted(gaps_detected):
            if not temp_chunk:
                temp_chunk.append(g_ts)
            else:
                if g_ts == temp_chunk[-1] + timedelta(seconds=granularity):
                    temp_chunk.append(g_ts)
                else:
                    gap_chunks.append(temp_chunk)
                    temp_chunk = [g_ts]
        if temp_chunk:
            gap_chunks.append(temp_chunk)

        resolved_count = 0
        for chunk in gap_chunks:
            chunk_start = chunk[0]
            chunk_end = chunk[-1] + timedelta(seconds=granularity)
            
            print(f"Data Quality: Running targeted refetch for gap window {chunk_start.isoformat()} to {chunk_end.isoformat()}...")
            
            # Try to fetch and ingest raw candles up to 3 times (bounded retry)
            for attempt in range(1, 4):
                try:
                    # Increment gap attempts in DB for the whole chunk
                    for g_ts in chunk:
                        self._increment_single_gap_attempts(p.product_id, granularity, g_ts)
                        
                    raw_candles = await self._fetch_coinbase_candles_raw(p.market_data_product_id, chunk_start, chunk_end, granularity)
                    if raw_candles:
                        self._ingest_and_validate_candles(p, granularity, raw_candles)
                        break
                except Exception as e:
                    print(f"Data Quality: Refetch attempt {attempt}/3 failed: {e}")
                await asyncio.sleep(self.rate_limit_delay)
                
            # Riverify each individual timestamp after the refetch attempts
            present_timestamps = set(self._get_candle_timestamps(p.product_id, granularity, chunk_start, chunk_end))
            
            for g_ts in chunk:
                if g_ts in present_timestamps:
                    self._update_single_gap_status(p.product_id, granularity, g_ts, "RESOLVED")
                    resolved_count += 1
                else:
                    # Since we retried and it's still missing, we must mark it as EXPLICIT_UNAVAILABLE. No DETECTED left silent!
                    self._update_single_gap_status(p.product_id, granularity, g_ts, "EXPLICIT_UNAVAILABLE")

        print(f"Data Quality: Gap recovery completed. Resolved {resolved_count}/{len(gaps_detected)} gaps.")
        return resolved_count

    # ────────────────────────── Checkpoints ──────────────────────────

    def _get_checkpoint(self, product_id: str, granularity: int, start: datetime, end: datetime) -> Optional[dict]:
        with self._get_db_cursor_context() as cur:
            if self.db.use_sqlite:
                row = cur.execute(
                    "SELECT last_processed_time, status FROM historical_backfill_checkpoints "
                    "WHERE product_id = ? AND granularity = ? AND start_time = ? AND end_time = ?",
                    (product_id, granularity, start.isoformat(), end.isoformat())
                ).fetchone()
                if row:
                    return {"last_processed_time": datetime.fromisoformat(row[0]).replace(tzinfo=UTC), "status": row[1]}
            else:
                cur.execute(
                    "SELECT last_processed_time, status FROM historical_backfill_checkpoints "
                    "WHERE product_id = %s AND granularity = %s AND start_time = %s AND end_time = %s",
                    (product_id, granularity, start, end)
                )
                row = cur.fetchone()
                if row:
                    return {"last_processed_time": row["last_processed_time"], "status": row["status"]}
        return None

    def _save_checkpoint(self, product_id: str, granularity: int, start: datetime, end: datetime, last_processed: datetime, status: str):
        with self._get_db_cursor_context() as cur:
            last_val = last_processed.isoformat() if self.db.use_sqlite else last_processed
            if self.db.use_sqlite:
                cur.execute("""
                    INSERT OR REPLACE INTO historical_backfill_checkpoints (
                        product_id, granularity, start_time, end_time, last_processed_time, status, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (product_id, granularity, start.isoformat(), end.isoformat(), last_val, status))
            else:
                cur.execute("""
                    INSERT INTO historical_backfill_checkpoints (
                        product_id, granularity, start_time, end_time, last_processed_time, status, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, now())
                    ON CONFLICT (product_id, granularity, start_time, end_time) DO UPDATE SET
                        last_processed_time = EXCLUDED.last_processed_time,
                        status = EXCLUDED.status,
                        updated_at = now()
                """, (product_id, granularity, start, end, last_val, status))

    # ────────────────────────── Gaps ──────────────────────────

    def _get_candle_timestamps(self, product_id: str, granularity: int, start: datetime, end: datetime) -> List[datetime]:
        timestamps = []
        with self._get_db_cursor_context() as cur:
            if self.db.use_sqlite:
                rows = cur.execute(
                    "SELECT candle_open FROM historical_candles "
                    "WHERE product_id = ? AND granularity = ? AND candle_open >= ? AND candle_open < ? AND quality_state = 'VALID' "
                    "ORDER BY candle_open ASC",
                    (product_id, granularity, start.isoformat(), end.isoformat())
                ).fetchall()
                for r in rows:
                    timestamps.append(datetime.fromisoformat(r[0]).replace(tzinfo=UTC))
            else:
                cur.execute(
                    "SELECT candle_open FROM historical_candles "
                    "WHERE product_id = %s AND granularity = %s AND candle_open >= %s AND candle_open < %s AND quality_state = 'VALID' "
                    "ORDER BY candle_open ASC",
                    (product_id, granularity, start, end)
                )
                rows = cur.fetchall()
                for r in rows:
                    timestamps.append(r["candle_open"])
        return timestamps

    def _save_gaps(self, product_id: str, granularity: int, gap_timestamps: list, status: str):
        with self._get_db_cursor_context() as cur:
            for g_ts in gap_timestamps:
                if self.db.use_sqlite:
                    cur.execute("""
                        INSERT OR REPLACE INTO historical_gaps (
                            product_id, granularity, gap_start, gap_end, status, updated_at
                        ) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    """, (product_id, granularity, g_ts.isoformat(), g_ts.isoformat(), status))
                else:
                    cur.execute("""
                        INSERT INTO historical_gaps (
                            product_id, granularity, gap_start, gap_end, status, updated_at
                        ) VALUES (%s, %s, %s, %s, %s, now())
                        ON CONFLICT (product_id, granularity, gap_start, gap_end) DO UPDATE SET
                            status = EXCLUDED.status,
                            updated_at = now()
                    """, (product_id, granularity, g_ts, g_ts, status))

    def _update_single_gap_status(self, product_id: str, granularity: int, g_ts: datetime, status: str):
        with self._get_db_cursor_context() as cur:
            if self.db.use_sqlite:
                cur.execute(
                    "UPDATE historical_gaps SET status = ?, updated_at = CURRENT_TIMESTAMP "
                    "WHERE product_id = ? AND granularity = ? AND gap_start = ?",
                    (status, product_id, granularity, g_ts.isoformat())
                )
            else:
                cur.execute(
                    "UPDATE historical_gaps SET status = %s, updated_at = now() "
                    "WHERE product_id = %s AND granularity = %s AND gap_start = %s",
                    (status, product_id, granularity, g_ts)
                )

    def _get_gap_attempts(self, product_id: str, granularity: int, g_ts: datetime) -> int:
        with self._get_db_cursor_context() as cur:
            if self.db.use_sqlite:
                row = cur.execute(
                    "SELECT attempts FROM historical_gaps WHERE product_id = ? AND granularity = ? AND gap_start = ?",
                    (product_id, granularity, g_ts.isoformat())
                ).fetchone()
                return row[0] if row else 0
            else:
                cur.execute(
                    "SELECT attempts FROM historical_gaps WHERE product_id = %s AND granularity = %s AND gap_start = %s",
                    (product_id, granularity, g_ts)
                )
                row = cur.fetchone()
                return row["attempts"] if row else 0

    def _increment_single_gap_attempts(self, product_id: str, granularity: int, g_ts: datetime):
        with self._get_db_cursor_context() as cur:
            if self.db.use_sqlite:
                cur.execute(
                    "UPDATE historical_gaps SET attempts = attempts + 1, updated_at = CURRENT_TIMESTAMP "
                    "WHERE product_id = ? AND granularity = ? AND gap_start = ?",
                    (product_id, granularity, g_ts.isoformat())
                )
            else:
                cur.execute(
                    "UPDATE historical_gaps SET attempts = attempts + 1, updated_at = now() "
                    "WHERE product_id = %s AND granularity = %s AND gap_start = %s",
                    (product_id, granularity, g_ts)
                )
