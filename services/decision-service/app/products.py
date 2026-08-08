from pydantic import BaseModel

class ProductMapping(BaseModel):
    canonical_symbol: str
    execution_product_id: str
    market_data_product_id: str
    market_data_is_proxy: bool

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
    # Standardize format e.g. BTC/USDC
    sym = canonical_symbol.replace("-", "/").strip().upper()
    if sym in PRODUCT_MAPPINGS:
        return PRODUCT_MAPPINGS[sym]
    # Fallback to direct mapping
    prod_id = sym.replace("/", "-")
    return ProductMapping(
        canonical_symbol=sym,
        execution_product_id=prod_id,
        market_data_product_id=prod_id,
        market_data_is_proxy=False
    )
