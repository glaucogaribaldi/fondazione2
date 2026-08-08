import os
import json
import asyncio
import threading
import traceback
import websockets
from datetime import datetime, UTC
from typing import Any
from prometheus_client import Counter, Gauge
from .products import registry, get_product_mapping
from .executor import PaperExecutor

# WebSocket Observability Metrics (Blocker K5 / G / R5)
WS_CONNECTED = Gauge("foundation_ws_connection_state", "WebSocket connection state (1=connected, 0=disconnected)")
WS_RECONNECTS = Counter("foundation_ws_reconnects_total", "Total WebSocket reconnects")
WS_HEARTBEAT_AGE = Gauge("foundation_ws_heartbeat_age_seconds", "Heartbeat age in seconds")
WS_MESSAGES_RECEIVED = Counter("foundation_ws_messages_received_total", "Total WebSocket messages received")
WS_SEQUENCE_GAPS = Counter("foundation_ws_sequence_gaps_total", "Total WebSocket sequence gaps detected")
WS_NORMALIZATION_FAILURES = Counter("foundation_ws_normalization_failures_total", "Total proxy/normalization failures")
WS_STALE_PRODUCTS = Counter("foundation_ws_stale_products_total", "Total stale products detected")
QUOTE_CONVERSION_FAILURES = Counter("foundation_quote_conversion_failures_total", "Total quote-conversion failures")

# Blocker R5: Duplicate & Out-of-Order dedicated Prometheus Metrics
WS_DUPLICATES = Counter("foundation_ws_duplicate_messages_total", "Total duplicate WS messages detected")
WS_OUT_OF_ORDER = Counter("foundation_ws_out_of_order_messages_total", "Total out-of-order WS messages detected")

class CoinbaseWebSocketService:
    def __init__(self, ws_url: str = "wss://advanced-trade-ws.coinbase.com"):
        self.ws_url = ws_url
        self.running = False
        self.last_heartbeat_time = None
        self.last_sequence_num = None
        self.executor = None
        self.ws = None
        self._task = None

    def start(self):
        if self.running:
            return
        self.running = True
        self.executor = PaperExecutor()
        self._task = asyncio.create_task(self._main_loop())
        asyncio.create_task(self._watchdog_loop())
        asyncio.create_task(self._periodic_refresh_loop())

    def stop(self):
        self.running = False
        if self._task:
            self._task.cancel()

    async def _main_loop(self):
        backoff = 1.0
        while self.running:
            try:
                # 1. Ensure registry is synchronized before subscribing (Blocker R3 / B)
                if not registry.is_initialized():
                    print("WebSocket Service: Registry not initialized yet. Synchronizing...")
                    await asyncio.to_thread(registry.sync_universe)
                
                active_products = [p for p in registry.list_products() if p.market_data_eligible]
                
                # Blocker R1: Universe built using unique market_data_product_id instead of product_id (Deduplicated)
                market_data_product_ids = sorted(list({p.market_data_product_id for p in active_products if p.market_data_product_id}))
                
                if not market_data_product_ids:
                    print("WebSocket Service: No active market-data products found. Retrying in 5s...")
                    await asyncio.sleep(5)
                    continue

                # Blocker R5: Reset sequence state on reconnect / fresh connection
                self.last_sequence_num = None

                print(f"WebSocket Service: Connecting to {self.ws_url}...")
                WS_CONNECTED.set(0)
                async with websockets.connect(self.ws_url) as ws:
                    self.ws = ws
                    print("WebSocket Service: Connected successfully!")
                    WS_CONNECTED.set(1)
                    backoff = 1.0  # Reset backoff on successful connection
                    
                    # 2. Subscribe to channels in batches/shards using deduplicated market-data IDs (Blocker R1 / C)
                    await self._subscribe_all(ws, market_data_product_ids)

                    # 3. Read messages
                    while self.running:
                        try:
                            raw_msg = await asyncio.wait_for(ws.recv(), timeout=15.0)
                            WS_MESSAGES_RECEIVED.inc()
                            await self._handle_message(raw_msg)
                        except asyncio.TimeoutError:
                            print("WebSocket Service: Receive timeout. Reconnecting...")
                            break
                        except Exception as e:
                            print(f"WebSocket Service: Error in receive loop: {e}")
                            break
            except Exception as e:
                print(f"WebSocket Service: Connection error: {e}")
                WS_CONNECTED.set(0)
                WS_RECONNECTS.inc()
                
            if self.running:
                # Bounded exponential backoff reconnect (Blocker C)
                print(f"WebSocket Service: Reconnecting in {backoff:.1f}s...")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2.0, 60.0)

    async def _subscribe_all(self, ws, market_data_product_ids: list[str]):
        batch_size = 100
        print(f"WebSocket Service: Subscribing to status, ticker, heartbeats for {len(market_data_product_ids)} market-data IDs in batches of {batch_size}...")
        
        # Batch subscribe to ticker
        for i in range(0, len(market_data_product_ids), batch_size):
            batch = market_data_product_ids[i:i+batch_size]
            ticker_sub = {
                "type": "subscribe",
                "product_ids": batch,
                "channel": "ticker"
            }
            await ws.send(json.dumps(ticker_sub))
            await asyncio.sleep(0.05)
            
        # Batch subscribe to status
        for i in range(0, len(market_data_product_ids), batch_size):
            batch = market_data_product_ids[i:i+batch_size]
            status_sub = {
                "type": "subscribe",
                "product_ids": batch,
                "channel": "status"
            }
            await ws.send(json.dumps(status_sub))
            await asyncio.sleep(0.05)

        # Subscribe to heartbeats
        if market_data_product_ids:
            hb_sub = {
                "type": "subscribe",
                "product_ids": [market_data_product_ids[0]],
                "channel": "heartbeats"
            }
            await ws.send(json.dumps(hb_sub))

    async def _handle_message(self, raw_msg: str):
        try:
            msg = json.loads(raw_msg)
        except Exception as e:
            print(f"WebSocket Service: Malformed json message: {e}")
            return

        channel = msg.get("channel")
        seq_num = msg.get("sequence_num")
        
        # Blocker R5: Robust, deterministic Sequence State Manager
        if seq_num is not None:
            if self.last_sequence_num is not None:
                if seq_num == self.last_sequence_num:
                    # Message DUPLICATE: log, increment, and discard immediately (do not process) (Blocker R5)
                    WS_DUPLICATES.inc()
                    return
                elif seq_num < self.last_sequence_num:
                    # Message OUT-OF-ORDER: log, increment, and discard immediately (Blocker R5)
                    WS_OUT_OF_ORDER.inc()
                    return
                elif seq_num > self.last_sequence_num + 1:
                    # SEQUENCE GAP: log, increment gap metrics, and process normally (Blocker R5)
                    gap = seq_num - (self.last_sequence_num + 1)
                    WS_SEQUENCE_GAPS.inc(gap)
                    
            self.last_sequence_num = seq_num

        if channel == "heartbeats":
            self.last_heartbeat_time = datetime.now(UTC)
            WS_HEARTBEAT_AGE.set(0.0)
            
        elif channel == "status":
            events = msg.get("events", [])
            for ev in events:
                if ev.get("type") in ["snapshot", "update"]:
                    products = ev.get("products", [])
                    for prod in products:
                        await self._handle_status_update(prod)

        elif channel == "ticker":
            events = msg.get("events", [])
            for ev in events:
                if ev.get("type") in ["snapshot", "update"]:
                    tickers = ev.get("tickers", [])
                    for tick in tickers:
                        await self._handle_ticker_update(tick)

    async def _handle_status_update(self, prod: dict[str, Any]):
        product_id = prod.get("id")
        if not product_id:
            return
        
        # Blocker R3: Automatic universe refresh if an unknown product_id arrives via status (no restart!)
        if not registry.get_product(product_id):
            print(f"WebSocket Service: Discovered unknown product ID '{product_id}' via status update! Synchronizing universe...")
            await asyncio.to_thread(registry.sync_universe)
            return

        # Update product status dynamically in the registry (Blocker B / C / R3)
        registry.update_product_status(product_id, prod)

    async def _handle_ticker_update(self, tick: dict[str, Any]):
        product_id = tick.get("product_id")
        price_str = tick.get("price")
        if not product_id or not price_str:
            return

        try:
            price = float(price_str)
        except ValueError:
            return

        # Normalization routing check (Blocker D / R1): find all canonical pairs pointing to this market_data_product_id
        mapped_symbols = registry.get_canonical_symbols_for_market_data(product_id)
        if not mapped_symbols:
            WS_NORMALIZATION_FAILURES.inc()
            return

        for symbol in mapped_symbols:
            try:
                # Update database market marks, triggering live MTM recalculations and snapshots! (Blocker P1 / O1 / F)
                await asyncio.to_thread(self.executor.update_market_mark, symbol, price)
            except Exception as e:
                print(f"WebSocket Service: Failed to update market mark for {symbol}: {e}")

    async def _watchdog_loop(self):
        """
        Liveness watchdog monitoring heartbeat timeout (Blocker C / R4).
        If the heartbeat exceeds 25 seconds, it closes the connection to force reconnect.
        """
        while self.running:
            await asyncio.sleep(5)
            if self.last_heartbeat_time:
                age = (datetime.now(UTC) - self.last_heartbeat_time).total_seconds()
                WS_HEARTBEAT_AGE.set(age)
                if age > 25.0:
                    print(f"WebSocket Service Watchdog: Heartbeat is stale (age: {age:.1f}s > 25s). Forcing reconnect...")
                    WS_STALE_PRODUCTS.inc()
                    self.last_heartbeat_time = None
                    if self.ws:
                        try:
                            # Blocker R4: Force close/disconnect active connection to trigger reconnect in _main_loop
                            await self.ws.close(code=4000, reason="Heartbeat timeout")
                        except Exception as e:
                            print(f"WebSocket Service Watchdog: Error closing websocket: {e}")

    async def _periodic_refresh_loop(self):
        """
        Periodic REST catalog refresh task (Blocker R3).
        Saves registry states and delisted history dynamically every 3600 seconds.
        """
        while self.running:
            await asyncio.sleep(3600)
            if self.running:
                print("WebSocket Service: Running periodic REST catalog refresh...")
                try:
                    await asyncio.to_thread(registry.sync_universe)
                except Exception as e:
                    print(f"WebSocket Service: Periodic refresh failed: {e}")

# Singleton instance of the WebSocket Service
websocket_service = CoinbaseWebSocketService()
