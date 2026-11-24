"""Trading sessions and declared gross cash dividends for the development period."""
import csv
import json
from bisect import bisect_right
from collections import defaultdict
from datetime import date
from decimal import Decimal

from src.execution import Dividend, Session
from src.fetch_market import MARKET, ROOT, SYMBOLS
from src.market_data import load_bars


def load_sessions(symbol):
    if symbol not in SYMBOLS:
        raise ValueError("Unknown study stock")
    bars = load_bars(MARKET / "exchange" / f"{symbol}.csv")
    sessions = sorted(date.fromisoformat(d) for d in bars)
    feed = json.loads((MARKET / "actions" / f"{symbol}.json").read_text(encoding="utf-8"))
    ex_dates = {e["date"] for e in feed if e["kind"] == "dividends"}
    if any(e["kind"] != "dividends" for e in feed):
        raise ValueError("Review new corporate action before simulation")
    rights = defaultdict(list)
    with (ROOT / "research/dividends.psv").open(encoding="utf-8") as f:
        for row in csv.DictReader(f, delimiter="|"):
            if row["symbol"] != symbol:
                continue
            # For these record dates, the ex-date is the penultimate eligible session.
            # Require an independently matching historical ex-date before using the rule.
            record = date.fromisoformat(row["record_date"])
            ex_day = sessions[bisect_right(sessions, record)-2]
            if ex_day in rights:
                raise ValueError(f"Duplicate entitlement {symbol} {ex_day}")
            if not row["amount"]:
                if (symbol, row["record_date"]) != ("005930", "2024-12-31"):
                    raise ValueError("Unexpected unknown dividend")
                rights[ex_day].append(Dividend(None, None, None))
                continue
            announced = date.fromisoformat(row["announced"])
            # An announcement known only to the day is usable at the following trading close.
            available = sessions[bisect_right(sessions, announced)]
            pay = date.fromisoformat(row["paid_date"])
            first, paid = Decimal(row["amount"]), Decimal(row["paid_amount"])
            rights[ex_day].append(Dividend(first, pay, available))
            if first != paid:
                if (symbol, row["record_date"], paid-first) != ("035420", "2021-12-31", Decimal(10)):
                    raise ValueError("Unreconciled dividend revision")
                correction = sessions[bisect_right(sessions, date(2022, 3, 4))]
                rights[ex_day].append(Dividend(paid-first, pay, correction))
    if {d.isoformat() for d in rights} != ex_dates:
        raise ValueError(f"Ex-dates disagree for {symbol}")
    result = []
    for i, day in enumerate(sessions):
        if day.year < 2020:
            continue
        b = bars[day.isoformat()]
        if min(b["volume"], b["open"], b["close"]) <= 0:
            raise ValueError(f"Trading status needs review: {symbol} {day}")
        result.append(Session(day, sessions[i-1], Decimal(str(b["open"])), Decimal(str(b["close"])),
                              "open", True, dividends=tuple(rights.get(day, ()))))
    return result
