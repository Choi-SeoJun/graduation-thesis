"""Validate official member XLSX exports and index their metadata locally."""
import argparse
import csv
import hashlib
import json
import re
import sqlite3
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from xml.etree import ElementTree

from src.fetch_market import KST, MARKET, ROOT
from src.news_events import signal_day


MANIFEST = ROOT / "research/bigkinds_exports.psv"
LOCAL = ROOT / "data/local/news/bigkinds"
COLS = {"A": "뉴스 식별자", "B": "일자", "C": "언론사", "E": "제목", "N": "기관",
        "O": "키워드", "Q": "본문", "R": "URL", "S": "분석제외 여부"}


def rows(path):
    with zipfile.ZipFile(path) as archive, archive.open("xl/worksheets/sheet1.xml") as sheet:
        # Official exports use inline strings and omit cells with no value.
        for _, row in ElementTree.iterparse(sheet, events=("end",)):
            if not row.tag.endswith("}row"):
                continue
            cells = {cell.get("r").rstrip("0123456789"): "".join(cell.itertext())
                     for cell in row}
            if row.get("r") == "1":
                if any(cells.get(key) != label for key, label in COLS.items()):
                    raise ValueError(f"Unexpected header in {path.name}")
            else:
                yield tuple(cells.get(key, "") for key in COLS)
            row.clear()


def ingest(conn, path, lo, hi, expected, recorded_digest):
    with path.open("rb") as f:
        digest = hashlib.file_digest(f, "sha256").hexdigest()
    if digest != recorded_digest:
        raise ValueError(f"{path.name}: expected a different SHA-256")
    cached = conn.execute("select sha256, count from exports where lo=? and hi=?", (lo, hi)).fetchone()
    if cached:
        if cached != (digest, expected):
            raise ValueError(f"Previously indexed export changed for {lo} to {hi}")
        return
    count = 0
    with conn:
        for article in rows(path):
            aid, day, outlet = article[:3]
            if not (re.fullmatch(r"\d{8}\.\d{17}", aid) and lo <= day <= hi and outlet):
                raise ValueError(f"Invalid record in {path.name}: {aid} {day}")
            conn.execute("insert into articles values (?,?,?,?,?,?,?,?,?)", article)
            count += 1
        if count != expected or count > 20000:
            raise ValueError(f"{path.name}: {count} rows, screen showed {expected}")
        conn.execute("insert into exports values (?,?,?,?)", (lo, hi, digest, count))


def verify_earnings(db):
    original = json.loads((ROOT / "research/earnings.json").read_text(encoding="utf-8"))
    ledger = {(r["symbol"], r["fiscal_quarter"]): r for r in original}
    with (MARKET / "raw/KOSPI.csv").open(encoding="utf-8-sig", newline="") as f:
        calendar = [date.fromisoformat(r["date"]) for r in csv.DictReader(f)
                    if r["date"] <= "2024-12-31"]
    seen = set()
    with (ROOT / "research/bigkinds_earnings.psv").open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f, delimiter="|"):
            key = row["symbol"], row["fiscal_quarter"]
            base = ledger[key]
            aid = row["bigkinds_id"]
            article = db.execute("select day, outlet from articles where id=?", (aid,)).fetchone()
            day = base["official_release_date"]
            transmitted = datetime.strptime(aid[9:23], "%Y%m%d%H%M%S").replace(tzinfo=KST)
            known = max(datetime.fromisoformat(row[col]) for col in ("published_at", "modified_at", "transmitted_at"))
            if (key in seen or article != (day.replace("-", ""), row["outlet"])
                    or row["outlet"] == "이투데이" or row["official_release_date"] != day
                    or row["etoday_url"] != base["article_url"] or row["filing_url"] != base["official_filing_url"]
                    or int(row["official_operating_profit_100m_krw"]) != base["official_operating_profit_100m_krw"]
                    or row["etoday_available_at"] != base["available_at"]
                    or transmitted != datetime.fromisoformat(row["transmitted_at"])
                    or str(signal_day(known.isoformat(), calendar)) != day
                    or str(signal_day(base["available_at"], calendar)) != day or row["common_signal_day"] != day):
                raise ValueError(f"Quarterly witness mismatch: {key}")
            seen.add(key)
    if seen != set(ledger) or len(seen) != 60:
        raise ValueError("Incomplete quarterly ledger")
    print("Matched quarterly decisions:", len(seen))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folder", type=Path, default=LOCAL / "exports")
    args = parser.parse_args()
    LOCAL.mkdir(parents=True, exist_ok=True)
    with MANIFEST.open(encoding="utf-8", newline="") as f, sqlite3.connect(LOCAL / "members.sqlite") as db:
        db.execute("pragma journal_mode=WAL")
        db.execute("create table if not exists articles (id text primary key, day text, outlet text, "
                   "title text, organizations text, keywords text, excerpt text, url text, exclusion text)")
        db.execute("create table if not exists exports (lo text, hi text, sha256 text, count integer, primary key(lo,hi))")
        next_day = date(2020, 1, 1)
        total = 0
        for rec in csv.DictReader(f, delimiter="|"):
            lo, hi = rec["start"], rec["end"]
            lo_day, hi_day = (datetime.strptime(d, "%Y%m%d").date() for d in (lo, hi))
            if lo_day != next_day or hi_day < lo_day:
                raise ValueError(f"Noncontiguous export period: {lo} to {hi}")
            next_day = hi_day + timedelta(days=1)
            path = args.folder / f"NewsResult_{lo}-{hi}.xlsx"
            expected = int(rec["screen_count"])
            ingest(db, path, lo, hi, expected, rec["sha256"])
            total += expected
        if next_day != date(2025, 1, 1) or db.execute("select count(*) from articles").fetchone()[0] != total:
            raise ValueError("Incomplete five-year collection")
        db.execute("create index if not exists by_day on articles(day)")
        print("Indexed unique article IDs:", total)
        verify_earnings(db)


if __name__ == "__main__":
    main()
