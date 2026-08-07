from datetime import datetime
from typing import Literal, Any
from pydantic import BaseModel, Field, model_validator

Action = Literal["NO_TRADE", "OPEN", "ADD", "REDUCE", "CLOSE"]


class Candle(BaseModel):
    timestamp: datetime
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: float = Field(ge=0)

    @model_validator(mode="after")
    def validate_ohlc(self) -> "Candle":
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise ValueError("inconsistent OHLC candle")
        if self.high < self.low:
            raise ValueError("high must be greater than or equal to low")
        return self


class MarketSnapshot(BaseModel):
    timestamp: datetime
    bid: float = Field(gt=0)
    ask: float = Field(gt=0)
    candles: list[Candle] = Field(min_length=32, max_length=512)

    @model_validator(mode="after")
    def validate_book(self) -> "MarketSnapshot":
        if self.ask < self.bid:
            raise ValueError("ask must be greater than or equal to bid")
        return self


class PortfolioSnapshot(BaseModel):
    equity: float = Field(gt=0)
    cash: float = Field(ge=0)
    daily_pnl_pct: float
    open_positions: int = Field(ge=0)
    current_position_pct: float = Field(ge=0, le=100)
    last_trade_at: datetime | None = None


class DecisionRequest(BaseModel):
    request_id: str
    mode: Literal["paper", "shadow", "live"] = "paper"
    lane_id: str
    symbol: str
    timeframe: str
    market: MarketSnapshot
    portfolio: PortfolioSnapshot


class Forecast(BaseModel):
    direction: Literal["up", "down", "flat"]
    expected_return_pct: float
    confidence: float = Field(ge=0, le=1)
    volatility: float = Field(ge=0)
    model: str


class Proposal(BaseModel):
    action: Action
    allocation_pct: float = Field(ge=0, le=100)
    confidence: float = Field(ge=0, le=1)
    stop_loss_pct: float | None = Field(default=None, gt=0)
    take_profit_pct: float | None = Field(default=None, gt=0)
    reason_codes: list[str] = Field(default_factory=list)


class DecisionResponse(BaseModel):
    request_id: str
    lane_id: str
    symbol: str
    decision: Action
    allocation_pct: float
    confidence: float
    stop_loss_pct: float | None
    take_profit_pct: float | None
    valid_until: datetime
    approved_by_risk_engine: bool
    reason_codes: list[str]
    model_versions: dict[str, str]


class RiskDecision(BaseModel):
    risk_decision_id: str
    candidate_id: str
    result: Literal["APPROVE", "MODIFY_DOWN", "REJECT", "PROTECTIVE_EXIT"]
    approved_action: Action
    approved_quantity: float = 0.0
    limits_snapshot: dict[str, Any] = Field(default_factory=dict)
    reason_codes: list[str] = Field(default_factory=list)
    expires_at: datetime


class ExecutionIntent(BaseModel):
    execution_intent_id: str
    risk_decision_id: str
    mode: Literal["paper", "live"]
    symbol: str
    action: Literal["OPEN", "ADD", "REDUCE", "CLOSE"]
    side: Literal["BUY", "SELL"]
    quantity: float
    order_type: Literal["MARKET", "LIMIT", "STOP", "STOP_LIMIT"] = "MARKET"
    limit_price: float | None = None
    stop_price: float | None = None
    take_profit_price: float | None = None
    time_exit_at: datetime | None = None
    client_order_id: str
    created_at: datetime
    expires_at: datetime


class ExecutionResult(BaseModel):
    execution_intent_id: str
    broker_order_id: str
    status: Literal["PENDING", "PARTIAL", "FILLED", "CANCELLED", "REJECTED", "FAILED"]
    requested_quantity: float
    filled_quantity: float
    average_fill_price: float | None = None
    fee: float = 0.0
    slippage: float = 0.0
    fills: list[dict[str, Any]] = Field(default_factory=list)
    updated_at: datetime
    reason_codes: list[str] = Field(default_factory=list)
