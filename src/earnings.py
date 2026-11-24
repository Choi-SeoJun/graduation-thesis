"""Validate the development-period quarterly earnings ledger and align publication pulses."""
import hashlib
import json
import math
import re
from datetime import date, datetime

from src.fetch_market import KST, ROOT, SYMBOLS
from src.news_archive import ROOT as ARCHIVE, read
from src.news_events import signal_day


def expected_quarter(day):
    quarter = (day.month - 1) // 3
    return f"{day.year}Q{quarter}" if quarter else f"{day.year - 1}Q4"


def validate_records(records):
    """Require a reviewed first release for every stock-quarter before encoding no-news dates."""
    quarters = {expected_quarter(date(year, month, 1))
                for year in range(2020, 2025) for month in (1, 4, 7, 10)}
    expected = {(s, q) for s in SYMBOLS for q in quarters}
    seen = set()
    for row in records:
        key = (row["symbol"], row["fiscal_quarter"])
        if key in seen or key not in expected:
            raise ValueError(f"Unexpected or repeated earnings record: {key}")
        seen.add(key)
        filing_day = date.fromisoformat(row["official_release_date"])
        if expected_quarter(filing_day) != key[1]:
            raise ValueError(f"Quarter and filing date disagree for {key}")
        published = datetime.fromisoformat(row["published_at"])
        available = datetime.fromisoformat(row["available_at"])
        if (published.utcoffset() is None or available.utcoffset() is None
                or available < published
                or available.isoformat() != (row["modified_at"] or row["published_at"])
                or published.astimezone(KST).date() < filing_day
                or available.astimezone(KST).year not in range(2020, 2025)):
            raise ValueError(f"Invalid article availability for {key}")
        if (row["article_url"] != "https://www.etoday.co.kr/news/view/" + row["article_id"]
                or not re.fullmatch(r"[0-9a-f]{64}", row["article_body_sha256"])):
            raise ValueError(f"Invalid publisher provenance for {key}")
        value, status = row["operating_profit_yoy_pct"], row["yoy_status"]
        if status not in {"article_rate", "not_reported", "loss_transition"}:
            raise ValueError(f"Unknown YoY status for {key}")
        if (value is None) != (status != "article_rate") or (value is not None and not math.isfinite(value)):
            raise ValueError(f"Invalid YoY change for {key}")
        if (row["loss_transition"] is not None) != (status == "loss_transition"):
            raise ValueError(f"Inconsistent loss transition for {key}")
    if seen != expected:
        raise ValueError(f"Unreviewed earnings quarters: {sorted(expected - seen)}")


def build_daily(sessions, records):
    validate_records(records)
    sessions = list(sessions)
    if not sessions or sessions != sorted(set(sessions)) or sessions[-1].year > 2024:
        raise ValueError("Expected a sorted development-period trading calendar")
    events = {}
    for record in records:
        stamp = record["available_at"]
        day = signal_day(stamp, sessions)
        if day is None or datetime.fromisoformat(stamp).astimezone(KST).date() < sessions[0]:
            raise ValueError("Trading calendar does not cover earnings release " + stamp)
        key = (day, record["symbol"])
        if key in events:
            raise ValueError(f"Two quarterly releases mapped to the same trading session: {key}")
        events[key] = record
    rows = []
    for day in sessions:
        if day.year < 2020:
            continue
        for symbol in SYMBOLS:
            rec = events.get((day, symbol))
            value = rec["operating_profit_yoy_pct"] if rec else None
            rows.append({"date": day.isoformat(), "symbol": symbol,
                         "earnings_release": int(rec is not None), "earnings_yoy_available": int(value is not None),
                         "earnings_op_yoy_pct": value if value is not None else 0})
    return rows


def verify_bodies(records):
    """Check the first article and all preceding decisions in each release-day title cohort."""
    validate_records(records)
    candidate_count = 0
    earlier_count = 0
    for row in records:
        archive_day = read(ARCHIVE / "days" / (row["official_release_date"] + ".json.gz"))
        candidates = [item["id"] for item in archive_day["matches"] if row["symbol"] in item["instruments"]]
        earlier = {}
        chosen = None
        for article_id in candidates:
            article = read(ARCHIVE / "bodies" / (article_id + ".json.gz"))
            if (article["id"] != article_id or row["symbol"] not in article["symbols"]
                    or article["available_at"] != (article["modified_at"] or article["published_at"])):
                raise ValueError("Invalid release-day candidate: " + article_id)
            if article_id == row["article_id"]:
                chosen = article
            elif (article["available_at"], int(article_id)) < (row["available_at"], int(row["article_id"])):
                earlier[article_id] = (article["available_at"], hashlib.sha256(article["body"].encode()).hexdigest())
        if (chosen is None or chosen["url"] != row["article_url"]
                or hashlib.sha256(chosen["body"].encode()).hexdigest() != row["article_body_sha256"]
                or any(chosen[k] != row[k] for k in ("published_at", "modified_at", "available_at"))):
            raise ValueError("Source article changed: " + row["article_id"])
        reviewed = {aid: (a["available_at"], a["body_sha256"]) for aid, a in row.get("earlier_reviews", {}).items()}
        if earlier != reviewed:
            raise ValueError(f"Review earlier candidates again: {row['symbol']} {row['fiscal_quarter']}")
        candidate_count += len(candidates)
        earlier_count += len(earlier)
    return candidate_count, earlier_count


if __name__ == "__main__":
    ledger = json.loads((ROOT / "research/earnings.json").read_text(encoding="utf-8"))
    total, earlier = verify_bodies(ledger)
    print(f"{len(ledger)} releases, {total} release-day articles, {earlier} earlier exclusions verified")
