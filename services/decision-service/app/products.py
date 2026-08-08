import os
import threading
import sqlite3
import psycopg2
import collections
from psycopg2.extras import RealDictCursor
from pydantic import BaseModel
from typing import Optional, Dict, List, Any
from datetime import datetime, UTC

class ProductMapping(BaseModel):
    canonical_symbol: str
    execution_product_id: str
    market_data_product_id: str
    market_data_is_proxy: bool

class CoinbaseProduct(BaseModel):
    product_id: str
    product_type: str
    base_currency: str
    quote_currency: str
    canonical_asset: str
    canonical_symbol: str
    execution_product_id: str
    market_data_product_id: str
    market_data_is_proxy: bool
    is_disabled: bool
    trading_disabled: bool
    cancel_only: bool
    limit_only: bool
    post_only: bool
    base_increment: float
    quote_increment: float
    min_market_funds: float
    market_data_eligible: bool
    paper_execution_eligible: bool
    ineligibility_reason: Optional[str] = None
    updated_at: datetime

class CoinbaseUniverseRegistry:
    def __init__(self):
        self._lock = threading.Lock()
        self._products: Dict[str, CoinbaseProduct] = {}
        self._initialized = False

    def is_initialized(self) -> bool:
        with self._lock:
            return self._initialized

    def sync_universe(self):
        """
        Fetches products from Coinbase Exchange, normalizes them generically, 
        populates the in-memory catalog, and persists them dynamically to the database. (Blocker A / B / D / E)
        """
        import httpx
        try:
            url = "https://api.exchange.coinbase.com/products"
            headers = {"User-Agent": "Fondazione2/1.0.0"}
            response = httpx.get(url, headers=headers, timeout=15.0)
            response.raise_for_status()
            raw_products = response.json()
        except Exception as e:
            print(f"Registry: Failed to fetch online products from Coinbase REST API: {e}")
            self.load_from_db()
            return

        if not isinstance(raw_products, list):
            print("Registry: Invalid response format from Coinbase REST API")
            self.load_from_db()
            return

        # Build lookup set of all available product IDs to evaluate proxies generically (Blocker D)
        all_ids = {p["id"] for p in raw_products if p.get("id")}
        
        new_products = {}
        now = datetime.now(UTC)

        for p in raw_products:
            prod_id = p.get("id")
            base = p.get("base_currency")
            quote = p.get("quote_currency")
            if not prod_id or not base or not quote:
                continue

            status = p.get("status", "online")
            is_disabled = status != "online"
            trading_disabled = bool(p.get("trading_disabled", False))
            cancel_only = bool(p.get("cancel_only", False))
            limit_only = bool(p.get("limit_only", False))
            post_only = bool(p.get("post_only", False))

            try:
                base_increment = float(p.get("base_increment") or 0.00000001)
                quote_increment = float(p.get("quote_increment") or 0.01)
                min_market_funds = float(p.get("min_market_funds") or 1.0)
            except (ValueError, TypeError):
                base_increment = 0.00000001
                quote_increment = 0.01
                min_market_funds = 1.0

            canonical_asset = base.upper()
            canonical_symbol = f"{canonical_asset}/{quote.upper()}"
            execution_product_id = prod_id

            # Default: no proxy
            market_data_product_id = prod_id
            market_data_is_proxy = False

            # Generic USDC proxy rule (Blocker D / R1): 
            # Non-user public channels don't support *-USDC subscription (except USDT-USDC & EURC-USDC).
            # We map *-USDC to correspond *-USD product if the *-USD product exists!
            if quote.upper() == "USDC" and base.upper() not in ["USDT", "EURC"]:
                usd_pair = f"{base.upper()}-USD"
                if usd_pair in all_ids:
                    market_data_product_id = usd_pair
                    market_data_is_proxy = True

            market_data_eligible = (status == "online")

            # Note: Blocker R2 — removed the supported_quotes whitelist. All quotes are allowed,
            # paper execution eligibility is evaluated dynamically based on the conversion rate graph!
            paper_execution_eligible = True
            ineligibility_reason = None

            if is_disabled:
                paper_execution_eligible = False
                ineligibility_reason = "Product is offline/disabled"
            elif trading_disabled:
                paper_execution_eligible = False
                ineligibility_reason = "Trading is disabled"
            elif cancel_only:
                paper_execution_eligible = False
                ineligibility_reason = "Product is cancel-only"

            prod_obj = CoinbaseProduct(
                product_id=prod_id,
                product_type="SPOT",
                base_currency=base.upper(),
                quote_currency=quote.upper(),
                canonical_asset=canonical_asset,
                canonical_symbol=canonical_symbol,
                execution_product_id=execution_product_id,
                market_data_product_id=market_data_product_id,
                market_data_is_proxy=market_data_is_proxy,
                is_disabled=is_disabled,
                trading_disabled=trading_disabled,
                cancel_only=cancel_only,
                limit_only=limit_only,
                post_only=post_only,
                base_increment=base_increment,
                quote_increment=quote_increment,
                min_market_funds=min_market_funds,
                market_data_eligible=market_data_eligible,
                paper_execution_eligible=paper_execution_eligible,
                ineligibility_reason=ineligibility_reason,
                updated_at=now
            )
            new_products[prod_id] = prod_obj

        with self._lock:
            # Maintain registry history: do not silently delete delisted/removed products, preserve them (Blocker B)
            for k, v in self._products.items():
                if k not in new_products:
                    v.is_disabled = True
                    v.paper_execution_eligible = False
                    v.ineligibility_reason = "Delisted/Removed from Coinbase REST catalog"
                    v.updated_at = now
                    new_products[k] = v
                    
            self._products = new_products
            self._initialized = True
            print(f"Registry: Dynamic discovery complete. Loaded {len(self._products)} SPOT products into memory.")

        self.persist_to_db()

    def update_product_status(self, product_id: str, status_data: dict[str, Any]):
        """
        Dynamically updates product specifications from WS status events. (Blocker B / C / R3)
        """
        with self._lock:
            updated = False
            for p in self._products.values():
                if p.product_id == product_id or p.market_data_product_id == product_id:
                    status = status_data.get("status", "online")
                    p.is_disabled = status != "online"
                    p.trading_disabled = status_data.get("trading_disabled", p.trading_disabled)
                    p.cancel_only = status_data.get("cancel_only", p.cancel_only)
                    
                    p.paper_execution_eligible = True
                    p.ineligibility_reason = None

                    if p.is_disabled:
                        p.paper_execution_eligible = False
                        p.ineligibility_reason = "Product is offline/disabled"
                    elif p.trading_disabled:
                        p.paper_execution_eligible = False
                        p.ineligibility_reason = "Trading is disabled"
                    elif p.cancel_only:
                        p.paper_execution_eligible = False
                        p.ineligibility_reason = "Product is cancel-only"

                    p.updated_at = datetime.now(UTC)
                    updated = True
            
            if updated:
                self.persist_to_db()

    def get_product(self, key: str) -> Optional[CoinbaseProduct]:
        key = key.replace("/", "-").strip().upper()
        with self._lock:
            if key in self._products:
                return self._products[key]
            
            # Try symbol match (e.g. BTC/USDC)
            sym = key.replace("-", "/")
            for p in self._products.values():
                if p.canonical_symbol == sym or p.product_id == key:
                    return p
        return None

    def get_canonical_symbols_for_market_data(self, market_data_product_id: str) -> list[str]:
        """
        Normalization mapping routing: given an inbound WS product_id (e.g. BTC-USD),
        returns all corresponding canonical symbols (e.g. ["BTC/USD", "BTC/USDC"]) (Blocker D / R1).
        """
        symbols = []
        with self._lock:
            for p in self._products.values():
                if p.market_data_product_id == market_data_product_id:
                    symbols.append(p.canonical_symbol)
        return symbols

    def list_products(self) -> List[CoinbaseProduct]:
        with self._lock:
            return list(self._products.values())

    def get_metrics_summary(self, get_mark_func=None) -> dict[str, Any]:
        """
        Collects detailed aggregate statistics for reports and metrics. (Blocker G / R2)
        """
        products = self.list_products()
        
        unique_assets = set()
        quotes_dist = {}
        total = len(products)
        active = 0
        disabled = 0
        eligible = 0

        for p in products:
            unique_assets.add(p.base_currency)
            quotes_dist[p.quote_currency] = quotes_dist.get(p.quote_currency, 0) + 1
            if p.is_disabled:
                disabled += 1
            else:
                active += 1
                
            # If get_mark_func is provided, check dynamic eligibility (Blocker R2)
            if get_mark_func is not None:
                elig, _ = self.get_product_eligibility(p.product_id, get_mark_func)
                if elig:
                    eligible += 1
            else:
                if p.paper_execution_eligible:
                    eligible += 1

        return {
            "total_products": total,
            "active_products": active,
            "disabled_products": disabled,
            "eligible_products": eligible,
            "unique_assets": len(unique_assets),
            "quotes_distribution": quotes_dist
        }

    def get_product_eligibility(self, product_id: str, get_mark_func) -> tuple[bool, Optional[str]]:
        """
        Evaluates execution eligibility dynamically based on live conversion graph liveness and status checks (Blocker R2).
        """
        p = self.get_product(product_id)
        if not p:
            return False, "Unknown product"
            
        if p.is_disabled:
            return False, "Product is offline/disabled"
        if p.trading_disabled:
            return False, "Trading is disabled"
        if p.cancel_only:
            return False, "Product is cancel-only"
            
        # Check conversion path dynamically (Blocker R2 / E)
        rate = get_conversion_rate_to_usdc(p.quote_currency, get_mark_func)
        if rate is None:
            return False, f"Stale or missing quote conversion path for {p.quote_currency}"
            
        return True, None

    def _ensure_table_exists(self, conn, is_sqlite: bool):
        if is_sqlite:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS coinbase_products (
                    product_id TEXT PRIMARY KEY,
                    product_type TEXT NOT NULL,
                    base_currency TEXT NOT NULL,
                    quote_currency TEXT NOT NULL,
                    canonical_asset TEXT NOT NULL,
                    canonical_symbol TEXT NOT NULL,
                    execution_product_id TEXT NOT NULL,
                    market_data_product_id TEXT NOT NULL,
                    market_data_is_proxy INTEGER NOT NULL DEFAULT 0,
                    is_disabled INTEGER NOT NULL DEFAULT 0,
                    trading_disabled INTEGER NOT NULL DEFAULT 0,
                    cancel_only INTEGER NOT NULL DEFAULT 0,
                    limit_only INTEGER NOT NULL DEFAULT 0,
                    post_only INTEGER NOT NULL DEFAULT 0,
                    base_increment REAL,
                    quote_increment REAL,
                    min_market_funds REAL,
                    market_data_eligible INTEGER NOT NULL DEFAULT 1,
                    paper_execution_eligible INTEGER NOT NULL DEFAULT 1,
                    ineligibility_reason TEXT,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
        else:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS coinbase_products (
                        product_id TEXT PRIMARY KEY,
                        product_type TEXT NOT NULL,
                        base_currency TEXT NOT NULL,
                        quote_currency TEXT NOT NULL,
                        canonical_asset TEXT NOT NULL,
                        canonical_symbol TEXT NOT NULL,
                        execution_product_id TEXT NOT NULL,
                        market_data_product_id TEXT NOT NULL,
                        market_data_is_proxy BOOLEAN NOT NULL DEFAULT FALSE,
                        is_disabled BOOLEAN NOT NULL DEFAULT FALSE,
                        trading_disabled BOOLEAN NOT NULL DEFAULT FALSE,
                        cancel_only BOOLEAN NOT NULL DEFAULT FALSE,
                        limit_only BOOLEAN NOT NULL DEFAULT FALSE,
                        post_only BOOLEAN NOT NULL DEFAULT FALSE,
                        base_increment NUMERIC(28,10),
                        quote_increment NUMERIC(28,10),
                        min_market_funds NUMERIC(28,10),
                        market_data_eligible BOOLEAN NOT NULL DEFAULT TRUE,
                        paper_execution_eligible BOOLEAN NOT NULL DEFAULT TRUE,
                        ineligibility_reason TEXT,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    )
                """)

    def persist_to_db(self):
        db_url = os.getenv("DATABASE_URL")
        if not db_url or db_url.startswith("sqlite"):
            try:
                conn = sqlite3.connect("file:fondazione_test?mode=memory&cache=shared", uri=True)
                self._ensure_table_exists(conn, is_sqlite=True)
                with conn:
                    for p in self._products.values():
                        conn.execute("""
                            INSERT OR REPLACE INTO coinbase_products (
                                product_id, product_type, base_currency, quote_currency,
                                canonical_asset, canonical_symbol, execution_product_id,
                                market_data_product_id, market_data_is_proxy, is_disabled,
                                trading_disabled, cancel_only, limit_only, post_only,
                                base_increment, quote_increment, min_market_funds,
                                market_data_eligible, paper_execution_eligible, ineligibility_reason,
                                updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            p.product_id, p.product_type, p.base_currency, p.quote_currency,
                            p.canonical_asset, p.canonical_symbol, p.execution_product_id,
                            p.market_data_product_id, int(p.market_data_is_proxy), int(p.is_disabled),
                            int(p.trading_disabled), int(p.cancel_only), int(p.limit_only), int(p.post_only),
                            p.base_increment, p.quote_increment, p.min_market_funds,
                            int(p.market_data_eligible), int(p.paper_execution_eligible), p.ineligibility_reason,
                            p.updated_at.isoformat()
                        ))
                conn.close()
            except Exception as e:
                pass
            return

        try:
            conn = psycopg2.connect(db_url)
            self._ensure_table_exists(conn, is_sqlite=False)
            with conn:
                with conn.cursor() as cur:
                    for p in self._products.values():
                        cur.execute("""
                            INSERT INTO coinbase_products (
                                product_id, product_type, base_currency, quote_currency,
                                canonical_asset, canonical_symbol, execution_product_id,
                                market_data_product_id, market_data_is_proxy, is_disabled,
                                trading_disabled, cancel_only, limit_only, post_only,
                                base_increment, quote_increment, min_market_funds,
                                market_data_eligible, paper_execution_eligible, ineligibility_reason,
                                updated_at
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (product_id) DO UPDATE SET
                                is_disabled = EXCLUDED.is_disabled,
                                trading_disabled = EXCLUDED.trading_disabled,
                                cancel_only = EXCLUDED.cancel_only,
                                limit_only = EXCLUDED.limit_only,
                                post_only = EXCLUDED.post_only,
                                base_increment = EXCLUDED.base_increment,
                                quote_increment = EXCLUDED.quote_increment,
                                min_market_funds = EXCLUDED.min_market_funds,
                                market_data_eligible = EXCLUDED.market_data_eligible,
                                paper_execution_eligible = EXCLUDED.paper_execution_eligible,
                                ineligibility_reason = EXCLUDED.ineligibility_reason,
                                updated_at = EXCLUDED.updated_at;
                        """, (
                            p.product_id, p.product_type, p.base_currency, p.quote_currency,
                            p.canonical_asset, p.canonical_symbol, p.execution_product_id,
                            p.market_data_product_id, p.market_data_is_proxy, p.is_disabled,
                            p.trading_disabled, p.cancel_only, p.limit_only, p.post_only,
                            p.base_increment, p.quote_increment, p.min_market_funds,
                            p.market_data_eligible, p.paper_execution_eligible, p.ineligibility_reason,
                            p.updated_at
                        ))
            conn.close()
        except Exception as e:
            print(f"Registry: PostgreSQL persist failed: {e}")

    def load_from_db(self):
        db_url = os.getenv("DATABASE_URL")
        if not db_url or db_url.startswith("sqlite"):
            try:
                conn = sqlite3.connect("file:fondazione_test?mode=memory&cache=shared", uri=True)
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                rows = cursor.execute("SELECT * FROM coinbase_products").fetchall()
                new_products = {}
                for r in rows:
                    p = CoinbaseProduct(
                        product_id=r["product_id"],
                        product_type=r["product_type"],
                        base_currency=r["base_currency"],
                        quote_currency=r["quote_currency"],
                        canonical_asset=r["canonical_asset"],
                        canonical_symbol=r["canonical_symbol"],
                        execution_product_id=r["execution_product_id"],
                        market_data_product_id=r["market_data_product_id"],
                        market_data_is_proxy=bool(r["market_data_is_proxy"]),
                        is_disabled=bool(r["is_disabled"]),
                        trading_disabled=bool(r["trading_disabled"]),
                        cancel_only=bool(r["cancel_only"]),
                        limit_only=bool(r["limit_only"]),
                        post_only=bool(r["post_only"]),
                        base_increment=float(r["base_increment"]),
                        quote_increment=float(r["quote_increment"]),
                        min_market_funds=float(r["min_market_funds"]),
                        market_data_eligible=bool(r["market_data_eligible"]),
                        paper_execution_eligible=bool(r["paper_execution_eligible"]),
                        ineligibility_reason=r["ineligibility_reason"],
                        updated_at=datetime.fromisoformat(r["updated_at"])
                    )
                    new_products[p.product_id] = p
                if new_products:
                    with self._lock:
                        self._products = new_products
                        self._initialized = True
                conn.close()
            except Exception as e:
                pass
            return

        try:
            conn = psycopg2.connect(db_url)
            with conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("SELECT * FROM coinbase_products")
                    rows = cur.fetchall()
                    new_products = {}
                    for r in rows:
                        p = CoinbaseProduct(
                            product_id=r["product_id"],
                            product_type=r["product_type"],
                            base_currency=r["base_currency"],
                            quote_currency=r["quote_currency"],
                            canonical_asset=r["canonical_asset"],
                            canonical_symbol=r["canonical_symbol"],
                            execution_product_id=r["execution_product_id"],
                            market_data_product_id=r["market_data_product_id"],
                            market_data_is_proxy=bool(r["market_data_is_proxy"]),
                            is_disabled=bool(r["is_disabled"]),
                            trading_disabled=bool(r["trading_disabled"]),
                            cancel_only=bool(r["cancel_only"]),
                            limit_only=bool(r["limit_only"]),
                            post_only=bool(r["post_only"]),
                            base_increment=float(r["base_increment"]),
                            quote_increment=float(r["quote_increment"]),
                            min_market_funds=float(r["min_market_funds"]),
                            market_data_eligible=bool(r["market_data_eligible"]),
                            paper_execution_eligible=bool(r["paper_execution_eligible"]),
                            ineligibility_reason=r["ineligibility_reason"],
                            updated_at=r["updated_at"]
                        )
                        new_products[p.product_id] = p
                    if new_products:
                        with self._lock:
                            self._products = new_products
                            self._initialized = True
            conn.close()
        except Exception as e:
            print(f"Registry: PostgreSQL load failed: {e}")

# Singleton Instance
registry = CoinbaseUniverseRegistry()

# Static fallback dictionary for backward compatibility (Blocker D)
PRODUCT_MAPPINGS = {
    "BTC/USDC": ProductMapping(
        canonical_symbol="BTC/USDC",
        execution_product_id="BTC-USDC",
        market_data_product_id="BTC-USD",
        market_data_is_proxy=True
    ),
    "ETH/USDC": ProductMapping(
        canonical_symbol="ETH/USDC",
        execution_product_id="ETH-USDC",
        market_data_product_id="ETH-USD",
        market_data_is_proxy=True
    ),
    "SOL/USDC": ProductMapping(
        canonical_symbol="SOL/USDC",
        execution_product_id="SOL-USDC",
        market_data_product_id="SOL-USD",
        market_data_is_proxy=True
    )
}

def get_product_mapping(canonical_symbol: str) -> ProductMapping:
    p = registry.get_product(canonical_symbol)
    if p:
        return ProductMapping(
            canonical_symbol=p.canonical_symbol,
            execution_product_id=p.execution_product_id,
            market_data_product_id=p.market_data_product_id,
            market_data_is_proxy=p.market_data_is_proxy
        )
    
    # Static fallback for bootstrap and backward compatibility
    sym = canonical_symbol.replace("-", "/").strip().upper()
    if sym in PRODUCT_MAPPINGS:
        return PRODUCT_MAPPINGS[sym]
        
    prod_id = sym.replace("/", "-")
    return ProductMapping(
        canonical_symbol=sym,
        execution_product_id=prod_id,
        market_data_product_id=prod_id,
        market_data_is_proxy=False
    )

def get_conversion_rate_to_usdc(quote: str, get_mark_func) -> float | None:
    """
    Build/reuse a fresh quote-conversion graph from Coinbase products/marks (Blocker E / R2)
    Uses a synchronous shortest path BFS across the real, fresh price edges.
    """
    quote = quote.upper()
    if quote == "USDC":
        return 1.0

    products = registry.list_products()
    graph = {}
    
    def add_edge(u, v, rate):
        if u not in graph:
            graph[u] = []
        graph[u].append((v, rate))

    for p in products:
        symbol = p.canonical_symbol
        mark_data = get_mark_func(symbol)
        if mark_data is not None and mark_data > 0:
            base = p.base_currency
            quote_cur = p.quote_currency
            price = float(mark_data)
            add_edge(base, quote_cur, price)
            add_edge(quote_cur, base, 1.0 / price)

    # Run BFS to find the shortest conversion path to USDC
    queue = collections.deque([(quote, 1.0)])
    visited = {quote}

    while queue:
        curr, current_rate = queue.popleft()
        if curr == "USDC":
            return current_rate

        neighbors = graph.get(curr, [])
        for neighbor, edge_rate in neighbors:
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append((neighbor, current_rate * edge_rate))

    return None
