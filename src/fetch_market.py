"""Collect period-bounded development data. Local research copies stay outside Git."""
import ast
import csv
import hashlib
import io
import json
import os
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "data/local/market"
START = date(2019, 11, 1)
END = date(2024, 12, 31)
KST = timezone(timedelta(hours=9))
SYMBOLS = ("005930", "000660", "035420")
FIELDS = ("date", "open", "high", "low", "close", "volume")


def atomic_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(text.encode("utf-8"))
    os.replace(tmp, path)


def write_csv(path, fields, rows):
    buf = io.StringIO(newline="")
    writer = csv.DictWriter(buf, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    atomic_text(path, buf.getvalue())


def write_json(path, value):
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def fetch(url):
    for attempt in range(3):
        try:
            req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urlopen(req, timeout=25) as response:
                return response.read()
        except OSError:
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)


def ts(day):
    return int(datetime.combine(day, datetime.min.time(), tzinfo=KST).timestamp())


def day_at(timestamp):
    return datetime.fromtimestamp(timestamp, KST).date()


def check_day(day):
    if not START <= day <= END:
        raise ValueError(f"Unexpected provider date {day}")


def collect(symbol):
    params = {"symbol": symbol, "requestType": 1, "startTime": START.strftime("%Y%m%d"),
              "endTime": END.strftime("%Y%m%d"), "timeframe": "day"}
    url = "https://api.finance.naver.com/siseJson.naver?" + urlencode(params)
    payload = fetch(url)
    rows = []
    for day_text, o, h, l, c, volume, *_ in ast.literal_eval(payload.decode("utf-8").strip())[1:]:
        day = datetime.strptime(day_text, "%Y%m%d").date()
        check_day(day)
        rows.append(dict(zip(FIELDS, (day.isoformat(), o, h, l, c, volume))))
    by_day = {r["date"]: r for r in rows}
    if len(by_day) != len(rows) or not rows or min(by_day) != START.isoformat() or max(by_day) != "2024-12-30":
        raise ValueError(f"{symbol}: duplicate or incomplete price series")

    ticker = "^KS11" if symbol == "KOSPI" else symbol + ".KS"
    endpoint = "https://query1.finance.yahoo.com/v8/finance/chart/" + quote(ticker, safe="")
    yurl = endpoint + "?" + urlencode({"period1": ts(START), "period2": ts(END + timedelta(days=1)),
                                      "interval": "1d", "events": "div,splits"})
    chart = json.loads(fetch(yurl))["chart"]["result"][0]
    quotes = chart["indicators"]["quote"][0]
    # Yahoo quote metadata can include the current price. Only dated development bars are retained.
    cross = {}
    for i, stamp in enumerate(chart["timestamp"]):
        day = day_at(stamp)
        check_day(day)
        if day.isoformat() in cross:
            raise ValueError(f"{ticker}: duplicate comparison date {day}")
        cross[day.isoformat()] = {k: quotes[k][i] for k in FIELDS[1:]}
    events = []
    for kind, entries in chart.get("events", {}).items():
        for entry in entries.values():
            day = day_at(entry["date"])
            check_day(day)
            # Entitlement is a development-period fact. Its later monetary value was
            # still unannounced at the cutoff and must not travel with the dataset.
            detail = {"date": entry["date"]} if kind == "dividends" else entry
            events.append({"date": day.isoformat(), "kind": kind, "detail": detail})
    # Compare displayed OHLC at the precision of the index feed and the equity tick.
    mismatches = []
    for d in sorted(set(by_day) & set(cross)):
        for field in ("open", "high", "low", "close"):
            value = cross[d][field]
            if value is None or abs(value - by_day[d][field]) > (0.011 if symbol == "KOSPI" else 0.1):
                mismatches.append({"date": d, "field": field, "primary": by_day[d][field], "comparison": value})
    write_csv(MARKET / "raw" / (symbol + ".csv"), FIELDS, rows)
    write_csv(MARKET / "comparison" / (symbol + ".csv"), FIELDS,
              ({"date": d, **values} for d, values in sorted(cross.items())))
    write_json(MARKET / "actions" / (symbol + ".json"), events)
    return {"symbol": symbol, "naver_url": url, "naver_sha256": hashlib.sha256(payload).hexdigest(),
            "comparison_url": yurl, "rows": len(rows), "comparison_rows": len(cross),
            "first": min(by_day), "last": max(by_day),
            "only_primary": sorted(set(by_day) - set(cross)), "only_comparison": sorted(set(cross) - set(by_day)),
            "comparison_ohlc_differences": mismatches,
            "cash_dividend_candidates": sum(e["kind"] == "dividends" for e in events),
            "split_candidates": sum(e["kind"] == "splits" for e in events)}


def main():
    summaries = []
    for symbol in (*SYMBOLS, "KOSPI"):
        audit = collect(symbol)
        summaries.append(audit)
        print(symbol, audit["rows"], "OHLC differences", len(audit["comparison_ohlc_differences"]))
    write_json(MARKET / "sources.json", summaries)


if __name__ == "__main__":
    main()
