"""Reproduce the public 2024 H1 BIGKinds sample audit without using it for training."""
import argparse
import csv
import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from src.fetch_market import KST, MARKET, ROOT
from src.news_events import signal_day


DIGEST = "9f3df3c4ba418d4598debc8a4512d2f9f7851edd5b364b2356acd1ba467b34b8"
DEFAULT_INPUT = ROOT / "data/local/news/bigkinds/public_ai_20240630.csv"
CASES = ROOT / "research/bigkinds_cases.psv"


def audit(path, calendar):
    with path.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    if digest != DIGEST:
        raise ValueError("Public file differs from the version documented in docs/bigkinds-audit.md")
    with CASES.open(encoding="utf-8", newline="") as case_file:
        cases = list(csv.DictReader(case_file, delimiter="|"))
    earnings = json.loads((ROOT / "research/earnings.json").read_text(encoding="utf-8"))
    q1 = {r["symbol"]: r for r in earnings if r["fiscal_quarter"] == "2024Q1"}
    matches = {case["bigkinds_id"]: set() for case in cases}
    dates, media, identifiers = set(), set(), set()
    negative_candidate_ids = set()
    rows = 0
    with path.open(encoding="utf-8-sig", newline="") as source:
        for row in csv.DictReader(source):
            day, publisher = row["일자"], row["언론사"]
            if not "2024-01-01" <= day <= "2024-06-30":
                raise ValueError(f"Out-of-scope article date {day}")
            url = urlsplit(row["주소"])
            if url.hostname != "www.bigkinds.or.kr":
                raise ValueError(f"Unexpected article index host {url.hostname}")
            article_id, = parse_qs(url.query)["newsId"]
            dates.add(day)
            media.add(publisher)
            identifiers.add(article_id)
            rows += 1
            if article_id in matches:
                matches[article_id].add((day, publisher))
            if day in ("2024-02-21", "2024-02-22"):
                excerpt = row["제목"] + " " + row["본문"]
                if ("에코플랜트" in excerpt and "하이닉스" in excerpt
                        and any(term in excerpt for term in ("전력", "재생에너지", "PPA"))):
                    negative_candidate_ids.add(article_id)
    with calendar.open(encoding="utf-8-sig", newline="") as market:
        sessions = [date.fromisoformat(row["date"]) for row in csv.DictReader(market)
                    if row["date"] <= "2024-12-31"]
    if sessions != sorted(set(sessions)) or not dates:
        raise ValueError("Invalid development calendar or empty public dataset")
    print(f"{rows:,} rows | {len(identifiers):,} IDs | {len(media)} outlets | "
          f"{len(dates)} dates, {min(dates)} to {max(dates)}")
    print("event | BIGKinds outlet close | EToday close")
    earlier = same = later = 0
    for case in cases:
        day, article_id = case["day"], case["bigkinds_id"]
        if matches[article_id] != {(day, case["publisher"])}:
            raise ValueError(f"Unverified article ID, date or publisher: {article_id}")
        # KPF: positions after the period encode the outlet-to-index feed timestamp.
        received = datetime.strptime(article_id.split(".")[1][:14], "%Y%m%d%H%M%S")
        public_times = [datetime.fromisoformat(day + "T" + case[col])
                        for col in ("published_kst", "modified_kst") if case[col]]
        known = max(received, *public_times).replace(tzinfo=KST)
        etoday = datetime.fromisoformat(day + "T" + case["etoday_kst"]).replace(tzinfo=KST)
        if case["kind"] == "earnings":
            first_report = q1[case["symbol"]]
            if (first_report["article_id"] != case["etoday_id"] or
                    datetime.fromisoformat(first_report["available_at"]) != etoday):
                raise ValueError(f"Earnings article differs from the first-report ledger: {day}")
        index_close = signal_day(known.isoformat(), sessions)
        etoday_close = signal_day(etoday.isoformat(), sessions)
        if index_close is None or etoday_close is None:
            raise ValueError("Market calendar does not cover the matched cases")
        earlier += index_close < etoday_close
        same += index_close == etoday_close
        later += index_close > etoday_close
        print(f"{case['event']} | {index_close} | {etoday_close}")
    print(f"Compared article pairs: earlier={earlier}, same={same}, later={later}")
    print(f"SK ecoplant PPA title/excerpt candidates on Feb 21–22: {len(negative_candidate_ids)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--calendar", type=Path, default=MARKET / "raw/KOSPI.csv")
    args = parser.parse_args()
    audit(args.csv, args.calendar)
