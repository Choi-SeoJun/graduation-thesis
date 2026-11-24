"""Recount confirmed contracts from the three published evidence ledgers."""
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path


RESEARCH = Path(__file__).resolve().parents[1] / "research"


def samsung_rows():
    data = json.loads((RESEARCH / "bigkinds_samsung.json").read_text(encoding="utf-8"))
    # The same case can enter by its original EToday event and independently by a filing.
    groups = defaultdict(list)
    for row in data["reference_anchors"] + data["supplementary"]:
        groups[row["key"]].append(row)
    for row in data["contract_disclosures"]:
        for key in row["baseline_keys"] or [row["key"]]:
            groups[key].append(row)
    for key, group in groups.items():
        # A filing can correct the event classification of a matched press article.
        if any(r.get("adjudication") in {
            "amendment", "counterparty_conflict", "mixed_entity_unallocated"
        } for r in group):
            continue
        other = [r["member"] for r in group if r.get("member")]
        etoday = [r["etoday"] for r in group if r.get("etoday")]
        if not other:
            continue
        other_close = min((a["close"] for a in other if a.get("close")), default=None)
        etoday_close = min((a["close"] for a in etoday if a.get("close")), default=None)
        ambiguous = any(r.get("etoday_scope", "").startswith("ambiguous") for r in group)
        matched = None if not etoday and ambiguous else bool(etoday)
        yield key, matched, other_close, etoday_close


def hynix_rows():
    with (RESEARCH / "bigkinds_hynix.psv").open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f, delimiter="|"):
            if row["status"] != "confirmed":
                continue
            matched = (None if row["etoday_scope"] == "etoday_counterparty_unspecified"
                       else bool(row["etoday_close"]))
            yield row["event_key"], matched, row["bigkinds_comparison_close"], row["etoday_close"]


def naver_rows():
    data = json.loads((RESEARCH / "bigkinds_naver.json").read_text(encoding="utf-8"))
    for row in data["events"]:
        matched = bool(row.get("etoday_id"))
        if row["etoday_status"].startswith("etoday_report_") and not matched:
            matched = None
        close = row.get("etoday_signal_close_at")
        yield row["event_id"], matched, row["bigkinds_signal_close_at"][:10], close[:10] if close else None


def count_cases(rows):
    counts = Counter()
    seen = set()
    for key, matched, other_close, etoday_close in rows:
        if not key or key in seen:
            raise ValueError(f"Empty or duplicated event: {key}")
        seen.add(key)
        counts["events"] += 1
        if not matched:
            counts["ambiguous" if matched is None else "not_found"] += 1
            continue
        counts["both"] += 1
        if not other_close or not etoday_close:
            counts["timing_unknown"] += 1
            continue
        counts["timed"] += 1
        if other_close < etoday_close:
            counts["earlier"] += 1
        elif other_close == etoday_close:
            counts["same"] += 1
        else:
            counts["later"] += 1
    return counts


if __name__ == "__main__":
    cols = "events both not_found ambiguous timed earlier same later timing_unknown".split()
    print("symbol", *cols, sep=" | ")
    for symbol, records in (("005930", samsung_rows), ("000660", hynix_rows), ("035420", naver_rows)):
        counts = count_cases(records())
        print(symbol, *(counts[k] for k in cols), sep=" | ")
