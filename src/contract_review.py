"""Audit EToday's 2020–2024 company-title cohort using the publisher's article bodies."""
import argparse
import csv
import collections
import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from tempfile import TemporaryDirectory

from src.news_archive import HOST, KST, META, ROOT, cached, get, read, save
from src.news_archive import title_match
from src.fetch_market import write_json

PROJECT = Path(__file__).resolve().parents[1]
BODIES = ROOT / "bodies"
WORK = ROOT / "contracts"
START = "2020-01-01"
END = "2024-12-31"
LEGAL = re.compile(r"계약|수주|낙찰|해약|파기|발주|PPA\b|구매\s*확약|해지(?:했|한|를|된|\b)|(?:거래|공급).{0,16}해제|"
                   r"위탁\s*생산|생산.{0,8}맡|공급.{0,16}(?:선정|채택|따냈|맺|체결|중단|하기로|한다고|한[다다고])|"
                   r"(?:납품|조달).{0,16}(?:체결|따냈|맺|중단|하기로|한다고)|(?:중계|판|방송|사용|공급|저작)권.{0,8}(?:얻|취득|획득|확보)|"
                   r"(?:공급사|공급업체|보급\s*사업자|설계팀|설계\s*공모전|감사인).{0,30}(?:선정|당선)|"
                   r"(?:거래|공급|위탁|조달).{0,18}(?:종료|해제|중지|중단)", re.I)
CONFIRM = re.compile(r"공급\s*계약|판매\s*계약|구매\s*계약|공사\s*계약|거래\s*계약|전력.{0,6}계약|계약.{0,12}(?:체결|맺|해지|해약|파기|취소|따내)|(?:체결|맺).{0,8}계약|수주(?:했|한|했다고|에\s*성공|에\s*성공|를\s*따)|낙찰|수주처|거래.{0,6}종료")
BOUNDARY = re.compile(r"(?<=[다요음됨함임])[.?!]\s+|\n+")


class BodyText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.skip = 0
        self.parts = []
        self.found = False
        self.closed = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if not self.depth:
            if tag == "div" and attrs.get("itemprop") == "articleBody" and not self.found:
                self.depth = 1
                self.found = True
            return
        if tag == "div":
            self.depth += 1
        if tag in ("script", "style", "iframe"):
            self.skip += 1
        if tag in ("p", "br", "div", "li", "figcaption"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if not self.depth:
            return
        if tag in ("script", "style", "iframe") and self.skip:
            self.skip -= 1
        if tag in ("p", "div", "li"):
            self.parts.append("\n")
        if tag == "div":
            self.depth -= 1
            self.closed = self.depth == 0

    def handle_data(self, text):
        if self.depth and not self.skip:
            self.parts.append(text)

    def value(self):
        if not self.closed:
            raise ValueError("Publisher article body not found or incomplete")
        return "\n".join(" ".join(line.split()) for line in "".join(self.parts).splitlines() if line.strip())


def cohort():
    articles = {}
    for path in sorted((ROOT / "days").glob("*.json.gz")):
        day = path.name[:10]
        if not START <= day <= END:
            continue
        for a in read(path)["matches"]:
            item = {"id": a["id"], "archive_date": day, "symbols": a["instruments"],
                    "title": a["title"], "url": f"{HOST}/news/view/{a['id']}"}
            if a["id"] in articles and articles[a["id"]] != item:
                raise ValueError(f"Conflicting archive row {a['id']}")
            articles[a["id"]] = item
    if len(articles) != 8842:
        raise ValueError("Missing company-title archive days. Run src.news_archive first.")
    return articles


def parse_body(item, raw):
    text = raw.decode("utf-8")
    meta = dict(META.findall(text))
    def stamp(key):
        if not meta.get(key):
            return None
        parsed = datetime.fromisoformat(meta[key])
        if parsed.tzinfo is None:
            raise ValueError("Missing timestamp offset")
        return parsed.astimezone(KST).isoformat()
    published = stamp("article:published_time")
    modified = stamp("dd:modified_time")
    if not published or not START <= published[:10] <= END or (modified and modified < published):
        raise ValueError("Invalid development-period publication timestamp")
    body = ""
    if not modified or modified[:10] <= END:
        parser = BodyText()
        # Only retain the historical article. The tail includes current recommendations.
        parser.feed(text.partition("<!-- 기업 네임카드 -->")[0])
        parser.close()
        body = parser.value()
    return {**item, "published_at": published, "modified_at": modified,
            "available_at": modified or published, "body": body,
            "body_sha256": hashlib.sha256(body.encode()).hexdigest()}


def read_body(path):
    doc = read(path)
    if doc.get("body") and (doc.get("modified_at") or "")[:10] > END:
        doc.update(body="", body_sha256=hashlib.sha256(b"").hexdigest())
        save(path, doc)
    return doc


def get_body(item):
    path = BODIES / (item["id"] + ".json.gz")
    if path.exists():
        return read_body(path)
    return cached(path, lambda: parse_body(item, get(item["url"])))


def collect():
    articles = cohort()
    for path in BODIES.glob("*.json.gz"):
        read_body(path)
    missing = [item for article_id, item in articles.items() if not (BODIES / (article_id + ".json.gz")).exists()]
    errors = {}
    with ThreadPoolExecutor(max_workers=5) as pool:
        jobs = {pool.submit(get_body, a): a for a in missing}
        for job in as_completed(jobs):
            a = jobs[job]
            try:
                job.result()
            except (OSError, ValueError) as exc:
                errors[a["id"]] = str(exc)
    save(WORK / "fetch_errors.json", errors)
    print("Collected", len(articles)-len(errors), "of", len(articles), "errors", len(errors), flush=True)


def inspect_doc(body):
    sentences = BOUNDARY.split(body)
    broad = [s for s in sentences if LEGAL.search(s)]
    direct = [s for s in broad if CONFIRM.search(s)]
    return broad, direct


def queue():
    old = json.loads((PROJECT / "research/etoday_events.json").read_text(encoding="utf-8"))
    old_pair = {(r["id"], r["symbol"]) for r in old}
    records = []
    for p in sorted(BODIES.glob("*.gz")):
        doc = read_body(p)
        broad, direct = inspect_doc(doc["body"])
        if not broad:
            continue
        for symbol in doc["symbols"]:
            if (doc["id"], symbol) not in old_pair:
                records.append({"id": doc["id"], "symbol": symbol, "date": doc["archive_date"],
                                "title": doc["title"], "direct": direct, "broad": broad})
    save(WORK / "review_queue.json.gz", records)
    print("Body candidates beyond prior ledger", len(records), "strong lexical",sum(bool(r["direct"]) for r in records))
    return records


def curated():
    decisions = {}
    for path in sorted((PROJECT / "research").glob("contract_manual*.psv")):
        with path.open(encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream, delimiter="|"):
                key = (row["id"], row["symbol"])
                if key in decisions or row["event_type"] not in ("exclude","signed","terminated"):
                    raise ValueError(f"Invalid or duplicate decision {key} in {path.name}")
                if row["event_type"] != "exclude":
                    if not row["role"] or not row["report_id"] or not row["event_id"]:
                        raise ValueError(f"Incomplete positive decision {key}")
                    events = row["event_id"].split(",")
                    row["event_id"] = events[0]
                    if len(events) > 1:
                        row["additional_event_ids"] = events[1:]
                decisions[key] = {k:v for k,v in row.items() if v}
    return decisions


def xml_check():
    days, months = set(), set()
    for p in (ROOT / "months").glob("*.gz"):
        months.update(read(p)["ids"])
    for p in (ROOT / "days").glob("*.gz"):
        days.update(read(p)["ids"])
    extra = months - days
    matches = []
    for i in sorted(extra):
        article = read(ROOT / "articles" / f"{i}.json")
        if not article.get("outside_window") and title_match(i, article["headline"]):
            matches.append(i)
    return {"xml_only_ids":len(extra),"additional_company_titles":matches}


def digest(value):
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def decision_sources(research):
    sources = {"etoday_events.json": digest(read(research / "etoday_events.json"))}
    for path in sorted(research.glob("contract_manual*.psv")):
        with path.open(encoding="utf-8-sig", newline="") as stream:
            sources[path.name] = digest(list(csv.DictReader(stream, delimiter="|")))
    return sources


def verify_seal(prior, corpus, adjudication, refresh=False):
    if prior["corpus_sha256"] != corpus:
        raise ValueError("Publisher editions changed. Re-audit those editions in a separate snapshot.")
    if prior.get("adjudication") != adjudication and not refresh:
        raise ValueError("Frozen adjudication changed. Check decision shards, screen and ledger. "
                         "Use --refresh-adjudication only after intentional re-review.")


def check_freeze():
    research = PROJECT / "research"
    frozen = read(research / "contract_audit.json")
    seal = frozen["adjudication"]
    if decision_sources(research) != seal["decision_sources_sha256"]:
        raise ValueError("Committed sources and sealed inputs already disagree")
    # Simulate a lost positive decision in an isolated copy of the real input.
    target = "contract_manual.psv"
    original = (research / target).read_text(encoding="utf-8-sig")
    lines = [s for s in original.splitlines() if not s.startswith("2198302|035420|signed|")]
    if len(lines) != len(original.splitlines()) - 1:
        raise ValueError("Regression fixture 2198302 must have exactly one positive decision")
    with TemporaryDirectory() as directory:
        temporary = Path(directory)
        (temporary / "etoday_events.json").write_text("[]", encoding="utf-8")
        (temporary / target).write_text("\n".join(lines) + "\n", encoding="utf-8")
        sources = dict(seal["decision_sources_sha256"])
        sources[target] = decision_sources(temporary)[target]
    broken = dict(seal, decision_sources_sha256=sources)
    try:
        verify_seal(frozen, frozen["corpus_sha256"], broken)
    except ValueError as error:
        if "Frozen adjudication changed" not in str(error):
            raise
    else:
        raise AssertionError("Removing the positive decision 2198302 did not stop the pipeline")
    print("Verified: missing positive decision 2198302 stops normal finalization.")


def finish(refresh=False):
    research = PROJECT / "research"
    legacy = {(r["id"],r["symbol"]):r for r in json.loads((research/"etoday_events.json").read_text(encoding="utf-8"))}
    overrides = curated()
    xml = xml_check()
    if xml["additional_company_titles"]:
        raise ValueError("Found extra company titles in monthly XML. Add these to the body cohort.")
    reviews, gaps = [], []
    candidates = []
    counts = collections.Counter()
    fingerprints = hashlib.sha256()
    used = set()
    for i, item in sorted(cohort().items()):
        path = BODIES / f"{i}.json.gz"
        body = read_body(path) if path.is_file() else {}
        fingerprints.update((i + "|" + body.get("body_sha256","") + "|" + str(body.get("published_at"))
                             + "|" + str(body.get("modified_at")) + "\n").encode())
        counts["articles"] += 1
        counts["nonempty_bodies"] += bool(body.get("body", "").strip())
        sentences, _ = inspect_doc(body.get("body", ""))
        for symbol in item["symbols"]:
            key = (i, symbol)
            counts["stock_article_pairs"] += 1
            if not body.get("body", "").strip():
                late = (body.get("modified_at") or "")[:10] > END
                gaps.append({"id":i,"symbol":symbol,"archive_date":item["archive_date"],
                             "published_at":body.get("published_at"),"modified_at":body.get("modified_at"),
                             "reason":"revision_after_development" if late else ("empty_body" if body else "fetch_failed")})
                continue
            old = legacy.get(key)
            manual = overrides.get(key)
            explicit = manual or old
            if manual:
                used.add(key)
            if not (explicit or sentences or body.get("modified_at")):
                continue
            if sentences:
                counts["body_keyword_pairs"] += 1
                candidates.append(key)
            if sentences and not old:
                counts["body_keyword_pairs_outside_previous_titles"] += 1
            rec = {"id":i,"symbol":symbol,"url":item["url"],"published_at":body["published_at"],
                   "modified_at":body.get("modified_at"),"available_at":body["available_at"]}
            if explicit:
                if old and (old["modified_at"],old["published_at"]) != (rec["modified_at"],rec["published_at"]):
                    raise ValueError(f"Prior publisher times changed: {key}")
                details = ("role","event_type","event_id","additional_event_ids","report_id","note","verification_url")
                if old and old.get("verification_url"):
                    rec["verification_url"] = old["verification_url"]
                rec.update({k:explicit[k] for k in details if k in explicit})
                rec["review_basis"] = "body_adjudication" if manual else "prior_review"
            else:
                rec["event_type"] = "exclude"
                rec["review_basis"] = "body_screen" if sentences else "revised_article_without_cue"
                rec["note"] = ("본문 후보 문맥에서 해당 상장사의 새로운 공급·구매 확정을 확인하지 못함."
                               if sentences else "본문에 대상 계약 표현 없음. 수정 전 판본의 시점 공백 점검용.")
            reviews.append(rec)
    if set(overrides) != used:
        raise ValueError(f"Unused manual decisions {set(overrides)-used}")
    if set(legacy) - {(r["id"],r["symbol"]) for r in reviews+gaps}:
        raise ValueError("Lost previous ledger article")
    # Keep the public snapshots auditable. A changed online edition must be re-reviewed.
    stamp = fingerprints.hexdigest()
    prior_path = research / "contract_audit.json"
    seal = {"decision_sources_sha256": decision_sources(research),
            "screen_sha256": digest([(p.pattern, p.flags) for p in (LEGAL, CONFIRM, BOUNDARY)]),
            "body_candidate_pairs_sha256": digest(sorted(candidates)),
            "classified_pairs_sha256": digest(sorted((r["id"],r["symbol"]) for r in reviews)),
            "decisions_sha256": digest(reviews), "gaps_sha256": digest(gaps)}
    if prior_path.exists():
        verify_seal(read(prior_path), stamp, seal, refresh)
    metrics = {}
    for symbol in ("005930","000660","035420"):
        group = [r for r in reviews if r["symbol"]==symbol and r["event_type"]!="exclude"]
        def events(kind):
            return len({v for r in group if r["event_type"] == kind for v in [r["event_id"],*r.get("additional_event_ids",[])]})
        metrics[symbol] = {"signed":events("signed"),"terminated":events("terminated"),
                           "reports":len({r["report_id"] for r in group}),"included_articles":len(group)}
    write_json(research/"contract_events.json", reviews)
    write_json(research/"contract_gaps.json", gaps)
    counts["reviews_with_named_case_notes"] = sum(r["review_basis"] in {"body_adjudication", "prior_review"} for r in reviews)
    counts["screen_negatives_without_case_notes"] = sum(r["review_basis"]=="body_screen" for r in reviews)
    counts["no_cue_retained_only_for_revision"] = sum(r["review_basis"]=="revised_article_without_cue" for r in reviews)
    write_json(prior_path, {"first":START,"last":END,"counts":dict(counts),"xml_crosscheck":xml,
                            "adjudication":seal,
                            "articles_unusable":len({r["id"] for r in gaps}), "corpus_sha256":stamp,
                            "unique_counts_before_close_alignment":metrics})
    print(dict(counts),metrics,"unusable pairs",len(gaps))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("step", nargs="?", choices=("collect", "queue", "finish", "check-freeze"), default="collect")
    parser.add_argument("--refresh-adjudication", action="store_true",
                        help="Refresh the frozen case decisions after reviewing changes to sources or screening.")
    args = parser.parse_args()
    if args.refresh_adjudication and args.step != "finish":
        parser.error("--refresh-adjudication applies only to finish")
    if args.step == "collect":
        collect()
    elif args.step == "queue":
        queue()
    elif args.step == "check-freeze":
        check_freeze()
    else:
        finish(args.refresh_adjudication)
