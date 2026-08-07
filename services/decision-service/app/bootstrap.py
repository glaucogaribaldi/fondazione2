from .models import DecisionRequest, Proposal


def should_run_bootstrap_probe(request: DecisionRequest, settings: dict) -> bool:
    return (
        settings["enabled"]
        and request.mode == "paper"
        and request.symbol == settings["symbol"]
        and request.request_id.startswith(settings["request_id_prefix"])
        and request.portfolio.last_trade_at is None
        and request.portfolio.open_positions == 0
        and request.portfolio.current_position_pct == 0
    )


def bootstrap_probe_proposal(settings: dict) -> Proposal:
    return Proposal(
        action="BUY",
        allocation_pct=settings["allocation_pct"],
        confidence=1.0,
        stop_loss_pct=settings["stop_loss_pct"],
        take_profit_pct=settings["take_profit_pct"],
        reason_codes=["BOOTSTRAP_PROBE"],
    )
