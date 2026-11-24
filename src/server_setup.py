"""Restore and verify frozen development inputs on a clean research server (Python 3.11)."""
import argparse
import hashlib
import json
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from src import exchange_archive, market_data, prepare
from src.fetch_market import END, FIELDS, MARKET, ROOT, START, SYMBOLS, day_at, naver_bars, write_csv, write_json


MANIFEST = ROOT / "docs/input-fingerprints.json"
AUDITED_ACTIONS = ROOT / "research/market_action_audit.json"


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def file_errors(root, expected):
    return [name for name, sha in expected.items()
            if not (root / name).is_file() or digest(root / name) != sha]


def verify(expected):
    errors = file_errors(ROOT, expected)
    if errors:
        raise ValueError("Missing or different from the published input snapshot: " + ", ".join(errors))
    from src.rights import load_sessions
    from src.server_train import read_panel
    for symbol in SYMBOLS:
        account_dates = [s.day.isoformat() for s in load_sessions(symbol)]
        panel_dates = [r["date"] for r in read_panel(symbol, 5)]
        if not panel_dates or panel_dates != account_dates:
            raise ValueError(f"{symbol}: prepared panel and execution calendar disagree")
        print(f"{symbol}: {len(panel_dates)} verified sessions ({panel_dates[0]} to {panel_dates[-1]})")
    print(f"Verified {len(expected)} input SHA-256 values.")


def restore_actions():
    audit = json.loads(AUDITED_ACTIONS.read_text(encoding="utf-8"))
    if (audit["start"], audit["end"]) != (START.isoformat(), END.isoformat()) or set(audit["symbols"]) != set(SYMBOLS):
        raise ValueError("Development corporate-action coverage changed")
    for symbol, evidence in audit["symbols"].items():
        if evidence["split_count"] != 0:
            raise ValueError(f"{symbol}: review split-adjustment evidence before restoring inputs")
        actions = []
        seen = set()
        for row in evidence["dividend_ex_dates"]:
            day = date.fromisoformat(row["date"])
            stamp = row["provider_timestamp"]
            if day_at(stamp) != day or not START <= day <= END or day in seen:
                raise ValueError(f"{symbol}: duplicate or invalid ex-dividend date {day}")
            seen.add(day)
            actions.append({"date": row["date"], "kind": "dividends", "detail": {"date": stamp}})
        write_json(MARKET / "actions" / f"{symbol}.json", actions)


def restore(expected):
    public = {name: sha for name, sha in expected.items() if not name.startswith("data/local/")}
    errors = file_errors(ROOT, public)
    if errors:
        raise ValueError("Restore these tracked research files from this Git revision: " + ", ".join(errors))

    kospi = MARKET / "raw/KOSPI.csv"
    kospi_key = kospi.relative_to(ROOT).as_posix()
    if file_errors(ROOT, {kospi_key: expected[kospi_key]}):
        kospi.parent.mkdir(parents=True, exist_ok=True)
        rows, _, _ = naver_bars("KOSPI")
        with TemporaryDirectory(dir=kospi.parent) as tmp:
            new_index = Path(tmp) / "KOSPI.csv"
            write_csv(new_index, FIELDS, rows)
            if digest(new_index) != expected[kospi_key]:
                raise ValueError("The index provider returned a different KOSPI snapshot. Use the frozen server-handoff.zip.")
            new_index.replace(kospi)
    stock_keys = [(MARKET / "exchange" / f"{s}.csv").relative_to(ROOT).as_posix() for s in SYMBOLS]
    stock_hashes = {key: expected[key] for key in stock_keys}
    if file_errors(ROOT, stock_hashes):
        print("Restoring the three equities from the fixed 2019-2024 Marcap archives.", flush=True)
        exchange_archive.extract()
        if file_errors(ROOT, stock_hashes):
            raise ValueError("Equity bars differ from the recorded snapshot. Use the frozen server-handoff.zip.")
    restore_actions()
    market_data.main()
    prepare.main()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-only", action="store_true", help="check local inputs without downloads or writes")
    args = parser.parse_args(argv)
    expected = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if not args.verify_only and file_errors(ROOT, expected):
        restore(expected)
    verify(expected)


if __name__ == "__main__":
    main()
