"""Pair the two contract sources on the same decisions, execution dates and costs."""
import csv
import json
from collections import defaultdict
from datetime import date
from statistics import median

from src.fetch_market import write_csv
from src.news_schema import CONTRACT, UNION_CONTRACT


SOURCE_PAIRS = (
    ("with-earnings", "contracts-earnings", "union-contracts-earnings"),
    ("contracts-only", "contracts", "union-contracts"),
)


def action(entry):
    if entry["terminal"] == "True" or entry["target"] == "":
        return ""
    desired = entry["target"] == "True"
    if desired == (int(entry["prior_shares"]) > 0):
        return "hold"
    return "buy" if desired else "sell"


def read_replay(path):
    with (path / "ledger.csv").open(encoding="utf-8", newline="") as stream:
        ledger = list(csv.DictReader(stream))
    return ledger, json.loads((path / "summary.json").read_text(encoding="utf-8"))


def compare_runs(year_dir, symbol, year, model, seed, by_day, sensitivity):
    """Read this fit's recorded ledgers so every reported difference has a dated witness."""
    summaries = []
    for contrast, etoday, combined in SOURCE_PAIRS:
        for bps in ((5, 0, 10) if sensitivity else (5,)):
            scenario = "" if bps == 5 else f"slippage-{bps}bp"
            left, base = read_replay(year_dir / etoday / model / scenario)
            right, extra = read_replay(year_dir / combined / model / scenario)
            if base["execution_slippage_bps"] != bps or extra["execution_slippage_bps"] != bps:
                raise ValueError("Source comparisons must use the same execution cost")
            daily = []
            for a, b in zip(left, right, strict=True):
                if any(a[k] != b[k] for k in ("day", "decision_day", "terminal")):
                    raise ValueError("Mismatched validation dates in source ledgers")
                if bool(a["target"]) != bool(b["target"]):
                    raise ValueError("Mismatched eligible decisions in source ledgers")
                row = by_day[date.fromisoformat(a["decision_day"])]
                et_action, union_action = action(a), action(b)
                et_fill = a["order"] if int(a["filled_qty"]) else "none"
                union_fill = b["order"] if int(b["filled_qty"]) else "none"
                differs = any(int(row[e]) != int(row[u]) for e, u in zip(CONTRACT, UNION_CONTRACT))
                daily.append({
                    "decision_close": a["decision_day"], "execution_open": a["day"],
                    "forced_liquidation_day": int(a["terminal"] == "True"), "eligible_policy_day": int(bool(et_action)),
                    **{k: row[k] for k in (*CONTRACT, *UNION_CONTRACT, "earnings_release")},
                    "source_inputs_differ": int(differs),
                    "etoday_target_long": a["target"], "union_target_long": b["target"],
                    "etoday_prior_shares": a["prior_shares"], "union_prior_shares": b["prior_shares"],
                    "etoday_decision": et_action, "union_decision": union_action,
                    "target_changed": int(a["target"] != b["target"]) if et_action else "",
                    "decision_changed": int(et_action != union_action) if et_action else "",
                    "etoday_order": a["order"], "union_order": b["order"],
                    "etoday_filled_qty": a["filled_qty"], "union_filled_qty": b["filled_qty"],
                    "filled_side_changed": int(et_fill != union_fill),
                })
            summary = {
                "symbol": symbol, "validation_year": year, "model": model, "seed": seed, "contrast": contrast,
                "etoday_condition": etoday, "union_condition": combined, "slippage_bps": bps,
                "execution_sessions": len(daily), "eligible_policy_days": sum(r["eligible_policy_day"] for r in daily),
                "source_input_changed_policy_days": sum(r["eligible_policy_day"] * r["source_inputs_differ"] for r in daily),
                "target_changed_days": sum(r["target_changed"] == 1 for r in daily),
                "buy_sell_hold_changed_days": sum(r["decision_changed"] == 1 for r in daily),
                "filled_side_changed_nonterminal_days": sum(r["filled_side_changed"] for r in daily if not r["forced_liquidation_day"]),
                "terminal_fill_side_changed": daily[-1]["filled_side_changed"],
                "etoday_book_return": base["book_return"], "union_book_return": extra["book_return"],
                "net_return_delta_pp": 100 * (extra["book_return"] - base["book_return"]),
                "etoday_book_drawdown": base["book_max_drawdown"], "union_book_drawdown": extra["book_max_drawdown"],
                "drawdown_delta_pp": 100 * (extra["book_max_drawdown"] - base["book_max_drawdown"]),
                "etoday_trades": base["trades"], "union_trades": extra["trades"],
                "trades_delta": extra["trades"] - base["trades"],
                "etoday_end_unvalued_rights": base["pending_end"], "union_end_unvalued_rights": extra["pending_end"],
            }
            destination = year_dir / "source-comparison" / contrast / model / scenario / "daily.csv"
            write_csv(destination, list(daily[0]), daily)
            summaries.append(summary)
    return summaries


def summarize_seeds(rows, seeds):
    groups = defaultdict(list)
    for row in rows:
        groups[row["symbol"], row["validation_year"], row["contrast"], row["slippage_bps"]].append(row)
    summary = []
    for (symbol, year, contrast, bps), group in groups.items():
        if sorted(r["seed"] for r in group) != sorted(seeds):
            raise ValueError("Missing or duplicated FQI seed in a source comparison")
        record = {"symbol": symbol, "validation_year": year, "contrast": contrast, "slippage_bps": bps, "seeds": len(group)}
        for key in ("net_return_delta_pp", "drawdown_delta_pp", "buy_sell_hold_changed_days",
                    "filled_side_changed_nonterminal_days"):
            values = [r[key] for r in group]
            record.update({f"{key}_median": median(values), f"{key}_min": min(values), f"{key}_max": max(values)})
        summary.append(record)
    return summary
