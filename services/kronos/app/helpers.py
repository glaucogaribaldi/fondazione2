from datetime import timedelta


def timeframe_delta(timeframe: str) -> timedelta:
    units = {"m": "minutes", "h": "hours", "d": "days"}
    try:
        amount = int(timeframe[:-1])
        unit = units[timeframe[-1]]
    except (ValueError, KeyError, IndexError) as exc:
        raise ValueError(f"unsupported timeframe: {timeframe}") from exc
    return timedelta(**{unit: amount})
