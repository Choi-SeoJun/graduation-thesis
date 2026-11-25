"""Count fixed double-bottom (W) and double-top (M) confirmations from development closes."""
import csv
from bisect import bisect_left
from collections import Counter
from itertools import pairwise

from src.fetch_market import END, MARKET, ROOT, START, SYMBOLS, write_csv


STAGES = ("pivots", "consecutive_pairs", "gap_22_45", "similar_2pct", "neck_3pct", "confirmed_days")


def pattern_days(closes, *, first_report=0, audit=None):
    """Count stages by second pivot date, and final signals by distinct confirmation date."""
    signals = set()
    for shape in ("W", "M"):
        extreme = min if shape == "W" else max
        pivots = []
        for i in range(2, len(closes) - 2):
            window = closes[i-2:i+3]
            if closes[i] == extreme(window) and window.count(closes[i]) == 1:
                pivots.append(i)
        stages = dict.fromkeys(STAGES, 0)
        stages["pivots"] = sum(i >= first_report for i in pivots)
        for first, second in pairwise(pivots):
            in_period = second >= first_report
            stages["consecutive_pairs"] += in_period
            if not 22 <= second - first <= 45:
                continue
            stages["gap_22_45"] += in_period
            a, b = closes[first], closes[second]
            if 100 * abs(a - b) > a + b:  # Difference <= 2% of their mean.
                continue
            stages["similar_2pct"] += in_period
            middle = closes[first+1:second]
            if shape == "W":
                neck = max(middle)
                if 100 * neck < 103 * max(a, b):
                    continue
            else:
                neck = min(middle)
                if 100 * neck > 97 * min(a, b):
                    continue
            stages["neck_3pct"] += in_period
            # Check from pivot+1 so a premature crossing invalidates this pair.
            # Pivot+2 is the first close at which this second pivot was knowable.
            for i in range(second + 1, min(second + 11, len(closes))):
                crossed = (closes[i-1] <= neck < closes[i] if shape == "W"
                           else closes[i-1] >= neck > closes[i])
                if crossed:
                    if i >= second + 2 and i >= first_report:
                        signals.add((i, shape))
                    break
        stages["confirmed_days"] = sum(kind == shape for _, kind in signals)
        if audit is not None:
            audit[shape] = stages
    return signals


def main():
    rows = []
    funnels = []
    for symbol in SYMBOLS:
        days, closes = [], []
        with (MARKET / "exchange" / f"{symbol}.csv").open(encoding="utf-8", newline="") as stream:
            for row in csv.DictReader(stream):
                if not START.isoformat() <= row["date"] <= END.isoformat():
                    raise ValueError("Expected development-only source with the 2019 warm-up")
                days.append(row["date"])
                closes.append(int(row["close"]))
        if (days != sorted(set(days)) or len(days) != 1272 or days[0] != START.isoformat()
                or days[-1] != "2024-12-30" or min(closes) <= 0):
            raise ValueError(f"Invalid close history for {symbol}")
        audit = {}
        signals = pattern_days(closes, first_report=bisect_left(days, "2020-01-01"), audit=audit)
        counts = Counter((int(days[i][:4]), shape) for i, shape in signals)
        for shape, stages in audit.items():
            funnels.append({"symbol": symbol, "shape": shape, **stages})
        for year in range(2020, 2025):
            rows.append({"symbol": symbol, "year": year, "W": counts[year,"W"], "M": counts[year,"M"]})
    write_csv(ROOT / "research/chart_pattern_counts.psv", ("symbol", "year", "W", "M"), rows, delimiter="|")
    write_csv(ROOT / "research/chart_pattern_funnel.psv", ("symbol", "shape", *STAGES), funnels, delimiter="|")
    print(f"{len(rows)} stock-years; W={sum(r['W'] for r in rows)}, M={sum(r['M'] for r in rows)}")


if __name__ == "__main__":
    main()
