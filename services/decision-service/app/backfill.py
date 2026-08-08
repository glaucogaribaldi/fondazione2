import os
import sys
import sqlite3
import psycopg2
import httpx
import time
from datetime import datetime, UTC, timedelta
from typing import List, Dict, Any, Optional
from .products import registry, get_product_mapping
from .executor import DatabaseConnection

class CoinbaseBackfillEngine:
    """
    Restart-safe, idempotent dynamic backfill and data-quality validation engine (Blocker B / C / D).
    """
    def __init__(self, db_url: str | None = None, rate_limit_delay: float = 0.35):
        self.db = DatabaseConnection(db_url)
        self.rate_limit_delay = rate_limit_delay
        self.headers = {"User-Agent": "Fondazione2/1.0.0"}

    def _get_db_cursor_context(self):
        """Unified cursor context manager for SQLite/PostgreSQL."""
        if self.db.use_sqlite:
            # Return a wrapper that acts like a psycopg2 transaction block
            class SQLiteContext:
                def __init__(self, conn):
                    self.conn = conn
                def __enter__(self):
                    return self.conn.cursor()
                def __exit__(self, exc_type, exc_val, exc_tb):
                    if exc_type is None:
                        self.conn.commit()
            return SQLiteContext(self.db.sqlite_conn)
        else:
            return self.db.get_cursor()

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
        # Ensure registry is initialized to load mappings dynamically (Blocker E)
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

        # Check existing checkpoint if resume is True
        if resume:
            checkpoint = self._get_checkpoint(product_id, granularity, start_time, end_time)
            if checkpoint and checkpoint["status"] == "COMPLETED":
                print(f"Backfill: Checkpoint already completed for {symbol}. Skipping.")
                return True

        self._save_checkpoint(product_id, granularity, start_time, end_time, start_time, "PENDING")

        # Chunk date range: Coinbase unauthenticated limit is 300 candles per request.
        # Sub-window size is 300 * granularity seconds.
        chunk_seconds = 300 * granularity
        current_start = start_time

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
                
                # Bounded rate limiting to avoid HTTP 429 on unauthenticated API (Blocker C)
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
        url = f"https://api.exchange.coinbase.com/products/{market_data_id}/candles"
        params = {
            "granularity": granularity,
            "start": start.isoformat(),
            "end": end.isoformat()
        }
        async with httpx.AsyncClient() as client:
            response = await client.get(url, params=params, headers=self.headers, timeout=15.0)
            if response.status_code == 429:
                print("Backfill: Hit Coinbase unauthenticated rate limit (429)! Backing off...")
                await asyncio.sleep(2.0)
                # Retry once
                response = await client.get(url, params=params, headers=self.headers, timeout=15.0)
            
            response.raise_for_status()
            return response.json()

    def _ingest_and_validate_candles(self, p, granularity: int, raw_candles: list):
        """
        Idempotent database ingestion combined with strict Data Quality validation (Blocker B / D).
        """
        with self._get_db_cursor_context() as cur:
            for c in raw_candles:
                # c structure: [timestamp, low, high, open, close, volume]
                ts = datetime.fromtimestamp(c[0], tz=UTC)
                low, high, o, close, vol = float(c[1]), float(c[2]), float(c[3]), float(c[4]), float(c[5])
                
                # Strict Data Quality validation rules (Blocker D)
                quality_state = "VALID"
                if o <= 0 or close <= 0 or low <= 0 or high <= 0 or vol < 0:
                    quality_state = "QUARANTINED"  # Invalid prices or negative volume
                elif high < low:
                    quality_state = "QUARANTINED"  # Impossible high/low relation
                elif o < low or o > high or close < low or close > high:
                    quality_state = "QUARANTINED"  # Open/Close bounds check failed
                
                if self.db.use_sqlite:
                    cur.execute("""
                        INSERT OR REPLACE INTO historical_candles (
                            product_id, canonical_symbol, granularity, candle_open,
                            open, high, low, close, volume, quality_state
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        p.product_id, p.canonical_symbol, granularity, ts.isoformat(),
                        o, high, low, close, vol, quality_state
                    ))
                else:
                    cur.execute("""
                        INSERT INTO historical_candles (
                            product_id, canonical_symbol, granularity, candle_open,
                            open, high, low, close, volume, quality_state
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (product_id, granularity, candle_open) DO UPDATE SET
                            open = EXCLUDED.open,
                            high = EXCLUDED.high,
                            low = EXCLUDED.low,
                            close = EXCLUDED.close,
                            volume = EXCLUDED.volume,
                            quality_state = EXCLUDED.quality_state;
                    """, (
                        p.product_id, p.canonical_symbol, granularity, ts,
                        o, high, low, close, vol, quality_state
                    ))

    async def scan_and_recover_gaps(self, p, granularity: int, start: datetime, end: datetime) -> int:
        """
        Scans for gaps in the canonical candles table, and performs targeted minimal
        re-fetch recovery without fabricating missing candles (Blocker D).
        """
        print(f"Data Quality: Scanning for gaps in {p.canonical_symbol}...")
        
        # 1. Fetch all VALID candle open timestamps for this window
        candles = self._get_candle_timestamps(p.product_id, granularity, start, end)
        if not candles:
            print("Data Quality: No candles found. Gap scan aborted.")
            return 0

        # Construct a set for instant lookup
        candle_set = set(candles)
        
        # 2. Iterate through expected timestamps
        gaps_detected = []
        current = start
        while current < end:
            if current not in candle_set:
                gaps_detected.append(current)
            current += timedelta(seconds=granularity)

        if not gaps_detected:
            print("Data Quality: 0 gaps detected! Data is complete and green.")
            return 0

        print(f"Data Quality: Detected {len(gaps_detected)} missing candle intervals.")
        
        # Save gaps as DETECTED
        self._save_gaps(p.product_id, granularity, gaps_detected, "DETECTED")

        # 3. Targeted Gap Recovery: perform minimal, bounded Coinbase re-fetches (Blocker D)
        # We group contiguous gap timestamps to fetch minimal sub-windows!
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
            try:
                raw_candles = await self._fetch_coinbase_candles_raw(p.market_data_product_id, chunk_start, chunk_end, granularity)
                if raw_candles:
                    self._ingest_and_validate_candles(p, granularity, raw_candles)
                    # Mark this subset of gaps as RESOLVED
                    self._update_gaps_status(p.product_id, granularity, chunk, "RESOLVED")
                    resolved_count += len(chunk)
                else:
                    # Coinbase explicitly returned no data for this interval -> Mark as EXPLICIT_UNAVAILABLE
                    self._update_gaps_status(p.product_id, granularity, chunk, "EXPLICIT_UNAVAILABLE")
                await asyncio.sleep(self.rate_limit_delay)
            except Exception as e:
                print(f"Data Quality: Targeted gap recovery refetch failed: {e}")
                # Increment attempts
                self._increment_gaps_attempts(p.product_id, granularity, chunk)

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
                    return {"last_processed_time": datetime.fromisoformat(row[0]), "status": row[1]}
            else:
                cur.execute(
                    "SELECT last_processed_time, status FROM historical_backfill_checkpoints "
                    "WHERE product_id = %s AND granularity = %s AND start_time = %s AND end_time = %s",
                    (product_id, granularity, start, end)
                )
                row = cur.fetchone()
                if row:
                    # Postgres RealDictCursor returns dictionary
                    return {"last_processed_time": row["last_processed_time"], "status": row["status"]}
        return None

    def _save_checkpoint(self, product_id: str, granularity: int, start: datetime, end: datetime, last_processed: datetime, status: str):
        with self._get_db_cursor_context() as cur:
            if self.db.use_sqlite:
                cur.execute("""
                    INSERT OR REPLACE INTO historical_backfill_checkpoints (
                        product_id, granularity, start_time, end_time, last_processed_time, status, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (product_id, granularity, start.isoformat(), end.isoformat(), last_processed.isoformat(), status))
            else:
                cur.execute("""
                    INSERT INTO historical_backfill_checkpoints (
                        product_id, granularity, start_time, end_time, last_processed_time, status, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, now())
                    ON CONFLICT (product_id, granularity, start_time, end_time) DO UPDATE SET
                        last_processed_time = EXCLUDED.last_processed_time,
                        status = EXCLUDED.status,
                        updated_at = now()
                """, (product_id, granularity, start, end, last_processed, status))

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
                    timestamps.append(datetime.fromisoformat(r[0]))
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

    def _update_gaps_status(self, product_id: str, granularity: int, gap_timestamps: list, status: str):
        with self._get_db_cursor_context() as cur:
            for g_ts in gap_timestamps:
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

    def _increment_gaps_attempts(self, product_id: str, granularity: int, gap_timestamps: list):
        with self._get_db_cursor_context() as cur:
            for g_ts in gap_timestamps:
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

import asyncio
