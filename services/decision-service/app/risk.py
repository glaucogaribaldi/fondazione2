from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from .models import DecisionRequest, Proposal


LIVE_CONFIRMATION_PHRASE = "I_UNDERSTAND_LIVE_TRADING_CAN_LOSE_MONEY"


@dataclass(frozen=True)
class RiskSettings:
    allowed_symbols: frozenset[str]
    allowed_actions: frozenset[str] = frozenset({"NO_TRADE", "OPEN", "ADD", "REDUCE", "CLOSE"})
    max_allocation_pct: float = 20
    max_spread_bps: float = 35
    max_market_age_seconds: int = 90
    max_decision_ttl_seconds: int = 300
    require_stop_loss_for_buy: bool = True
    min_stop_loss_pct: float = 0.25
    max_stop_loss_pct: float = 3.0
    max_take_profit_pct: float = 8.0


@dataclass(frozen=True)
class LaneSettings:
    minimum_confidence: float
    max_position_pct: float
    max_daily_loss_pct: float
    max_open_positions: int
    cooldown_minutes: int


@dataclass(frozen=True)
class RiskResult:
    approved: bool
    action: str
    allocation_pct: float
    reasons: tuple[str, ...]
    valid_until: datetime


def evaluate_risk(
    request: DecisionRequest,
    proposal: Proposal,
    global_settings: RiskSettings,
    lane_settings: LaneSettings,
    *,
    now: datetime | None = None,
    live_enabled: bool = False,
    live_confirmation: str = "",
) -> RiskResult:
    current_time = now or datetime.now(UTC)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=UTC)

    market_time = request.market.timestamp
    if market_time.tzinfo is None:
        market_time = market_time.replace(tzinfo=UTC)

    reasons: list[str] = []
    spread_bps = ((request.market.ask - request.market.bid) / request.market.bid) * 10_000
    market_age = (current_time - market_time).total_seconds()

    # 1. Symbol restriction
    if request.symbol not in global_settings.allowed_symbols:
        reasons.append("SYMBOL_NOT_ALLOWED")

    # 1b. Action restriction
    if proposal.action not in global_settings.allowed_actions:
        reasons.append("ACTION_NOT_ALLOWED")

    # 2. Market data freshness (HST-05)
    if market_age < -5 or market_age > global_settings.max_market_age_seconds:
        reasons.append("STALE_MARKET_DATA")

    # 3. Spread check
    if spread_bps > global_settings.max_spread_bps:
        reasons.append("SPREAD_TOO_WIDE")

    # 4. Confidence check (Only for new exposure entries)
    if proposal.action in ("OPEN", "ADD") and proposal.confidence < lane_settings.minimum_confidence:
        reasons.append("CONFIDENCE_TOO_LOW")

    # 5. Position Sizing Semantics (HST-04) and limits (Only for entries)
    if proposal.action in ("OPEN", "ADD"):
        if proposal.allocation_pct > min(
            global_settings.max_allocation_pct, lane_settings.max_position_pct
        ):
            reasons.append("ALLOCATION_LIMIT")

    # 6. Portfolio-level loss constraints
    if request.portfolio.daily_pnl_pct <= -lane_settings.max_daily_loss_pct:
        reasons.append("DAILY_LOSS_LIMIT")

    # 7. Position counts
    if (
        request.portfolio.open_positions >= lane_settings.max_open_positions
        and proposal.action == "OPEN"
    ):
        reasons.append("OPEN_POSITION_LIMIT")

    # 8. Cooldown - strictly applied only to ENTRIES/OPEN/ADD, never EXITS/REDUCES/CLOSE (HST-03)
    if proposal.action in ("OPEN", "ADD") and request.portfolio.last_trade_at:
        last_trade = request.portfolio.last_trade_at
        if last_trade.tzinfo is None:
            last_trade = last_trade.replace(tzinfo=UTC)
        if current_time - last_trade < timedelta(minutes=lane_settings.cooldown_minutes):
            reasons.append("COOLDOWN_ACTIVE")

    # 9. Protective SL/TP validation
    if proposal.action == "OPEN" and global_settings.require_stop_loss_for_buy:
        if proposal.stop_loss_pct is None:
            reasons.append("STOP_LOSS_REQUIRED")
        elif not (
            global_settings.min_stop_loss_pct
            <= proposal.stop_loss_pct
            <= global_settings.max_stop_loss_pct
        ):
            reasons.append("STOP_LOSS_OUT_OF_RANGE")

    if proposal.take_profit_pct and proposal.take_profit_pct > global_settings.max_take_profit_pct:
        reasons.append("TAKE_PROFIT_OUT_OF_RANGE")

    # 10. Live check
    if request.mode == "live" and (
        not live_enabled or live_confirmation != LIVE_CONFIRMATION_PHRASE
    ):
        reasons.append("LIVE_TRADING_LOCKED")

    # NO_TRADE actions preserve model reason codes
    if proposal.action == "NO_TRADE":
        reasons.extend(code for code in proposal.reason_codes if code not in reasons)
        return RiskResult(
            approved=True,
            action="NO_TRADE",
            allocation_pct=0,
            reasons=tuple(reasons or ["MODEL_NO_TRADE"]),
            valid_until=current_time + timedelta(seconds=60),
        )

    # If any safety check fails, force NO_TRADE (fail-closed)
    if reasons:
        return RiskResult(
            approved=False,
            action="NO_TRADE",
            allocation_pct=0,
            reasons=tuple(reasons),
            valid_until=current_time + timedelta(seconds=30),
        )

    # Approved!
    return RiskResult(
        approved=True,
        action=proposal.action,
        allocation_pct=proposal.allocation_pct,
        reasons=tuple(proposal.reason_codes or ["RISK_APPROVED"]),
        valid_until=current_time
        + timedelta(seconds=min(300, global_settings.max_decision_ttl_seconds)),
    )
