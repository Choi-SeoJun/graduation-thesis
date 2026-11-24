"""Inventory the public EToday development-period archive. Keep titles and IDs local."""
import argparse
import calendar
import gzip
import hashlib
import html
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.request import Request, urlopen
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[1] / "data/local/news/etoday"
FIRST = date(2019, 12, 30)  # previous session close before the first 2020 decision
LAST = date(2024, 12, 31)
KST = timezone(timedelta(hours=9))
HOST = "https://www.etoday.co.kr"
STOCKS = {"005930": r"삼성전자|삼성\s*電子", "000660": r"하이닉스|SK\s*HYNIX", "035420": r"네이버|NAVER"}
CORE = r"계약|수주|해지|해약|파기|낙찰|발주|납품|공급"
EXTRA = r"협력|제휴|협약|협상|양해각서|MOU|회동"
LINKS = re.compile(r'<li\s+class=["\']t_reduce["\']\s*>\s*<a\s+href=["\']https?://www\.etoday\.co\.kr/news/view/(\d+)["\'][^>]*>(.*?)</a>', re.S)
META = re.compile(r'<meta\s+property=["\']([^"\']+)["\']\s+content=["\']([^"\']*)["\']', re.I)


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if path.suffix == ".gz":
        data = gzip.compress(data, mtime=0)
    with NamedTemporaryFile(dir=path.parent, suffix=".tmp", delete=False) as output:
        tmp = Path(output.name)
        output.write(data)
    try:
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def read(path):
    data = path.read_bytes()
    if path.suffix == ".gz":
        data = gzip.decompress(data)
    return json.loads(data)


def get(url):
    req = Request(url, headers={"User-Agent": "GraduationThesisResearch/1.0 (+https://github.com/Choi-SeoJun/graduation-thesis)"})
    for attempt in range(4):
        try:
            with urlopen(req, timeout=20) as resp:
                raw = resp.read()
            time.sleep(0.22)
            return raw
        except OSError:
            if attempt == 3:
                raise
            time.sleep(1.5 * (2 ** attempt))


def each_day():
    return [FIRST + timedelta(days=i) for i in range((LAST-FIRST).days+1)]


def cached(path, loader):
    if path.is_file():
        return read(path)
    value = loader()
    save(path, value)
    return value


def get_day(day):
    def load():
        url = f"{HOST}/sitemap/?year={day.year}&month={day.month}&day={day.day}"
        raw = get(url)
        text = raw.decode("utf-8")
        heading = f"<h1>{day.year}년 {day.month}월 {day.day}일</h1>"
        if heading not in text:
            raise ValueError(f"Incorrect date heading on {url}")
        pairs = LINKS.findall(text)
        if len(pairs) != text.count('class="t_reduce"'):
            raise ValueError(f"Partial headline parser match {url}")
        ids = []
        matches = []
        for article_id, markup in pairs:
            title = html.unescape(re.sub("<[^>]+>", "", markup)).strip()
            ids.append(article_id)
            match = title_match(article_id, title)
            if match:
                matches.append(match)
        return {"day": day.isoformat(), "url": url, "sha256": hashlib.sha256(raw).hexdigest(),
                "ids": list(dict.fromkeys(ids)), "listed_records": len(ids), "matches": matches}
    return cached(ROOT / "days" / f"{day}.json.gz", load)


def title_match(article_id, title):
    instruments = [s for s, pattern in STOCKS.items() if re.search(pattern, title, re.I)]
    if not instruments:
        return None
    level = "contract_candidate" if re.search(CORE, title, re.I) else (
        "context" if re.search(EXTRA, title, re.I) else "company")
    return {"id": article_id, "instruments": instruments, "level": level, "title": title}


def get_month(month):
    def load():
        url = f"{HOST}/rss/web_sitemap?q={month.replace('-', '')}"
        raw = get(url)
        root = ElementTree.fromstring(raw)
        ids = []
        for row in root.findall("{*}url"):
            loc = row.findtext("{*}loc") or ""
            match = re.fullmatch(r"https?://www.etoday.co.kr/news/view/(\d+)", loc)
            if not match:
                raise ValueError("Unrecognized archive URL: " + loc)
            ids.append(match.group(1))
        if not ids:
            raise ValueError("Empty archive: " + month)
        return {"month": month, "url": url, "ids": ids, "sha256": hashlib.sha256(raw).hexdigest()}
    return cached(ROOT / "months" / f"{month}.json.gz", load)


def get_article(article_id):
    def load():
        url = f"{HOST}/news/view/{article_id}"
        raw = get(url)
        meta = dict(META.findall(raw.decode("utf-8")))
        def stamp(key):
            value = meta.get(key)
            if not value:
                return None
            instant = datetime.fromisoformat(value)
            if instant.tzinfo is None:
                raise ValueError("Missing explicit publication offset " + article_id)
            return instant.astimezone(KST).isoformat()
        published = stamp("article:published_time")
        modified = stamp("dd:modified_time")
        if not published or (modified and modified < published):
            raise ValueError("Unusable publisher metadata " + article_id)
        if not FIRST.isoformat() <= published[:10] <= LAST.isoformat():
            return {"id": article_id, "url": url, "published_at": published, "modified_at": None,
                    "outside_window": True, "headline": ""}
        return {"id": article_id, "url": url, "published_at": published, "modified_at": modified,
                "headline": html.unescape(meta.get("og:title", ""))}
    return cached(ROOT / "articles" / f"{article_id}.json", load)


def batch(items, fn):
    rows, errors = {}, {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs = {pool.submit(fn, item): item for item in items}
        for future in as_completed(jobs):
            key = str(jobs[future])
            try:
                rows[key] = future.result()
            except (OSError, ValueError, ElementTree.ParseError) as error:
                errors[key] = str(error)
    return rows, errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--month", help="Pilot month within 2020–2024, YYYY-MM")
    args = parser.parse_args()
    days = each_day()
    if args.month:
        if not re.fullmatch(r"202[0-4]-(?:0[1-9]|1[0-2])", args.month):
            parser.error("Only 2020–2024 allowed")
        days = [d for d in days if d.isoformat()[:7] == args.month]
    definition = {"company_title_patterns": STOCKS, "contract_title_pattern": CORE,
                  "context_title_pattern": EXTRA, "window": [FIRST.isoformat(), LAST.isoformat()]}
    frozen = cached(ROOT / "selection.json", lambda: definition)
    if frozen != definition:
        raise ValueError("Selection changed. Use a fresh archive cache for the revised study.")
    months = sorted({d.isoformat()[:7] for d in days if d >= date(2020, 1, 1)})
    all_months, monthly_errors = batch(months, get_month)
    all_days, day_errors = batch(days, get_day)
    candidates = {}
    inventory = {}
    all_daily_ids = {i for record in all_days.values() for i in record["ids"]}
    all_monthly_ids = {i for record in all_months.values() for i in record["ids"]}
    # XML includes a small set of articles omitted by the HTML index (e.g. horoscopes).
    # For a pilot, cross-month XML extras can lie just outside the requested period.
    # The full run checks title and publication date for all extras.
    extras, extra_errors = batch(sorted(all_monthly_ids-all_daily_ids), get_article)
    for month in months:
        selected = [record for key, record in sorted(all_days.items()) if key[:7] == month]
        daily = {i for record in selected for i in record["ids"]}
        reference = set(all_months.get(month, {}).get("ids", []))
        # Month XML membership follows a different boundary than the dated archive.
        # A URL on another observed day is accounted for and reviewed on that day.
        missing = sorted(reference - all_daily_ids)
        extra = sorted(daily - all_monthly_ids)
        expected_days = calendar.monthrange(int(month[:4]), int(month[-2:]))[1]
        unresolved = [i for i in missing if i not in extras or
                      (not extras[i].get("outside_window") and not extras[i]["headline"])]
        inventory[month] = {"expected_days": expected_days, "read_days": len(selected), "day_ids": len(daily),
                            "month_ids": len(reference), "monthly_only_count": len(missing),
                            "unreviewed_monthly_ids": unresolved,
                            "found_on_another_calendar_month": len((reference-daily) & all_daily_ids),
                            "day_ids_outside_monthly_xml": len(extra),
                            "reconciled": bool(reference) and len(selected) == expected_days and not unresolved}
    for d, record in sorted(all_days.items()):
        for article in record["matches"]:
            if article["level"] == "contract_candidate":
                article_id = article["id"]
                if article_id in candidates:
                    candidates[article_id]["also_listed"].append(d)
                else:
                    candidates[article_id] = {"archive_date": d, "also_listed": [], **article}
    first, last = days[0].isoformat(), days[-1].isoformat()
    for article_id, meta in extras.items():
        observed_day = meta["published_at"][:10]
        if not first <= observed_day <= last:
            continue
        article = title_match(article_id, meta["headline"])
        if article and article["level"] == "contract_candidate":
            candidates[article_id] = {"archive_date": observed_day, "origin": "month_xml", "also_listed": [], **article}
    articles, article_errors = batch(sorted(candidates), get_article)
    for article_id, row in candidates.items():
        row.update(articles.get(article_id, {}))
    output = ROOT / (args.month or "development")
    development_ids = {article_id for d, record in all_days.items() if d >= "2020-01-01"
                       for article_id in record["ids"]}
    outside = sum(record.get("outside_window", False) for record in extras.values())
    save(output / "coverage.json", {"months": inventory, "day_errors": day_errors,
                                    "month_errors": monthly_errors, "extra_errors": extra_errors,
                                    "article_errors": article_errors,
                                    "unique_daily_ids": len(all_daily_ids),
                                    "development_daily_ids": len(development_ids),
                                    "unique_xml_ids": len(all_monthly_ids),
                                    "xml_extra_scanned": len(extras), "xml_extra_outside_window": outside,
                                    "unique_candidates": len(candidates)})
    save(output / "candidates.json", list(candidates.values()))
    print("Days",len(all_days),"/",len(days),"months",len(all_months),"candidates",len(candidates),
          "metadata",len(articles),"months reconciled",sum(m["reconciled"] for m in inventory.values()))


if __name__ == "__main__":
    main()
