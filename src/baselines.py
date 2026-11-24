"""Local reference strategies and deterministic execution checks (no model fitting)."""
import csv
from dataclasses import asdict
from datetime import date
from decimal import Decimal

from src.execution import run_account, sma20_target
from src.fetch_market import MARKET, ROOT, SYMBOLS, write_csv
from src.market_data import load_bars
from src.prepare import PREPARED
from src.rights import load_sessions

OUTPUT = ROOT / "data/local/baselines"


def metrics(ledger):
    peak, drawdown = 10_000_000., 0.
    for entry in ledger:
        value = float(entry.booked_equity)
        peak = max(peak, value)
        drawdown = max(drawdown, 1-value/peak)
    end = ledger[-1]
    return {"booked_return_pct": round((float(end.booked_equity)/10_000_000-1)*100, 4),
            "booked_drawdown_pct": round(drawdown*100, 4),
            "fills": sum(e.filled_qty > 0 for e in ledger), "holding_days": sum(e.shares > 0 for e in ledger),
            "unpriced_rights_at_end": end.pending_rights}


def confirmed_later(period, ledger):
    """Ex-post addendum only. Do not feed later dividend amounts into any decision or reward."""
    last = period[-1].day
    extra = Decimal(0)
    for bar, entry in zip(period, ledger):
        if not entry.prior_shares:
            continue
        for right in bar.dividends:
            if right.per_share is None:
                return None
            if right.known_day > last:
                extra += entry.prior_shares * right.per_share
    return round(float((ledger[-1].booked_equity+extra)/Decimal(10_000_000)-1)*100,4)


def main():
    summaries = []
    sensitivity = []
    for s in SYMBOLS:
        with (PREPARED / f"{s}.csv").open(encoding="utf-8") as f:
            coverage = {r["date"]: r["news_status"] for r in csv.DictReader(f)}
        sessions = load_sessions(s)
        bars = load_bars(MARKET/"exchange"/f"{s}.csv")
        dates = sorted(bars)
        closes = [bars[d]["close"] for d in dates]
        trend = {date.fromisoformat(d): sma20_target(closes[max(0,i-19):i+1]) for i,d in enumerate(dates)}
        for y in (2020, 2021, 2022, 2023, 2024):
            period = [b for b in sessions if b.day.year == y]
            plans = {"cash": {}, "buy_hold": {period[0].previous_session: True}, "ma20": trend,
                     "ma20_same_coverage": {d:v for d,v in trend.items() if coverage.get(d.isoformat()) == "complete"}}
            for name, decisions in plans.items():
                ledger = run_account(period, decisions)
                summaries.append({"symbol":s,"year":y,"strategy":name,**metrics(ledger),
                                  "return_with_subsequently_confirmed_rights_pct": confirmed_later(period,ledger)})
                records = [asdict(e) for e in ledger]
                write_csv(OUTPUT/s/str(y)/f"{name}.csv", list(records[0]), records)
                for slip in ("0", ".0005", ".001"):
                    variant = ledger if slip == ".0005" else run_account(period, decisions,slippage=slip)
                    sensitivity.append({"symbol":s,"year":y,"strategy":name,"one_way_slippage":slip,**metrics(variant)})
        if s == "005930":
            # Isolate one real market interval, with hand-set decisions, to check accounting.
            days = [b for b in sessions if date(2020,1,17) <= b.day <= date(2020,1,21)]
            demo = run_account(days, {date(2020,1,16):True, date(2020,1,17):True, date(2020,1,20):False})
            records = [asdict(e) for e in demo]
            write_csv(OUTPUT/"samsung-example.csv", list(records[0]), records)
    write_csv(OUTPUT/"annual.csv", list(summaries[0]), summaries)
    write_csv(OUTPUT/"cost-sensitivity.csv", list(sensitivity[0]), sensitivity)
    print(f"{len(summaries)} stock/year/baseline ledgers")


if __name__ == "__main__":
    main()
