"""Align reviewed EToday contracts and quarterly earnings to each stock's decision time."""
import csv
import json
import re
from bisect import bisect_left, bisect_right
from collections import defaultdict
from datetime import date, datetime, time, timedelta

from src.fetch_market import KST, MARKET, ROOT, SYMBOLS, write_csv, write_json
from src.news_archive import ROOT as NEWS_ROOT
from src.news_schema import CONTRACT, EARNINGS, earnings_values

LATE_CLOSE = {date.fromisoformat(d) for d in
              ("2020-12-03", "2021-11-18", "2022-11-17", "2023-11-16", "2024-11-14")}
NEWS_COLUMNS = CONTRACT


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
    """Count confirmed editions at their available close; flag archival uncertainty separately."""
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
            # A later edit can reveal archive uncertainty, never revoke an earlier decision.
            # Zero here means no confirmed signal in the preserved editions up to this close.
            fields = counts[(symbol,session)]
            rows.append({"date": session.isoformat(), "symbol": symbol,
                         "news_status": "complete" if available else "unknown", **fields})
    return rows


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def main():
    out = NEWS_ROOT / "development"
    coverage = read_json(out / "coverage.json")
    reviewed = read_json(ROOT / "research/contract_events.json")
    unresolved = read_json(ROOT / "research/contract_gaps.json")
    earnings = read_json(ROOT / "research/earnings.json")
    with (MARKET / "raw/KOSPI.csv").open(encoding="utf-8") as src:
        sessions = sorted(date.fromisoformat(r["date"]) for r in csv.DictReader(src))
    if len({(r["id"], r["symbol"]) for r in reviewed}) != len(reviewed):
        raise ValueError("Duplicate contract article-stock decision")
    gaps = defaultdict(set)
    for article in unresolved:
        first = article.get("published_at")
        day = first[:10] if first else article["archive_date"]
        stamps = [first] if first else [day + "T00:00:00+09:00", day]
        if article.get("modified_at"):
            stamps.append(article["modified_at"])
        for stamp in stamps:
            observed = signal_day(stamp, sessions)
            if observed:
                gaps[article["symbol"]].add(observed)

    def complete(d):
        month = coverage["months"].get(d.isoformat()[:7])
        return month is not None and month["reconciled"]

    rows = build_daily(sessions, reviewed, gaps, complete)
    from src.earnings import build_daily as build_earnings
    eps = {(r["symbol"], r["date"]): r for r in build_earnings(sessions, earnings)}
    if set(eps) != {(r["symbol"], r["date"]) for r in rows}:
        raise ValueError("Quarterly earnings and contracts use different development sessions")
    for row in rows:
        row.update(earnings_values(eps[row["symbol"], row["date"]]))
    write_csv(out / "news_daily.csv", list(rows[0]), rows)
    # This shareable, price-free snapshot fixes the earnings and retrospective
    # EToday archive coverage used by both contract-source experiments.
    fields = ("date", "symbol", "news_status", *EARNINGS)
    write_csv(ROOT / "research/etoday_daily_context.csv", fields,
              ({k: row[k] for k in fields} for row in rows))
    write_json(out / "coding_audit.json", {
        "reviewed_article_stock_pairs": len(reviewed),
        "unusable_or_unreviewed_pairs": unresolved,
        "complete_signal_rows": sum(r["news_status"] == "complete" for r in rows),
        "unknown_signal_rows": sum(r["news_status"] == "unknown" for r in rows),
        "earnings_releases": sum(r["earnings_release"] for r in rows)})
    print(len(rows), "stock-sessions;", len(earnings), "earnings records;", len(unresolved), "contract gaps")


if __name__ == "__main__":
    main()
