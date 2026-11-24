"""Point-in-time price features and forward labels for each stock separately."""
import csv
import math
from datetime import date
from statistics import mean, stdev

from src.fetch_market import FIELDS as BAR_FIELDS, MARKET, START, END, SYMBOLS, write_csv

FEATURES = ("ret_5", "ma_gap_20", "volatility_20", "market_ret_20",
            "relative_strength_20", "relative_volume_20")
HORIZONS = (1, 5, 10)


def load_bars(path):
    bars = {}
    with path.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            d = date.fromisoformat(row["date"])
            if not START <= d <= END or d.isoformat() in bars:
                raise ValueError(f"Unexpected or duplicate date in {path}: {d}")
            bar = {k: float(row[k]) for k in BAR_FIELDS[1:]}
            o, h, l, c, v = (bar[k] for k in BAR_FIELDS[1:])
            if not all(math.isfinite(x) for x in bar.values()) or v < 0 or (v and not 0 < l <= min(o,c) <= max(o,c) <= h):
                raise ValueError(f"Invalid OHLCV in {path}: {d}")
            bars[d.isoformat()] = bar
    return bars


def make_rows(symbol, bars, market, splits=None):
    sessions = sorted(market)
    splits = splits or {}
    if (set(bars) | set(splits)) - set(sessions):
        raise ValueError("Date outside the index calendar")
    closes, opens, volumes, factors = [], [], [], []
    factor = 1.
    for d in sessions:
        ratio = splits.get(d, 1.)
        if not math.isfinite(ratio) or ratio <= 0:
            raise ValueError(f"Invalid split {d}")
        factor *= ratio
        factors.append(factor)
        bar = bars.get(d)
        traded = bar and bar["volume"] > 0 and min(bar["close"], bar["open"]) > 0
        opens.append(bar["open"] if traded else None)
        closes.append(bar["close"] * factor if traded else None)
        volumes.append(bar["volume"] / factor if traded else None)
    for i, d in enumerate(sessions):
        if d < "2020-01-01":
            continue
        row = {"date": d, "symbol": symbol, **dict.fromkeys(BAR_FIELDS[1:]), **bars.get(d, {}),
               **dict.fromkeys(FEATURES)}
        if i >= 20:
            w = closes[i-20:i+1]
            m = market[d]["close"] / market[sessions[i-20]]["close"] - 1
            row["market_ret_20"] = m
            if None not in w[-6:]:
                row["ret_5"] = w[-1] / w[-6] - 1
            if None not in w[-20:]:
                row["ma_gap_20"] = w[-1] / mean(w[-20:]) - 1
            if None not in w:
                row["volatility_20"] = stdev(w[j] / w[j-1] - 1 for j in range(1, 21))
                row["relative_strength_20"] = w[-1] / w[0] - 1 - m
            vv = volumes[i-20:i+1]
            if None not in vv and sum(vv[:-1]) > 0:
                row["relative_volume_20"] = vv[-1] / mean(vv[:-1])
        row["next_open_date"] = sessions[i+1] if i+1 < len(sessions) else None
        row["next_open"] = opens[i+1] if i+1 < len(sessions) else None
        for horizon in HORIZONS:
            end = i + 1 + horizon
            row[f"label_end_date_{horizon}"] = sessions[end] if end < len(sessions) else None
            target = None
            if end < len(sessions) and opens[end] is not None and row["next_open"] is not None:
                target = int(opens[end] * factors[end] > opens[i+1] * factors[i+1])
            row[f"target_up_{horizon}"] = target
        yield row


def main():
    import json
    market = load_bars(MARKET / "raw/KOSPI.csv")
    if abs(market["2019-12-30"]["close"] - 2197.67) > .005:
        raise ValueError("KOSPI base date mismatch")
    rows = []
    for symbol, anchor in zip(SYMBOLS, (55800, 94100, 186500)):
        bars = load_bars(MARKET / "exchange" / f"{symbol}.csv")
        if set(bars) != set(market) or bars["2019-12-30"]["close"] != anchor:
            raise ValueError(f"{symbol}: calendar or selection date mismatch")
        events = json.loads((MARKET / "actions" / f"{symbol}.json").read_text())
        splits = {e["date"]: e["detail"]["numerator"] / e["detail"]["denominator"]
                  for e in events if e["kind"] == "splits"}
        rows.extend(make_rows(symbol, bars, market, splits))
    write_csv(MARKET / "processed/market_features.csv", list(rows[0]), rows)
    print(f"{len(rows)} stock-sessions prepared")


if __name__ == "__main__":
    main()
