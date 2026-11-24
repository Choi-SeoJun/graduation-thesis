"""Reproduce the Samsung press pre-screen and verify the published case ledger.

Run from the repository root: python -m research.bigkinds_samsung [--members FILE].
Public output contains decisions and source IDs. The member export stays local.
"""
import argparse
import csv
import html
import json
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from src.news_events import signal_day

PROJECT = Path(__file__).resolve().parents[1]
LEDGER = PROJECT / "research/bigkinds_samsung.json"
KST = timezone(timedelta(hours=9))
TARGET = r"삼성\s*전자(?:\s*\(\s*(?:005930|주)\s*\)|\s*㈜|\s*주식회사)?"
LEGAL = re.compile(r'공급|수주|구매|납품|발주|판매|반도체.{0,5}장비|보안|도급|공사|위탁|용역|서비스|유지보수|장기.{0,3}공급|보험|관리|사용권|기술이전')
IRRELEVANT = re.compile(r'담보\s*제공|자사주|주식\s*신탁|자기주식|지분.{0,4}(?:매매|매각|양도)|계약연장\s*가능성|수주\s*기대')
CORP = r'[가-힣A-Za-z0-9·&]+'
PARTY = re.compile(rf'(?P<issuer>{CORP}?)(?:\([\w .-]+\))?(?:은|는|이|가)\s*(?:(?:지난|이날|이달|최근)\s*\d*일?\s*)?{TARGET}\s*(?:와|과|에|로부터)', re.I)
EXPLICIT = re.compile(rf'계약\s*상대(?:방|자)?\s*(?:은|는|[:：])?\s*{TARGET}(?=\s|이고|이며|로|와|과|,|\.|$)')
HEAD = re.compile(rf'^(?:\[[^\]]+\]\s*)?(?P<issuer>{CORP})[,，\s]+(?:은|는|이|가)?\s*{TARGET}\s*(?:와|과|에|로부터)', re.I)
AMT = re.compile(r'(?<![가-힣\d])(?:(\d[\d,]*(?:\.\d+)?)\s*조\s*)?(\d[\d,]*(?:\.\d+)?)\s*억(?:\s*(\d[\d,]*(?:\.\d+)?)\s*만)?|(?<![\d])([\d][\d,]*(?:\.\d+)?)\s*조\s*원')
DATE = re.compile(r'(?:오늘|이날|당일)|(\d{1,2})일(?:에|자)?\s*(?:공시|밝혔|밝히|발표)')

def normalize_text(text):
    text = html.unescape(text or "").replace("\u00a0", " ")
    return re.sub(r"\s+", " ", text).strip()


def has_amount(text):
    # The headlines contain stylized expressions such as '1조 340억원'.
    for match in AMT.finditer(text):
        if any(value and float(value.replace(",", "")) for value in match.groups()):
            return True
    return False


def find_issuer(title, excerpt):
    body_match = PARTY.search(excerpt)
    headline_match = HEAD.search(title)
    if body_match:
        return body_match["issuer"]
    if EXPLICIT.search(excerpt):
        announcement = re.search(r"(?:^|\s)([가-힣A-Za-z0-9&]+)\s*수주공시\s*[-–]", title)
        if announcement:
            return announcement[1]
        return headline_match["issuer"] if headline_match else None
    if headline_match and re.search(r"계약.{0,18}(?:체결|수주|공시)|(?:수주|체결).{0,15}공시", excerpt):
        return headline_match["issuer"]
    return None


NON_ISSUERS = {"삼성전자", "삼성", "관계자", "수주", "계약", "공시", "회사", "공급", "그룹", "기자", "주식회사"}
SUBSIDIARY = re.compile(
    r"전자(?:서비스|로지텍|판매|재팬)|삼성전자\s*(?:미국|중국|베트남|인도|태국|일본|현지|서안|"
    r"쑤저우|시안|화성법인|반도체유한공사|인니|헝가리)|삼성전자.{0,13}(?:유한공사|현지법인)"
)
CHANGED = re.compile(r"해지|해약|취소|해제|정정|변경|연장")


def screen(article):
    title = normalize_text(article["title"])
    excerpt = normalize_text(article["excerpt"])
    excerpt = re.sub(r"^\s*(?:\[[^\]]+\]|▲)+\s*", "", excerpt)
    joined = f"{title} {excerpt}"
    if not LEGAL.search(joined) or IRRELEVANT.search(title) or "공시" not in excerpt:
        return None
    issuer = find_issuer(title, excerpt)
    if not issuer or issuer in NON_ISSUERS or SUBSIDIARY.search(joined):
        return None
    if not has_amount(joined):
        return None

    day_number = int(article["day"][-2:])
    same_day = any(not d or int(d) == day_number for d in DATE.findall(excerpt))
    if f"{day_number:02d}일 " in excerpt[:20] and "수주공시" in excerpt:
        same_day = True
    return {"issuer": issuer, "same_day": same_day, "change_headline": bool(CHANGED.search(title))}


def prescreen(db_path, book):
    """Reconstruct all article-level and day/issuer candidate IDs from the exports."""
    alias = {"삼성에스디에스": "삼성SDS", "씨엔지하이테크": "씨앤지하이테크",
             "와이아이케어": "와이아이케이", "맥스느": "맥스트",
             "와이씨": "와이아이케이", "커뮤니케이션즈": "네이블커뮤니케이션즈"}
    pool = defaultdict(set)
    scanned = Counter()
    screened = Counter()
    with sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        sql = """SELECT * FROM articles WHERE outlet != '이투데이'
                 AND (title LIKE '%삼성전자%' OR excerpt LIKE '%삼성전자%')"""
        for article in db.execute(sql):
            year = article["day"][:4]
            scanned[year] += 1
            result = screen(article)
            if not result:
                continue
            screened[year] += 1
            if result["same_day"] and not result["change_headline"]:
                supplier = alias.get(result["issuer"], result["issuer"])
                pool[(article["day"], supplier)].add(article["id"])
        title_sql = """SELECT id, day FROM articles WHERE outlet != '이투데이'
                       AND title LIKE '%삼성전자%' AND title LIKE '%억%'
                       AND (title LIKE '%계약%' OR title LIKE '%수주%' OR title LIKE '%납품%')"""
        title_rows = db.execute(title_sql).fetchall()
        title_ids = {r["id"] for r in title_rows}
        assert len(title_ids) == 400 and len({r["day"] for r in title_rows}) == 183
        title_cases = [r for r in book["supplementary"] if r.get("title_witness_ids")]
        assert len(title_cases) == 23
        for case in title_cases:
            assert set(case["title_witness_ids"]) & title_ids

        for case in book["reference_anchors"] + book["contract_disclosures"] + book["supplementary"]:
            src = case["member"]
            if src:
                row = db.execute("SELECT outlet, day FROM articles WHERE id=?", (src["id"],)).fetchone()
                assert row and row["outlet"] == src["outlet"] != "이투데이"
    stored = {}
    for case in book["contract_disclosures"] + book["not_admitted"]:
        key = (case["day"].replace("-", ""), case.get("counterparty", case.get("company")))
        assert key not in stored
        stored[key] = set(case["member_ids"])
    assert pool == stored, "Export and public candidate groups differ"
    print("Screen by year (scanned / flagged)")
    for year, count in sorted(scanned.items()):
        print(year, count, screened[year])
    assert sum(scanned.values()) == 57910 and sum(screened.values()) == 377

def verify(book, market):
    with (PROJECT / "research/contract_events.json").open(encoding="utf8") as f:
        eligible = [r for r in json.load(f) if r["symbol"] == "005930" and r["event_type"] != "exclude"]
    keys = {key for r in eligible for key in [r["event_id"], *r.get("additional_event_ids", [])]}
    anchors = book["reference_anchors"]
    assert keys == {r["key"] for r in anchors} and len(keys) == len(anchors) == 79
    assert sum(r["member"] is not None for r in anchors) == 78
    supplement_keys = {r["key"] for r in book["supplementary"]}
    assert len(supplement_keys) == len(book["supplementary"]) == 25
    assert {"VODAFONE-UK-20210614", "D20240729900248"} <= supplement_keys
    cases = book["contract_disclosures"]
    assert len({r["key"] for r in cases}) == len(cases) == 172
    assert len(cases) + len(book["not_admitted"]) == 199
    assert sum(r["kind_receipt"] is not None for r in cases) == 164
    with (PROJECT / "research/bigkinds_samsung_filing_review.psv").open(encoding="utf8") as f:
        filings = {r["key"]: r for r in csv.DictReader(f, delimiter="|")}
    assert set(filings) == {r["key"] for r in cases}
    for case in cases:
        filing = filings[case["key"]]
        assert filing["adjudication"] == case["adjudication"]
        assert filing["primary_check"] == case["primary_check"]
        if case["adjudication"] != "retained":
            assert filing["filing_table_url"] and case["adjudication_note"]
    assert Counter(r["primary_check"] for r in cases) == {"dart_checked": 148, "kind_full_table": 24}
    assert Counter(r["adjudication"] for r in cases) == {
        "retained": 148, "amendment": 19, "mixed_entity_unallocated": 4, "counterparty_conflict": 1
    }
    calendar = None
    if market.is_file():
        with market.open(encoding="utf-8-sig") as f:
            calendar = [date.fromisoformat(r["date"]) for r in csv.DictReader(f)]
    for case in anchors + cases + book["supplementary"]:
        source = case["member"]
        for item in (case["etoday"], source):
            if not item:
                continue
            when = item["available_at"]
            if item is source:
                index = datetime.strptime(item["id"].split(".")[1][:14], "%Y%m%d%H%M%S")
                assert index.replace(tzinfo=KST).isoformat() == item["received_at"]
            if not when:
                assert item["close"] is None
                continue
            stamps = [item.get(k) for k in ("received_at", "published_at", "modified_at", "syndicated_at") if item.get(k)]
            assert item["published_at"] or (item.get("syndicated_at") and item.get("syndicated_url"))
            assert max(stamps) == when <= "2024-12-31T23:59:59+09:00"
            if calendar is not None:
                value = signal_day(when, calendar)
                assert (value.isoformat() if value else None) == item["close"]
    for case in cases:
        assert set(case["baseline_keys"]) <= keys
        assert case["member"]["id"] in case["member_ids"] + case.get("additional_member_ids", [])
        assert bool(case["etoday"]) == bool(case["etoday_ids"])
    print("Etoday reference, confirmed cross-reports, distinct formal observations:", len(anchors), 78, len(cases))
    print("Formal observation coverage:", dict(Counter(r["etoday_scope"] for r in cases)))
    print("Confirmed source clocks, anchors / formal:",
          sum(bool(r["member"] and r["member"]["close"]) for r in anchors),
          sum(bool(r["member"]["close"]) for r in cases))

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--members", type=Path, help="read-only local members.sqlite rebuilt from the 12 exports")
    parser.add_argument("--market", type=Path, default=PROJECT / "data/local/market/raw/KOSPI.csv")
    args = parser.parse_args()
    with LEDGER.open(encoding="utf8") as f:
        book = json.load(f)
    verify(book, args.market)
    if args.members:
        prescreen(args.members, book)

if __name__ == "__main__":
    main()
