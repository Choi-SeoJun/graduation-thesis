"""Align reviewed EToday corporate-contract stories to three stocks' market closes."""
import csv
import json
import re
from bisect import bisect_left, bisect_right
from collections import defaultdict
from datetime import date, datetime, time, timedelta

from src.fetch_market import KST, MARKET, ROOT, SYMBOLS, write_csv, write_json
from src.news_archive import ROOT as NEWS_ROOT

LATE_CLOSE = {date.fromisoformat(d) for d in
              ("2020-12-03", "2021-11-18", "2022-11-17", "2023-11-16", "2024-11-14")}
NEWS_COLUMNS = ("contract_signed", "contract_terminated", "contract_reports")


def eligible_headline(title):
    """Narrow, optional headline indicator retained to diagnose keyword omissions."""
    return bool(re.search("공급|판매|납품", title) and re.search("계약|수주|낙찰|해지|해약|파기", title))


def signal_day(stamp, sessions):
    """Date only means first session strictly after that calendar day."""
    if len(stamp) == 10:
        index = bisect_right(sessions, date.fromisoformat(stamp))
    else:
        point = datetime.fromisoformat(stamp)
        if point.tzinfo is None:
            raise ValueError(f"Missing timezone: {stamp}")
        local = point.astimezone(KST)
        day = local.date()
        end = time(16,30) if day in LATE_CLOSE else time(15,30)
        index = bisect_left(sessions, day) if local.time() <= end else bisect_right(sessions, day)
    return sessions[index] if index < len(sessions) else None


def build_daily(sessions, reviews, unresolved, complete_calendar_day):
    """Emit counts only after calendar coverage, timestamp and coding checks."""
    gaps = {(s,d) for s, dates in unresolved.items() for d in dates}
    new_events, reports = {}, {}
    for r in reviews:
        symbol = r["symbol"]
        first = signal_day(r["published_at"], sessions)
        known = signal_day(r["modified_at"] or r["published_at"], sessions)
        if r["modified_at"] and first != known and first is not None:
            # Original online version was not archived. Do not infer what was known then.
            gaps.add((symbol, first))
        if known is None or r["event_type"] == "exclude":
            continue
        if r["event_type"] not in ("signed","terminated") or not r["event_id"] or not r["report_id"]:
            raise ValueError(f"Invalid coding for {r['id']}")
        line = (symbol, r["report_id"])
        for identifier in (r["event_id"], *r.get("additional_event_ids", [])):
            event = (symbol, r["event_type"], identifier)
            new_events[event] = min(known, new_events.get(event, known))
        reports[line] = min(known, reports.get(line, known))
    counts = defaultdict(lambda: dict.fromkeys(NEWS_COLUMNS, 0))
    for (symbol, kind, _), observed in new_events.items():
        counts[(symbol,observed)]["contract_" + kind] += 1
    for (symbol,_), observed in reports.items():
        counts[(symbol,observed)]["contract_reports"] += 1
    rows = []
    for i, session in enumerate(sessions):
        if session.year < 2020:
            continue
        previous = sessions[i-1] if i else session - timedelta(days=1)
        # The preceding session's AFTER-close interval belongs to today's signal.
        complete = all(complete_calendar_day(previous + timedelta(days=n))
                       for n in range((session - previous).days + 1))
        for symbol in SYMBOLS:
            available = complete and (symbol,session) not in gaps
            fields = counts[(symbol,session)] if available else dict.fromkeys(NEWS_COLUMNS)
            rows.append({"date": session.isoformat(), "symbol": symbol,
                         "news_status": "complete" if available else "unknown", **fields})
    return rows


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def main():
    out = NEWS_ROOT / "development"
    coverage = read_json(out / "coverage.json")
    observed = read_json(out / "candidates.json")
    reviewed = read_json(ROOT / "research/etoday_events.json")
    with (MARKET / "raw/KOSPI.csv").open(encoding="utf-8") as src:
        sessions = sorted(date.fromisoformat(r["date"]) for r in csv.DictReader(src))
    lookup = {(r["id"],r["symbol"]):r for r in reviewed}
    if len(lookup) != len(reviewed):
        raise ValueError("Duplicate article-stock adjudication")
    gaps = defaultdict(set)
    used, missing = [], []
    narrow_ids = {a["id"] for a in observed if eligible_headline(a["title"])}
    for article in observed:
        for symbol in article["instruments"]:
            key = (article["id"], symbol)
            r = lookup.get(key)
            exact = article.get("published_at")
            modified = article.get("modified_at")
            if r and exact and r["published_at"] == exact and r["modified_at"] == modified:
                used.append(r)
                continue
            # Article without first-edition clock can affect the same or next session.
            # Re-running with added candidates does not turn their dates into zero.
            stamp = exact or article["archive_date"]
            lower = stamp[:10] + "T00:00:00+09:00"
            for t in ((stamp,) if exact else (lower, stamp)):
                d = signal_day(t, sessions)
                if d:
                    gaps[symbol].add(d)
            if modified:
                later = signal_day(modified, sessions)
                if later:
                    gaps[symbol].add(later)
            missing.append({"id": article["id"], "symbol": symbol})
    def complete(d):
        month = coverage["months"].get(d.isoformat()[:7])
        return month is not None and month["reconciled"]
    rows = build_daily(sessions, used, gaps, complete)
    write_csv(out / "news_daily.csv", list(rows[0]), rows)
    write_json(out / "coding_audit.json", {
        "candidate_articles": len(observed), "input_article_stock_pairs": sum(len(a["instruments"]) for a in observed),
        "narrow_title_articles": len(narrow_ids),
        "eligible_pairs_outside_narrow_titles": sum(r["event_type"] != "exclude" and r["id"] not in narrow_ids for r in used),
        "reviewed_pairs_used": len(used), "unusable_or_unreviewed_pairs": missing,
        "eligible_pairs": sum(r["event_type"] != "exclude" for r in used),
        "complete_signal_rows": sum(r["news_status"]=="complete" for r in rows),
        "unknown_signal_rows": sum(r["news_status"]=="unknown" for r in rows)})
    print(len(rows), "stock-sessions;", len(used), "reviewed article-stock pairs;",
          len(missing), "unusable or unreviewed")


if __name__ == "__main__":
    main()
