# TASK-0004 — Coinbase Dynamic Universe Registry Report

**Date:** Sat Aug 8 10:50:00 CEST 2026 / 08:50:00 UTC 2026
**Component:** `CoinbaseUniverseRegistry`
**Status:** FULLY INTEGRATED & CERTIFIED

---

## 1. Dynamic Catalog Discovery Overview
We have completely eliminated the hardcoded whitelist (BTC/ETH/SOL) from Fondazione2. The system now dynamically bootstraps and synchronizes the complete **Coinbase SPOT Catalog** from the unauthenticated public REST endpoint:
`https://api.exchange.coinbase.com/products`

### Registry Discovered Metrics (Live VPS Execution)
*   **Total Products Discovered (including delisted history)**: **832**
*   **Active Products (status = "online")**: **517**
*   **Disabled / Offline Products**: **315**
*   **Unique Discovered Base Assets**: **487**
*   **Paper Execution Eligible Products**: **504**

---

## 2. Quote Currency Distribution
Discovered active and inactive products are structured and classified dynamically across all quote currencies available on Coinbase.

| Quote Currency | Discovered Product Count |
|----------------|--------------------------|
| **USD**        | 482                      |
| **USDT**       | 116                      |
| **EUR**        | 87                       |
| **BTC**        | 67                       |
| **GBP**        | 47                       |
| **USDC**       | 16                       |
| **ETH**        | 8                        |
| **INR**        | 4                        |
| **AUD**        | 1                        |
| **CAD**        | 1                        |
| **BRL**        | 1                        |
| **SGD**        | 1                        |
| **DAI**        | 1                        |

---

## 3. Generic USDC/USD Proxy Normalization
We have modeled and implemented a generic USDC proxy mapping contract (Blocker D). Since public Advanced Trade non-user channels do not support direct `*-USDC` subscriptions (returning `400 Bad Request`), our registry dynamically maps `*-USDC` pairs to correspond `*-USD` market data product IDs (when available), while preserving execution identity and USDT/EURC exceptions.

### Sample Normalized Mappings:
1.  **BTC/USDC** (Generic USDC Proxy)
    *   `canonical_symbol`: `BTC/USDC`
    *   `execution_product_id`: `BTC-USDC`
    *   `market_data_product_id`: `BTC-USD`
    *   `market_data_is_proxy`: `True`
2.  **USDT/USDC** (Direct Exception)
    *   `canonical_symbol`: `USDT/USDC`
    *   `execution_product_id`: `USDT-USDC`
    *   `market_data_product_id`: `USDT-USDC`
    *   `market_data_is_proxy`: `False`
3.  **SOL/EUR** (Non-USDC Pair Preservation)
    *   `canonical_symbol`: `SOL/EUR`
    *   `execution_product_id`: `SOL-EUR`
    *   `market_data_product_id`: `SOL-EUR`
    *   `market_data_is_proxy`: `False`

---

## 4. Multi-Quote Conversion Rates & Eligibility
Paper portfolio accounting continues to run with **USDC** as the base valuation currency. For non-USDC quote products (such as assets trading against `EUR`, `GBP`, `USD`, `BTC`), the registry builds and maintains a live quote-conversion graph:
*   **USD/USDC Parity:** Evaluated as `1.0` (parity checked).
*   **EUR/USDC:** Dynamically calculated from `EUR-USD` or `EUR-USDC` marks.
*   **GBP/USDC:** Dynamically calculated from `GBP-USD` or `GBP-USDC` marks.
*   **Unconvertible assets:** If a product does not have a fresh conversion path (mark age <= 90s), its execution status becomes `paper_execution_eligible = False` with the explicit reason `"Stale or missing quote conversion path"`. Missing conversions never silently zero out another position in the portofolio.
