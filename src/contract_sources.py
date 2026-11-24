"""Build the two development-period contract inputs from publicly documented event evidence."""
import csv
import json
from dataclasses import dataclass
from datetime import date, datetime

from src.bigkinds_contract_counts import RESEARCH, excluded_samsung_group, samsung_groups
from src.fetch_market import KST, MARKET, SYMBOLS, write_csv
from src.news_events import signal_day
from src.news_schema import CONTRACT, UNION_CONTRACT


@dataclass(frozen=True)
class Witness:
    article_id: str
    url: str
    available_at: str


@dataclass
class Contract:
    symbol: str
    event_id: str
    report_id: str
    event_type: str
    role: str
    etoday: Witness | None = None
    bigkinds: Witness | None = None


def read_json(filename):
    return json.loads((RESEARCH / filename).read_text(encoding="utf-8"))


def earliest(*witnesses):
    return min((w for w in witnesses if w), key=lambda w: (w.available_at, w.article_id), default=None)


def witness(article_id, url, *timestamps):
    if not article_id or not url:
        raise ValueError(f"Missing evidence for {article_id}")
    clocks = [datetime.fromisoformat(stamp) for stamp in timestamps if stamp]
    if not clocks or any(t.tzinfo is None for t in clocks):
        raise ValueError(f"Missing zoned publication time for {article_id}")
    known = max(clocks).astimezone(KST)
    if not 2020 <= known.year <= 2024:
        raise ValueError(f"Source outside development period: {article_id}")
    return Witness(article_id, url, known.isoformat())


def baseline_contracts():
    contracts = {}
    for row in read_json("contract_events.json"):
        kind = row["event_type"]
        if kind == "exclude":
            continue
        source = witness(row["id"], row["url"], row["published_at"], row["modified_at"])
        for event_id in (row["event_id"], *row.get("additional_event_ids", [])):
            key = row["symbol"], event_id
            if key not in contracts:
                contracts[key] = Contract(*key, row["report_id"], kind, row["role"])
            case = contracts[key]
            if (case.report_id, case.event_type, case.role) != (row["report_id"], kind, row["role"]):
                raise ValueError(f"Conflicting EToday contract identity: {key}")
            case.etoday = earliest(case.etoday, source)
    return contracts


def add_samsung(contracts):
    for event_id, group in samsung_groups(read_json("bigkinds_samsung.json")).items():
        key = "005930", event_id
        if excluded_samsung_group(group):
            contracts.pop(key, None)
            continue
        if key not in contracts:
            # The filing screen identifies suppliers to Samsung. Vodafone selected Samsung.
            role = "seller" if event_id == "VODAFONE-UK-20210614" else "buyer"
            contracts[key] = Contract(*key, event_id, "signed", role)
        case = contracts[key]
        for row in group:
            if item := row.get("etoday"):
                source = witness(item["id"], item["url"], item["published_at"], item["modified_at"])
                case.etoday = earliest(case.etoday, source)
            if item := row.get("member"):
                source = witness(item["id"], item["url"], item.get("published_at"), item.get("modified_at"),
                                 item.get("syndicated_at"), item["received_at"])
                if source.available_at != item["available_at"]:
                    raise ValueError(f"Inconsistent BigKinds edition time: {item['id']}")
                case.bigkinds = earliest(case.bigkinds, source)


def add_hynix(contracts):
    with (RESEARCH / "bigkinds_hynix.psv").open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f, delimiter="|"):
            if row["status"] != "confirmed":
                continue
            key = "000660", row["event_key"]
            if key not in contracts:
                # New cases in this procurement screen are orders from SK hynix to a named supplier.
                contracts[key] = Contract(*key, row["event_key"], "signed", "buyer")
            case = contracts[key]
            if row["etoday_id"]:
                source = witness(row["etoday_id"], row["etoday_url"], row["etoday_known_kst"])
                case.etoday = earliest(case.etoday, source)
            case.bigkinds = witness(row["bigkinds_id"], row["publisher_url"], row["publisher_published_kst"],
                                    row["publisher_modified_kst"], row["index_received_kst"])


def add_naver(contracts):
    kinds = {"contract": "signed", "award": "signed", "termination": "terminated"}
    for row in read_json("bigkinds_naver.json")["events"]:
        key = "035420", row["event_id"]
        kind = kinds[row["kind"]]
        if key not in contracts:
            contracts[key] = Contract(*key, row["report_id"], kind, row["role"])
        case = contracts[key]
        if (case.event_type, case.role) != (kind, row["role"]):
            raise ValueError(f"Conflicting NAVER event interpretation: {key}")
        if row["etoday_id"]:
            source = witness(row["etoday_id"], row["etoday_url"], row["etoday_available_at"])
            case.etoday = earliest(case.etoday, source)
        case.bigkinds = witness(row["bigkinds_id"], row["publisher_url"], row["published_at"],
                                row["modified_at"], row["sent_at"])


def source_contracts():
    contracts = baseline_contracts()
    add_samsung(contracts)
    add_hynix(contracts)
    add_naver(contracts)
    for case in contracts.values():
        if case.event_type not in {"signed", "terminated"} or not case.report_id or not (case.etoday or case.bigkinds):
            raise ValueError(f"Incomplete contract: {case.event_id}")
    return [contracts[key] for key in sorted(contracts)]


def daily_from_contracts(sessions, contracts):
    """At the first usable close count distinct contracts and distinct announcements for each arm."""
    daily = {(symbol, day.isoformat()): dict.fromkeys(CONTRACT + UNION_CONTRACT, 0)
             for day in sessions if 2020 <= day.year <= 2024 for symbol in SYMBOLS}
    reports = {}
    for case in contracts:
        for prefix, info in (("", case.etoday), ("union_", earliest(case.etoday, case.bigkinds))):
            if info is None:
                continue
            day = signal_day(info.available_at, sessions)
            if day is None or (case.symbol, day.isoformat()) not in daily:
                continue
            key = case.symbol, day.isoformat()
            daily[key][prefix + "contract_" + case.event_type] += 1
            group = case.symbol, prefix, case.report_id
            reports[group] = min(day, reports.get(group, day))
    for (symbol, prefix, _), day in reports.items():
        daily[symbol, day.isoformat()][prefix + "contract_reports"] += 1
    return daily


def build_source_daily(sessions, contracts=None):
    sessions = sorted(day for day in sessions if day.year <= 2024)
    development = [day for day in sessions if day.year >= 2020]
    if (len(development) != 1231 or len(sessions) != len(set(sessions))
            or development[0] != date(2020, 1, 2) or development[-1] != date(2024, 12, 30)):
        raise ValueError("The full 2020–2024 development exchange calendar is required")
    return daily_from_contracts(sessions, source_contracts() if contracts is None else contracts)


def event_records(contracts):
    for case in contracts:
        row = dict(symbol=case.symbol, event_id=case.event_id, report_id=case.report_id,
                   event_type=case.event_type, role=case.role)
        for name, info in (("etoday", case.etoday), ("bigkinds", case.bigkinds)):
            for attr in ("article_id", "url", "available_at"):
                row[name + "_" + attr] = getattr(info, attr) if info else ""
        yield row


def main():
    with (MARKET / "raw/KOSPI.csv").open(encoding="utf-8-sig", newline="") as f:
        sessions = [date.fromisoformat(r["date"]) for r in csv.DictReader(f) if r["date"] <= "2024-12-31"]
    contracts = source_contracts()
    daily = build_source_daily(sessions, contracts)
    rows = [dict(date=day, symbol=symbol, **fields) for (symbol, day), fields in daily.items()]
    events = list(event_records(contracts))
    write_csv(RESEARCH / "contract_source_events.psv", list(events[0]), events, delimiter="|")
    write_csv(RESEARCH / "contract_source_daily.csv", list(rows[0]), rows)
    print(f"{len(contracts)} confirmed contracts; {len(rows)} stock-sessions")


if __name__ == "__main__":
    main()
