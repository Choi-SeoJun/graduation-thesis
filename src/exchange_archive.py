"""Recover unadjusted trade prices from a frozen, KRX-derived Marcap snapshot."""
import csv
import gzip
import hashlib
import io

from src.fetch_market import FIELDS, MARKET, START, END, SYMBOLS, fetch, write_csv, write_json

REVISION = "eeb328584b54211da285a74c68d6e1d5dc7d6a91"


def extract():
    collected = {symbol: {} for symbol in SYMBOLS}
    sources = []
    for year in range(START.year, END.year + 1):
        url = f"https://raw.githubusercontent.com/FinanceData/marcap/{REVISION}/data/marcap-{year}.csv.gz"
        path = MARKET / "archive" / f"{REVISION}-{year}.csv.gz"
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            payload = fetch(url)
            if not payload.startswith(b"\x1f\x8b"):
                raise ValueError(f"Invalid archive: {year}")
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(payload)
            tmp.replace(path)
        sources.append({"year": year, "url": url, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                d, s = row["Date"], row["Code"]
                if s not in collected or not START.isoformat() <= d <= END.isoformat():
                    continue
                if d in collected[s] or row["Market"] != "KOSPI":
                    raise ValueError(f"Duplicate or wrong market: {s} {d}")
                prices = {field: int(float(row[field.title()])) for field in FIELDS[1:]}
                collected[s][d] = {"date": d, **prices}
    with (MARKET / "raw/KOSPI.csv").open(encoding="utf-8") as f:
        calendar = {r["date"] for r in csv.DictReader(f)}
    for s, rows in collected.items():
        if set(rows) != calendar:
            raise ValueError(f"{s}: index/archive session mismatch: {sorted(calendar ^ set(rows))}")
        write_csv(MARKET / "exchange" / f"{s}.csv", FIELDS, (rows[d] for d in sorted(rows)))
    return collected, sources


def main():
    collected, sources = extract()
    audits = {}
    for symbol, rows in collected.items():
        with (MARKET / "raw" / f"{symbol}.csv").open(encoding="utf-8") as f:
            other = {r["date"]: r for r in csv.DictReader(f)}
        differences = {k: sum(rows[d][k] != float(other[d][k]) for d in rows) for k in FIELDS[1:]}
        audits[symbol] = {"rows": len(rows), "differences_vs_naver": differences}
        print(symbol, len(rows), differences)
    write_json(MARKET / "exchange/audit.json", {"revision": REVISION, "sources": sources, "audit": audits})


if __name__ == "__main__":
    main()
