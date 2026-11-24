"""Join verified daily news to development prices and materialize leakage-safe fold metadata."""
import csv
from collections import Counter

from src.fetch_market import MARKET, ROOT, SYMBOLS, write_csv, write_json
from src.market_data import FEATURES, HORIZONS

PREPARED = ROOT / "data/local/prepared"
NEWS_DAILY = ROOT / "data/local/news/etoday/development/news_daily.csv"
NEWS_FIELDS = ("contract_signed", "contract_terminated", "contract_reports")
FOLD_YEARS = (2022, 2023, 2024)


def news_index(path):
    if not path.is_file():
        return {}
    records = {}
    with path.open(encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            key = (r["symbol"], r["date"])
            status = r["news_status"]
            if key in records or status not in ("complete", "unknown"):
                raise ValueError(f"Invalid day coverage: {key} {status}")
            if status == "complete":
                counts = {k: int(r[k]) for k in NEWS_FIELDS}
                # One report can announce several contracts. The unit of each count differs.
                if min(counts.values()) < 0:
                    raise ValueError(f"Invalid news counts: {key}")
            else:
                if any(r[k] not in ("", None) for k in NEWS_FIELDS):
                    raise ValueError(f"Unknown day contains news counts: {key}")
                counts = dict.fromkeys(NEWS_FIELDS)
            records[key] = {"news_status": status, **counts}
    return records


def trainable(row, year, horizon, require_news=False):
    cutoff = f"{year}-01-01"
    end = row[f"label_end_date_{horizon}"]
    return (row["date"] < cutoff and bool(end) and end < cutoff
            and all(row[k] not in ("", None) for k in FEATURES)
            and row[f"target_up_{horizon}"] in ("0", "1", 0, 1)
            and (not require_news or row["news_status"] == "complete"))


def main():
    news = news_index(NEWS_DAILY)
    missing = {"news_status": "unknown", **dict.fromkeys(NEWS_FIELDS)}
    with (MARKET / "processed/market_features.csv").open(encoding="utf-8", newline="") as f:
        all_rows = [{**r, **news.get((r["symbol"], r["date"]), missing)} for r in csv.DictReader(f)]
    keys = {(r["symbol"], r["date"]) for r in all_rows}
    if len(keys) != len(all_rows) or (set(news) and set(news) != keys):
        raise ValueError("Price/news session panels disagree")
    folds = []
    for symbol in SYMBOLS:
        rows = [r for r in all_rows if r["symbol"] == symbol]
        if not rows or any(r["date"][:4] not in ("2020","2021","2022","2023","2024") for r in rows):
            raise ValueError("Data outside the permitted development years")
        write_csv(PREPARED / f"{symbol}.csv", list(rows[0]), rows)
        for year in FOLD_YEARS:
            validation = [r for r in rows if r["date"].startswith(str(year))]
            for h in HORIZONS:
                folds.append({"symbol": symbol, "validation_year": year, "horizon": h,
                              "train_start": rows[0]["date"], "train_end": f"{year-1}-12-31",
                              "train_price": sum(trainable(r, year, h) for r in rows),
                              "train_paired": sum(trainable(r, year, h, True) for r in rows),
                              "validation_sessions": len(validation),
                              "validation_news_complete": sum(r["news_status"] == "complete" for r in validation)})
    write_csv(PREPARED / "folds.csv", list(folds[0]), folds)
    summary = Counter((r["symbol"], r["news_status"]) for r in all_rows)
    write_json(PREPARED / "inventory.json", {s: {k: summary[s,k] for k in ("complete", "unknown")} for s in SYMBOLS})
    print(f"{len(all_rows)} stock-sessions, {len(folds)} stock/year/horizon definitions")


if __name__ == "__main__":
    main()
